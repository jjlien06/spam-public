"""intern-list.com: daily US internship lists (Software Engineering, ML/AI) published as Airtable shared views.

Its Apply links go to jobright.ai, which shows the employer's link only after a login, so each row is resolved to the
employer's own posting instead: through the public job-board APIs of companies whose board the queue already knows
(Workday, Greenhouse, Ashby, Lever), accepting only an exact title match. Rows that do not resolve are kept in the
`discovered` table for the user to open by hand; SPAM never guesses a link.
"""
import hashlib
import json
import re
import sqlite3
import urllib.request
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from .queue import canonical, company_key, platform

LISTS = {  # intern-list.com section → Airtable shared view (the embed on the page)
    "intern-list-swe": ("app17F0kkWQZhC6HB", "shrOTtndhc6HSgnYb", "Software Engineering"),
    "intern-list-ai": ("appjSXAWiVF4d1HoZ", "shrf04yGbrK3IebAl", "Data Science, AI & Machine Learning"),
}
HEADERS = {"User-Agent": "Mozilla/5.0", "x-requested-with": "XMLHttpRequest", "x-time-zone": "America/New_York",
           "x-user-locale": "en", "Accept": "application/json"}


def _get(url, data=None, headers=None, timeout=12):
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, headers={**HEADERS, **(headers or {}),
                                                              **({"Content-Type": "application/json"} if body else {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode()


def read_view(app, share, fetch=_get):
    """Every row of an Airtable shared view as {column name: value}."""
    page = fetch(f"https://airtable.com/embed/{app}/{share}?viewControls=on")
    found = re.search(r'urlWithParams:\s*"([^"]+)"', page)
    if not found:
        raise RuntimeError("intern-list: Airtable view layout changed (no data URL on the embed page)")
    path = found.group(1).encode().decode("unicode_escape")
    table = json.loads(fetch("https://airtable.com" + path, headers={"x-airtable-application-id": app}))["data"]["table"]
    names = {c["id"]: c["name"] for c in table["columns"]}
    return [{names.get(k, k): v for k, v in r.get("cellValuesByColumnId", {}).items()} for r in table["rows"]]


def title_key(title):
    return " ".join(re.findall(r"[a-z0-9+#]+", (title or "").lower().replace("–", "-").replace("—", "-")))


def board_of(url):
    """(kind, board) for a posting URL on a board whose public API we can list, else None."""
    parts = urlsplit(url)
    host, segments = (parts.hostname or "").lower(), [s for s in parts.path.split("/") if s]
    if host.endswith(".myworkdayjobs.com") and segments:
        site = segments[1] if re.fullmatch(r"[a-z]{2}-[a-z]{2}", segments[0], re.I) and len(segments) > 1 else segments[0]
        return ("workday", f"{host}|{host.split('.')[0]}|{site}")
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and segments:
        return ("greenhouse", segments[0])
    if host == "jobs.ashbyhq.com" and segments:
        return ("ashby", segments[0])
    if host == "jobs.lever.co" and segments:
        return ("lever", segments[0])
    return None


def slugs(company):
    """Board names a company is likely published under: "Acme Robotics, Inc." -> acmerobotics, acme-robotics, acme."""
    words = company_key(company).replace("&", " ").split()
    if not words:
        return []
    return list(dict.fromkeys(["".join(words), "-".join(words), words[0]]))


def discover_boards(db, companies, fetch=_get):
    """Find public Greenhouse/Ashby/Lever boards for companies the queue has no board for. A board counts only if
    its API answers; results (including "none") are cached in the `boards` table."""
    db.execute("CREATE TABLE IF NOT EXISTS boards (company TEXT, kind TEXT, board TEXT, PRIMARY KEY (company, kind, board))")
    known = {r[0] for r in db.execute("SELECT DISTINCT company FROM boards")}
    todo = sorted({company_key(c) for c in companies} - known - {""})
    probes = [(c, kind, slug) for c in todo for slug in slugs(c) for kind in ("greenhouse", "ashby", "lever")]
    endpoints = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
                 "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}", "lever": "https://api.lever.co/v0/postings/{}?mode=json"}

    def probe(item):
        company, kind, slug = item
        try:
            data = json.loads(fetch(endpoints[kind].format(slug)))
        except (OSError, ValueError):
            return None
        jobs = data if isinstance(data, list) else data.get("jobs")
        return (company, kind, slug) if jobs else None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=24) as pool:
        found = [hit for hit in pool.map(probe, probes) if hit]
    with db:
        db.executemany("INSERT OR IGNORE INTO boards VALUES (?,?,?)", found)
        db.executemany("INSERT OR IGNORE INTO boards VALUES (?,'none','')", [(c,) for c in todo])
    index = {}
    for company, kind, board in db.execute("SELECT company, kind, board FROM boards WHERE kind != 'none'"):
        index.setdefault(company, set()).add((kind, board))
    return index


def board_index(rows):
    """company_key → the job boards its queued postings live on."""
    index = {}
    for row in rows:
        board = board_of(row["url"])
        if board:
            index.setdefault(company_key(row["company"]), set()).add(board)
    return index


def list_board(kind, board, fetch=_get):
    """(title, url) of the board's open postings (Workday: those matching 'intern')."""
    if kind == "greenhouse":
        data = json.loads(fetch(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"))
        # Link the Greenhouse-hosted form, not a company page that embeds it (absolute_url often is one).
        return [(j["title"], f"https://job-boards.greenhouse.io/{board}/jobs/{j['id']}") for j in data.get("jobs", [])]
    if kind == "ashby":
        data = json.loads(fetch(f"https://api.ashbyhq.com/posting-api/job-board/{board}"))
        return [(j["title"], j["jobUrl"]) for j in data.get("jobs", [])]
    if kind == "lever":
        return [(j["text"], j["hostedUrl"]) for j in json.loads(fetch(f"https://api.lever.co/v0/postings/{board}?mode=json"))]
    host, tenant, site = board.split("|")
    found, offset = [], 0
    while offset < 100:
        data = json.loads(fetch(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                                data={"limit": 20, "offset": offset, "searchText": "intern", "appliedFacets": {}}))
        postings = data.get("jobPostings", [])
        found += [(p["title"], f"https://{host}/en-US/{site}{p['externalPath']}") for p in postings]
        offset += 20
        if len(postings) < 20:
            break
    return found


def _safe_list(kind_board, fetch):
    try:
        return list_board(*kind_board, fetch)
    except (OSError, ValueError, KeyError):
        return []


def resolve(company, title, index, listing):
    """The employer's own URL for this role: exactly one open posting with the same title on a known board."""
    wanted = title_key(title)
    hits = {url for kind_board in sorted(index.get(company_key(company), ())) for t, url in listing(*kind_board)
            if title_key(t) == wanted}
    return hits.pop() if len(hits) == 1 else None


def _age(value):
    try:
        return (date.today() - date.fromisoformat(str(value)[:10])).days
    except ValueError:
        return None


def pull(queue, fetch=_get, lists=LISTS):
    """Add intern-list roles that resolve to an employer posting; record the rest as discovered."""
    rows = queue.rows()
    index = board_index(rows)
    have = {(company_key(r["company"]), title_key(r["role"])) for r in rows}
    boards, report = {}, {}

    def listing(kind, board):
        if (kind, board) not in boards:
            try:
                boards[(kind, board)] = list_board(kind, board, fetch)
            except (OSError, ValueError, KeyError):
                boards[(kind, board)] = []
        return boards[(kind, board)]

    for source, (app, share, category) in lists.items():
        try:
            items = read_view(app, share, fetch)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            report[source + "_error"] = str(error)[:200]
            continue
        # Companies with no board in the queue: look for their public board by name (cached).
        for company, found in discover_boards(queue.db, [str(i.get("Company") or "") for i in items], fetch).items():
            index.setdefault(company, set()).update(found)
        # Fetch every job board these rows need at once (in parallel): one listing per board.
        needed = {kb for item in items for kb in index.get(company_key(str(item.get("Company") or "")), ())}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=16) as pool:
            for kb, result in zip(sorted(needed), pool.map(lambda kb: _safe_list(kb, fetch), sorted(needed))):
                boards.setdefault(kb, result)
        added, unmatched = [], []
        for item in items:
            company, title = str(item.get("Company") or "").strip(), str(item.get("Position Title") or "").strip()
            if not company or not title or (company_key(company), title_key(title)) in have:
                continue
            url = resolve(company, title, index, listing)
            location = str(item.get("Location") or "")
            if url:
                try:
                    url = canonical(url)
                except ValueError:
                    url = None
            if url:
                added.append({"id": hashlib.sha256(url.encode()).hexdigest()[:20], "company": company, "role": title,
                              "location": location, "url": url, "platform": platform(url), "category": category,
                              "age_days": _age(item.get("Date")), "locations": location})
            else:
                apply = item.get("Apply")
                unmatched.append({"source": source, "company": company, "role": title, "location": location,
                                  "posted": str(item.get("Date") or "")[:10], "salary": str(item.get("Salary") or ""),
                                  "hire_time": str(item.get("Hire Time") or ""),
                                  "link": (apply or {}).get("url", "") if isinstance(apply, dict) else ""})
        report[source + "_rows"] = len(items)
        report[source + "_added"] = queue.add(added, source) if added else 0
        report[source + "_unmatched"] = record_discovered(queue.db, unmatched)
    return report


def record_discovered(db, items):
    """Keep roles with no resolvable employer link for the user; returns how many are new."""
    db.execute("""CREATE TABLE IF NOT EXISTS discovered (key TEXT PRIMARY KEY, source TEXT, company TEXT, role TEXT,
        location TEXT, posted TEXT, salary TEXT, hire_time TEXT, link TEXT, status TEXT DEFAULT 'new', seen TEXT)""")
    new = 0
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with db:
        for item in items:
            key = f"{company_key(item['company'])}|{title_key(item['role'])}"
            cursor = db.execute("INSERT OR IGNORE INTO discovered (key, source, company, role, location, posted, salary, hire_time,"
                                " link, seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (key, item["source"], item["company"], item["role"], item["location"], item["posted"],
                                 item["salary"], item["hire_time"], item["link"], now))
            new += cursor.rowcount
    return new


def discovered(db, status="new", limit=300):
    try:
        return [dict(zip(("key", "source", "company", "role", "location", "posted", "salary", "hire_time", "link", "status"), r))
                for r in db.execute("SELECT key, source, company, role, location, posted, salary, hire_time, link, status"
                                    " FROM discovered WHERE status=? ORDER BY posted DESC LIMIT ?", (status, limit))]
    except sqlite3.OperationalError:
        return []


def careers_search(company, role):
    return "https://www.google.com/search?q=" + quote(f"{company} {role} careers apply")
