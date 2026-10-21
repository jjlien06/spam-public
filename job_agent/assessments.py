"""Assessment tracker: online assessments, coding challenges, take-homes, game-based tests (pymetrics,
Arctic Shores...) and recorded video interviews, pulled from Gmail with their due dates.

Each one is a card on the console's Assessments board (To do / In progress / Done / Expired). Gmail is
read the same way as security codes (gmail.py); a card's status is the user's to move, except that a
"thanks for completing" email marks it done and a passed due date shows it as expired.
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import gmail


def _owner():
    """The user's own name words and email/school domains, from their profile: kept out of company names
    and titles, and their school's own mail (courses, training) is not an assessment."""
    from . import paths
    for path in (paths.profile(),):
        if path.exists():
            try:
                facts = json.loads(path.read_text()).get("facts", {})
            except ValueError:
                return set(), set()
            name = facts.get("name") or ""
            name = " ".join(name.values()) if isinstance(name, dict) else str(name)
            # Only the school's own domain: a personal address (gmail.com) says nothing about who sent a test.
            domains = {str(e.get("school_domain")).lower() for e in facts.get("education", []) if isinstance(e, dict) and e.get("school_domain")}
            return set(re.findall(r"[a-z]+", name.lower())), domains
    return set(), set()
from .queue import company_key

QUERY = ('newer_than:{days}d -in:sent -from:swelist.com -from:joinhandshake.com (hackerrank OR codesignal OR codility '
         'OR hirevue OR karat OR pymetrics OR "arctic shores" OR cappfinity OR shl OR imocha OR hackerearth OR byteboard '
         'OR "online assessment" OR "coding assessment" OR "technical assessment" OR "coding challenge" OR "take home" '
         'OR "take-home" OR "online games" OR "video interview" OR "digital interview" OR "game-based" OR "assessment")')

PLATFORMS = [("hackerrank", "HackerRank"), ("codesignal", "CodeSignal"), ("codility", "Codility"), ("hirevue", "HireVue"),
             ("karat", "Karat"), ("pymetrics", "pymetrics"), ("arcticshores", "Arctic Shores"), ("cappfinity", "Cappfinity"),
             ("shl", "SHL"), ("imocha", "iMocha"), ("hackerearth", "HackerEarth"), ("byteboard", "Byteboard"),
             ("sparkhire", "Spark Hire"), ("modernhire", "Modern Hire"), ("tradermath", "Tradermath")]
NOT_AN_ASSESSMENT = re.compile(r"verify your \w+ account|call for graders|newsletter|prep session|\bwebinar\b|assigned training|"
                               r"thank you for (?:applying|your application)|application (?:received|confirmation)|"
                               r"confirmation on your application|currently reviewing your application|"
                               r"your application to .* is in", re.I)
DONE = re.compile(r"thanks? (?:you )?for (?:taking|completing)|assessment completed|submission confirmation|"
                  r"you have completed|successfully completed|we(?:'ve| have) received your (?:submission|responses)", re.I)
INVITE = re.compile(r"invit|next steps?|complete (?:the|your|our|an|this)\b|coding challenge|take[- ]home|online games|"
                    r"assessment|video interview|reminder|expires? in|still pending|waiting for your", re.I)
TEST_LINK = re.compile(r"https://(?:[\w-]+\.)?(?:hackerrank\.com/test|app\.codesignal\.com/(?:pre-screen|test|assessment)|"
                       r"app\.codility\.com/test|[\w.-]*hirevue\.com/|[\w.-]*pymetrics\.(?:com|ai)/|[\w.-]*arcticshores\.com/|"
                       r"assessment-portal\.[\w.-]+/|[\w.-]*karat\.(?:io|com)/)[^\s)>\"']+", re.I)
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
EXPLICIT = re.compile(r"(?:by|before|until|deadline[^.]{0,40}?(?:is|:)|expires? on|due(?: date)?:?|complete[^.]{0,30}? by)\s+"
                      r"(?:\w+day,?\s+)?((?:" + MONTHS + r")[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|\d{1,2}/\d{1,2}/\d{2,4})", re.I)
RELATIVE = re.compile(r"expire[sd]?\s+in\s+(\d+)\s+days?|(?:open|valid|available|active)\s+for\s+(\d+)\s+days|"
                      r"expire\s+(\d+)\s+days\s+from|within\s+(\d+)\s+days", re.I)
STATUSES = ("todo", "in_progress", "done", "expired", "skipped")


def db(state):
    conn = sqlite3.connect(Path(state) / "jobs.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS assessments (
        key TEXT PRIMARY KEY, company TEXT, title TEXT, kind TEXT, platform TEXT, status TEXT DEFAULT 'todo',
        due TEXT, link TEXT, email_url TEXT, thread TEXT, received TEXT, subject TEXT, note TEXT,
        manual INTEGER DEFAULT 0, updated TEXT)""")
    if "sender" not in [r[1] for r in conn.execute("PRAGMA table_info(assessments)")]:
        conn.execute("ALTER TABLE assessments ADD COLUMN sender TEXT")
    return conn


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def platform_of(sender, text):
    blob = (sender + " " + text).lower().replace(" ", "")
    return next((name for key, name in PLATFORMS if key in blob), "")


def kind_of(platform, text):
    if platform in ("pymetrics", "Arctic Shores", "Cappfinity") or re.search(r"\bgames?\b|game-based|gamified", text, re.I):
        return "Game"
    if platform in ("HireVue", "Spark Hire", "Modern Hire") or re.search(r"video interview|digital interview|recorded interview", text, re.I):
        return "Video"
    if re.search(r"take[- ]home", text, re.I):
        return "Take-home"
    if re.search(r"\binterview invitation|invit\w* (?:you )?to (?:an? )?interview|schedule (?:an|your) interview", text, re.I):
        return "Interview"
    if re.search(r"screening process|phone screen", text, re.I):
        return "Screening"
    return "Coding" if platform in ("HackerRank", "CodeSignal", "Codility", "Karat", "HackerEarth", "Byteboard") or \
        re.search(r"coding|technical|hackerrank|codesignal|codility", text, re.I) else "Assessment"


def _clean(name):
    return re.sub(r"^[^\w]+|[^\w.&)]+$", "", re.sub(r"(?i)^(?:(?:final )?reminder|re|fwd|\[action required\])\s*:?\s*", "", name)).strip()


GENERIC_COMPANY_NAMES = {"assessment", "coding", "technical", "online", "digital", "video", "interview"}


def company_of(subject, snippet, sender, companies):
    """Who sent the assessment: named in the subject, else a known company named in full, else the sender."""
    subject = _clean(subject)
    for pattern in (r"^(.+?) invited you to take", r"^(.+?) invites you to", r"^(.+?) has invited you", r"Next Steps? with (.+?):",
                    r"^(.+?) Application\s*[–-]", r"^(.+?):\s*Complete", r"^(.+?) is waiting for", r"^([\w&.' ]{2,40}?) Assessment$",
                    r"^([\w&.' ]{2,30}?) Interview Invitation", r"Complete Your ([\w&.' ]{2,30}?) Assessment"):
        found = re.search(pattern, subject)
        if (found and 1 < len(found.group(1)) < 45 and not re.search(r"\byour\b", found.group(1), re.I)
                and not (set(_norm(found.group(1)).split()) & _owner()[0])):
            candidate = _clean(found.group(1))
            if _norm(candidate) not in GENERIC_COMPANY_NAMES:
                return candidate
    text = " " + _norm(f"{subject} {snippet} {sender.split('@')[0]}") + " "
    known = sorted((c for c in companies if len(_norm(c)) > 2 and f" {_norm(c)} " in text), key=len, reverse=True)
    if known:
        return _clean(known[0])
    found = re.search(r"^(?:Thanks for taking|Assessment Completed:)\s+(?:the )?(?:\d{4}\s+)?([A-Z][\w&.]+)", subject)
    if found:
        return found.group(1)
    local = sender.split("@")[0].split(".")[0]
    domain = sender.split("@")[-1].split(".")[-2] if "@" in sender else ""
    fallback = (local if local.lower() not in ("no-reply", "noreply", "support", "robot", "notifications", "recruiting", "hr", "info", "job", "careers")
                else domain).capitalize()
    return "" if platform_of(sender, "") or _norm(fallback) in GENERIC_COMPANY_NAMES else fallback


GENERIC = {"reminder", "final", "your", "invitation", "invite", "invited", "action", "required", "assessment", "assessments",
           "expires", "expire", "hours", "hour", "days", "day", "in", "24", "for", "the", "to", "take", "on", "is",
           "waiting", "result", "results", "complete", "next", "step", "steps", "codesignal", "hackerrank", "codility", "from",
           "you", "re", "a", "an", "of", "and", "with", "still", "pending", "don", "t", "forget", "thanks", "taking", "completed"}


def same_assessment(a, b, company):
    """Two emails about one assessment: their distinctive title words overlap (or one has none left)."""
    words = lambda t: set(_norm(t).split()) - GENERIC - set(_norm(company).split()) - _owner()[0]
    x, y = words(a), words(b)
    return not x or not y or x <= y or y <= x or len(x & y) / len(x | y) >= 0.5


def title_of(subject):
    subject = _clean(subject)
    for pattern in (r"to take (.+?) on CodeSignal", r"HackerRank (.+?) Invitation", r"invite to (.+?) expires",
                    r"Thanks for taking (?:the )?(.+)$", r"Assessment Completed: (.+)$", r"complete (?:your|a|the) (.+)$"):
        found = re.search(pattern, subject, re.I)
        if found:
            return found.group(1).strip(" .!")
    name = r"(?:" + "|".join(map(re.escape, sorted(_owner()[0]))) + r")" if _owner()[0] else r"(?!x)x"
    subject = re.sub(r"(?i)^(?:" + name + r"(?:\s+" + name + r")*,\s*)?(?:you'?re invited!?|congrats!?)\s*", "", subject)
    subject = re.sub(r"(?i)^next steps?(?: with [^:]+)?\s*[:-]\s*", "", subject)
    subject = re.sub(r"(?i)\s*-\s*" + name + r"(?:\s+" + name + r")*\b", "", subject)
    return subject.strip()


def due_from(body, received):
    """The deadline stated in an email: an explicit date, else "expires in N days" counted from the email."""
    sent = datetime.strptime(received[:19], "%Y-%m-%dT%H:%M:%S")
    for found in EXPLICIT.finditer(body):
        text = re.sub(r"(\d)(?:st|nd|rd|th)", r"\1", found.group(1)).replace(",", " ").replace(".", " ")
        text = " ".join(text.split())
        for fmt in ("%B %d %Y", "%b %d %Y", "%B %d", "%b %d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                when = datetime.strptime(text.replace("Sept ", "Sep "), fmt)
            except ValueError:
                continue
            if "%Y" not in fmt and "%y" not in fmt:
                when = when.replace(year=sent.year)
                if when < sent - timedelta(days=1):
                    when = when.replace(year=sent.year + 1)
            return when.strftime("%Y-%m-%d")
    found = RELATIVE.search(body)
    if found:
        days = int(next(g for g in found.groups() if g))
        return (sent + timedelta(days=days)).strftime("%Y-%m-%d")
    return None


def _address(sender):
    """The bare address of "Name <user@host>" (or of a bare address), lowercased."""
    found = re.search(r"<([^>]+)>", sender or "")
    return (found.group(1) if found else sender or "").strip().lower()


def scan(state, days=45, search=None, bodies=None):
    """Add new assessments from Gmail, update due dates from reminders, and mark completed ones done."""
    search = search or gmail.search
    bodies = bodies or (lambda ids: {m["thread"]: m for m in gmail.threads_text(ids)})
    conn = db(state)
    companies = [r[0] for r in conn.execute("SELECT DISTINCT company FROM jobs")]
    owner_domains = _owner()[1]  # the user's school mail (courses, training) is not an assessment
    messages = [m for m in search(QUERY.format(days=days), 50) if not NOT_AN_ASSESSMENT.search(f"{m['subject']} {m['snippet']}")
                and not any(_address(m["sender"]).endswith(("." + d, "@" + d)) for d in owner_domains)]
    items = []
    for m in sorted(messages, key=lambda m: m["date"]):
        text = f"{m['subject']} {m['snippet']}"
        if not (DONE.search(text) or INVITE.search(text)):
            continue
        platform = platform_of(m["sender"], text)
        company = company_of(m["subject"], m["snippet"], m["sender"], companies)
        if not company:
            continue
        items.append({**m, "platform": platform, "company": company, "title": title_of(m["subject"]),
                      "kind": kind_of(platform, text), "done": bool(DONE.search(text))})
    # Bodies (links and deadlines) only for open assessments we have not read yet.
    known = {r["thread"] for r in conn.execute("SELECT thread FROM assessments WHERE due IS NOT NULL OR link IS NOT NULL")}
    wanted = sorted({i["thread"] for i in items if not i["done"] and i["thread"] and i["thread"] not in known})
    texts = bodies(wanted) if wanted else {}
    added, updated, done = [], [], []
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    for item in items:
        rows = [dict(r) for r in conn.execute("SELECT * FROM assessments") if company_key(r["company"]) == company_key(item["company"])]
        near = lambda r: abs((datetime.fromisoformat(r["received"][:19]) - datetime.fromisoformat(item["date"][:19])).days) <= 2
        same = [r for r in rows if r["thread"] == item["thread"] or same_assessment(r["title"], item["title"], item["company"])
                or (item["platform"] and r["platform"] == item["platform"] and r["received"] and near(r)
                    and bool(platform_of(r.get("sender") or "", "")) != bool(platform_of(item["sender"], "")))]
        if item["done"]:
            # "Thanks for taking X" closes that card, or every open card at the company on the same platform.
            targets = same + [r for r in rows if r not in same and r["status"] in ("todo", "in_progress", "expired")
                              and r["received"] <= item["date"] and item["platform"] and r["platform"] == item["platform"]]
            for row in targets:
                if row["status"] != "done":
                    conn.execute("UPDATE assessments SET status='done', updated=? WHERE key=?", (now, row["key"]))
                    done.append(f"{item['company']}: {row['title']}")
            if targets:
                continue
        # IMAP messages carry their body; connector threads are fetched by id.
        body = item.get("body") or texts.get(item["thread"], {}).get("body", "")
        due = due_from(" ".join(body.split()), item["date"]) if body else None
        link = (TEST_LINK.search(body) or [None])[0] if body else None
        platform = item["platform"] or (platform_of("", link or body[:0]) if link else "")
        email_url = f"https://mail.google.com/mail/u/0/#all/{item['thread']}" if item["thread"] else None
        if same:
            row = same[0]
            changes = {k: v for k, v in (("due", due), ("link", link)) if v and not row[k]}
            if changes:
                conn.execute("UPDATE assessments SET " + ", ".join(f"{k}=?" for k in changes) + ", updated=? WHERE key=?",
                             (*changes.values(), now, row["key"]))
                updated.append(f"{item['company']}: {row['title']}")
            continue
        key = f"{_norm(item['company'])}|{_norm(item['title'])}"
        conn.execute("INSERT OR IGNORE INTO assessments (key, company, title, kind, platform, status, due, link, email_url, thread,"
                     " received, subject, updated, sender) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (key, item["company"], item["title"], item["kind"], platform, "done" if item["done"] else "todo",
                      due, link, email_url, item["thread"], item["date"], item["subject"], now, item["sender"]))
        added.append(f"{item['company']}: {item['title']}" + (f" (due {due})" if due else ""))
    conn.commit()
    return {"added": added, "updated": updated, "done": done}


def board(state):
    """Cards for the console; an open card whose due date has passed shows as expired."""
    today = datetime.now().strftime("%Y-%m-%d")
    cards = []
    for r in db(state).execute("SELECT * FROM assessments ORDER BY COALESCE(due, '9999'), received"):
        card = dict(r)
        if card["status"] in ("todo", "in_progress") and card["due"] and card["due"] < today:
            card["status"] = "expired"
        cards.append(card)
    return cards


def update(state, key, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("status", "due", "note", "company", "title", "link")}
    if "status" in allowed and allowed["status"] not in STATUSES:
        raise ValueError("unknown status")
    if "due" in allowed:
        allowed["due"] = _due(allowed["due"])
    if "link" in allowed:
        allowed["link"] = _link(allowed["link"])
    for name in ("company", "title"):
        if name in allowed:
            allowed[name] = str(allowed[name]).strip()
            if not allowed[name]:
                raise ValueError(f"{name} is required")
    if not allowed:
        return {"updated": 0}
    conn = db(state)
    conn.execute("UPDATE assessments SET " + ", ".join(f"{k}=?" for k in allowed) + ", updated=? WHERE key=?",
                 (*allowed.values(), datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), key))
    conn.commit()
    return {"updated": conn.total_changes}


def _due(value):
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError("due date must be YYYY-MM-DD")
    return value


def _link(value):
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("assessment link must be an HTTP(S) URL")
    return value


def add(state, company, title, due=None, kind="Assessment", link=None):
    """A card the user adds by hand (an assessment mentioned on a call, say)."""
    due = _due(due)
    link = _link(link)
    conn = db(state)
    key = f"{_norm(company)}|{_norm(title)}"
    conn.execute("INSERT OR IGNORE INTO assessments (key, company, title, kind, status, due, link, manual, received, updated)"
                 " VALUES (?,?,?,?, 'todo', ?, ?, 1, ?, ?)", (key, company, title, kind, due or None, link or None,
                                                             datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                                             datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
    conn.commit()
    return {"added": key}
