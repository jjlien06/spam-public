import hashlib
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup

SOURCE = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md"


def canonical(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("Expected an HTTPS application URL")
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_") and k != "ref"]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


TRACKING = re.compile(r"^(utm_.*|ref|gh_src|src|source|nl|fr|mobile|needsredirect|lastselectedfacet)$", re.I)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def job_key(url):
    """One identity per job across the URL shapes Simplify and sites use."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = dict(parse_qsl(parts.query))
    if query.get("gh_jid"):
        return "greenhouse:" + query["gh_jid"]
    if host.endswith("greenhouse.io") and (match := re.search(r"/jobs/(\d+)", path)):
        return "greenhouse:" + match.group(1)
    if match := UUID.search(path):
        return "uuid:" + match.group().lower()
    if platform(url) == "workday" and "/job/" in path:
        tenant = host.split(".")[0]
        if host.endswith("myworkdaysite.com") and (match := re.search(r"/recruiting/([^/]+)", path)):
            tenant = match.group(1).lower()
        slug = re.sub(r"/(apply|applyManually|autofillWithResume)(/.*)?$", "", path).split("/")[-1]
        # A reposted requisition gets a "-1" suffix (R-100759 → R-100759-1); same job.
        requisition = slug.rsplit("_", 1)[-1].lower()
        base = re.sub(r"-\d{1,2}$", "", requisition)
        if len(re.sub(r"\D", "", base)) >= 4:  # only a real requisition number keeps its identity
            requisition = base
        return "workday:" + tenant + ":" + requisition
    numbers = [segment for segment in path.split("/") if re.fullmatch(r"\d{4,}", segment)]
    if numbers:
        return host + ":" + numbers[-1]
    kept = sorted((k, v) for k, v in query.items() if not TRACKING.match(k))
    return host + path.lower() + ("?" + urlencode(kept) if kept else "")


def platform(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    for name, domains in {"workday": ("myworkdayjobs.com", "myworkdaysite.com"),
                          "ashby": ("ashbyhq.com",), "greenhouse": ("greenhouse.io",),
                          "lever": ("lever.co",)}.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    # A company careers page embedding a Greenhouse board (Jump, Coinbase, Databricks).
    if dict(parse_qsl(parts.query)).get("gh_jid"):
        return "greenhouse"
    from .oracle import is_oracle
    if is_oracle(url):
        return "oracle"
    from .icims import is_icims
    if is_icims(url):
        return "icims"
    return "generic"


def greenhouse_board_url(url, company, fetch):
    """The hosted Greenhouse form for a company page that embeds one (?gh_jid=), or None.
    The board token is not in the URL: try the company name and the site's name against
    Greenhouse's public board API (fetch(url) -> True when it answers 200)."""
    parts = urlsplit(url)
    if (parts.hostname or "").endswith("greenhouse.io"):
        return None
    job_id = dict(parse_qsl(parts.query)).get("gh_jid")
    if not job_id:
        return None
    site = (parts.hostname or "").removeprefix("www.").split(".")[0]
    words = re.sub(r"[^a-z0-9 ]", "", company.lower()).split()
    candidates = dict.fromkeys(c for c in ("".join(words), site, words[0] if words else "",
                                           "".join(words[:2])) if c)
    for board in candidates:
        if fetch(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"):
            return f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    return None


MARKERS = ("🔥", "🎓", "🇺🇸", "🛂")
SECTION = re.compile(r"^## \W*(.+?) Internship Roles\s*$", re.MULTILINE)


def age_days(text):
    match = re.fullmatch(r"(\d+)(d|mo)", text.strip())
    if not match:
        return None
    return int(match.group(1)) * (30 if match.group(2) == "mo" else 1)


def clean(text):
    for marker in MARKERS:
        text = text.replace(marker, "")
    return " ".join(text.split())


def parse_list(html):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        heading = table.find_previous(string=SECTION)
        sections = SECTION.findall(str(heading)) if heading else []
        category = sections[-1] if sections else ""
        company = ""
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4:
                continue
            value = cells[0].get_text(" ", strip=True)
            if value != "↳":
                company = clean(value)
            link = next((a for a in cells[3].find_all("a", href=True)
                         if a.find("img", alt="Apply")), None)
            if not link or "🔒" in cells[3].get_text():
                continue
            try:
                url = canonical(link["href"])
            except ValueError:
                continue
            marked = cells[0].get_text() + cells[1].get_text()
            # "N locations" is a <details> summary line, not a place.
            locations = [text.strip() for text in cells[2].get_text("\n").split("\n")
                         if text.strip() and not re.fullmatch(r"\d+ locations", text.strip())]
            yield {"id": hashlib.sha256(url.encode()).hexdigest()[:20], "company": company,
                   "role": clean(cells[1].get_text(" ", strip=True)),
                   "location": cells[2].get_text(" ", strip=True), "url": url,
                   "platform": platform(url), "category": category,
                   "flags": "".join(m for m in MARKERS if m in marked),
                   "age_days": age_days(cells[4].get_text()) if len(cells) > 4 else None,
                   "locations": "; ".join(locations)}


class Queue:
    COLUMNS = {"category": "TEXT DEFAULT ''", "flags": "TEXT DEFAULT ''", "age_days": "INTEGER",
               "locations": "TEXT DEFAULT ''", "listed": "INTEGER DEFAULT 1",
               "source": "TEXT DEFAULT 'simplify'", "job_key": "TEXT DEFAULT ''",
               "fit_score": "REAL", "fit_legitimacy": "TEXT DEFAULT ''", "fit_report": "TEXT DEFAULT ''"}

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, company TEXT, role TEXT, location TEXT, url TEXT UNIQUE, platform TEXT, status TEXT DEFAULT 'queued', note TEXT DEFAULT '')")
        existing = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)")}
        with self.db:
            for name, kind in self.COLUMNS.items():
                if name not in existing:
                    self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
            for row in self.db.execute("SELECT id, url FROM jobs WHERE job_key=''").fetchall():
                self.db.execute("UPDATE jobs SET job_key=? WHERE id=?", (job_key(row["url"]), row["id"]))
            self.db.execute("CREATE TABLE IF NOT EXISTS tracked (job_key TEXT PRIMARY KEY, status TEXT, company TEXT,"
                            " title TEXT, url TEXT, location TEXT, applied_date TEXT, job_type TEXT)")
            # Every status change, from any code path or process, with its time: the
            # record of when each application was submitted and on what evidence.
            self.db.execute("CREATE TABLE IF NOT EXISTS status_log (job_id TEXT, old_status TEXT, new_status TEXT,"
                            " note TEXT, at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')))")
            self.db.execute("CREATE TRIGGER IF NOT EXISTS log_status_change AFTER UPDATE OF status, note ON jobs"
                            " WHEN OLD.status IS NOT NEW.status OR OLD.note IS NOT NEW.note BEGIN"
                            " INSERT INTO status_log(job_id, old_status, new_status, note) VALUES (NEW.id, OLD.status, NEW.status, NEW.note);"
                            " END")

    def ingest(self, jobs):
        """Replace the current listing: new rows are added, known rows refresh
        their listing details, and rows absent from this list are delisted.
        Status and notes are never changed here."""
        jobs = [{"category": "", "flags": "", "age_days": None, "locations": "", **job, "job_key": job_key(job["url"])}
                for job in jobs]
        before = self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        with self.db:
            self.db.execute("UPDATE jobs SET listed=0 WHERE source='simplify'")
            self.db.executemany("INSERT OR IGNORE INTO jobs(id,company,role,location,url,platform,job_key) VALUES (:id,:company,:role,:location,:url,:platform,:job_key)", jobs)
            self.db.executemany("UPDATE jobs SET company=:company, role=:role, location=:location, category=:category, flags=:flags,"
                                " age_days=:age_days, locations=:locations, listed=1, source='simplify' WHERE id=:id", jobs)
        return self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] - before

    def add(self, jobs, source):
        """Add postings from another list (speedyapply, intern-list) without touching
        the Simplify listing: a posting whose job_key is already queued is left alone."""
        known = {row[0] for row in self.db.execute("SELECT job_key FROM jobs")}
        fresh = []
        for job in jobs:
            job = {"category": "", "flags": "", "age_days": None, "locations": "", **job, "job_key": job_key(job["url"])}
            if job["job_key"] not in known:
                known.add(job["job_key"])
                fresh.append({**job, "source": source})
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO jobs(id,company,role,location,url,platform,job_key,category,flags,age_days,"
                                "locations,source) VALUES (:id,:company,:role,:location,:url,:platform,:job_key,:category,:flags,"
                                ":age_days,:locations,:source)", fresh)
        return len(fresh)

    def resolve_platforms(self, fetch):
        """Reclassify rows whose platform() changed (Lever, embedded Greenhouse) and point
        embedded Greenhouse jobs at their hosted board form; returns rows changed."""
        changed = 0
        with self.db:
            for row in self.db.execute("SELECT id, company, url, platform FROM jobs").fetchall():
                url = row["url"]
                if platform(url) == "greenhouse" and not (urlsplit(url).hostname or "").endswith("greenhouse.io"):
                    board = greenhouse_board_url(url, row["company"] or "", fetch)
                    if board and not self.db.execute("SELECT 1 FROM jobs WHERE url=?", (board,)).fetchone():
                        url = board
                    else:
                        continue  # no hosted form found: leave it as a company-site job
                if url != row["url"] or platform(url) != row["platform"]:
                    self.db.execute("UPDATE jobs SET url=?, platform=? WHERE id=?", (url, platform(url), row["id"]))
                    changed += 1
        return changed

    def rows(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY rowid")]

    def mark(self, job_id, status, note=""):
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,note=? WHERE id=?", (status, note, job_id))


_SUFFIX = {"inc", "llc", "ltd", "plc", "corp", "corporation", "co", "company", "software", "technologies",
           "technology", "holdings", "systems"}


def company_key(name):
    """One key per employer across the lists' spellings ("Veemo" / "Veemo Software", "XQT Corporation")."""
    words = re.findall(r"[a-z0-9&]+", (name or "").lower())
    while len(words) > 1 and words[-1] in _SUFFIX:
        words.pop()
    return " ".join(words)
