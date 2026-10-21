"""Read the user's Gmail without an agent in the loop: security codes for waiting Greenhouse
submits, and employer confirmation emails for the ledger.

Gmail is reached through the user's own Claude Code login (the claude.ai Gmail connector,
read-only search). A small headless `claude -p` calls search_threads once; we parse the
tool's own JSON result from the stream, so codes and subjects are never retyped by a model.
"""
import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .queue import Queue

SEARCH_TOOL = "mcp__claude_ai_Gmail__search_threads"
CODE_QUERY = 'subject:"Security code for your application to" newer_than:3d'
CONFIRM_QUERY = ('newer_than:{days}d -subject:"security code" ("thank you for applying" OR "thanks for applying" OR '
                 '"application received" OR "received your application" OR "thank you for your application" OR '
                 '"application submitted" OR "application was submitted" OR "thank you for your interest")')
CODE = re.compile(r"security code field on your application:\s*([A-Za-z0-9]{8})\b")
STOPWORDS = {"inc", "llc", "corp", "corporation", "company", "co", "the", "group", "holdings", "technologies",
             "technology", "systems", "international", "usa", "us", "na", "of", "and", "services", "solutions",
             # Too common to identify an employer on their own ("Keystone Capital Partners" vs "Akuna Capital").
             "capital", "bank", "financial", "partners", "global", "america", "american", "national", "labs", "ai",
             "software", "health", "insurance", "trading", "securities", "investments", "management", "research",
             "digital", "data", "energy", "networks", "media", "first", "united", "general"}


THREAD_TOOL = "mcp__claude_ai_Gmail__get_thread"


def mailboxes_file():
    from . import paths
    return paths.state() / "mailboxes.json"


def extra_mailboxes():
    """Other Gmail inboxes (e.g. a personal address some hiring accounts use), read over IMAP with an
    app password from Keychain. Listed in state/mailboxes.json by `job-agent gmail-login`."""
    try:
        return json.loads(mailboxes_file().read_text())
    except (OSError, ValueError):
        return []


def uses_connector():
    """The claude.ai Gmail connector needs Claude Code; Codex users (or anyone who chose it in setup) use IMAP only."""
    from .ai import settings
    return settings().get("gmail", "connector") != "imap" and bool(shutil.which("claude"))


def search(query, limit=20, runner=subprocess.run, mailboxes=None):
    """Messages matching a Gmail query in every connected inbox, newest first:
    {id, thread, date (local), sender, subject, snippet, to}."""
    addresses = extra_mailboxes() if mailboxes is None else mailboxes
    messages = []
    if uses_connector():
        try:
            messages = connector_search(query, limit, runner)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            if not addresses:
                raise
            import warnings
            warnings.warn(f"Claude Gmail connector failed; searching configured IMAP inboxes only: {error}", RuntimeWarning)
    for address in addresses:
        messages += imap_search(address, query, limit)
    return sorted(messages, key=lambda m: m["date"], reverse=True)


def email_text(body):
    """Readable text of an email body: HTML tags and <style>/<script>/<head> blocks removed (Workday's
    notifications start with CSS, which otherwise fills the snippet), entities decoded."""
    import html
    body = re.sub(r"(?is)<(style|script|head)\b.*?</\1\s*>", " ", body)
    body = re.sub(r"(?is)<!--.*?-->", " ", body)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())


def imap_search(address, query, limit=20):
    """Gmail search syntax over IMAP (X-GM-RAW), read-only; the body's start stands in for the snippet."""
    import imaplib
    from email import policy
    from email.parser import BytesParser
    from email.utils import parsedate_to_datetime
    from .accounts import Vault
    password = Vault().get("imap.gmail.com", address)
    if not password:
        raise RuntimeError(f"No app password stored for {address}; run job-agent gmail-login")
    found = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as client:
        client.login(address, password)
        client.select('"[Gmail]/All Mail"', readonly=True)
        status, data = client.uid("search", None, "X-GM-RAW", '"' + query.replace('"', '\\"') + '"')
        if status != "OK":
            raise RuntimeError(f"IMAP search failed for {address}")
        for uid in data[0].split()[-limit:]:
            status, parts = client.uid("fetch", uid, "(X-GM-THRID BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None) if status == "OK" else None
            if not raw:
                continue
            message = BytesParser(policy=policy.default).parsebytes(raw)
            part = message.get_body(("plain", "html"))
            body = part.get_content() if part else ""
            text = email_text(body)
            try:
                date = parsedate_to_datetime(message["Date"]).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
            except (TypeError, ValueError):
                date = ""
            found.append({"id": f"{address}:{uid.decode()}", "thread": None, "date": date, "sender": str(message["From"] or ""),
                          "subject": str(message["Subject"] or ""), "snippet": text[:400], "to": address, "body": text})
    return found


def connector_search(query, limit=20, runner=subprocess.run):
    data = call(SEARCH_TOOL, {"query": query, "pageSize": min(limit, 50)}, runner)
    messages = [_message(m) for thread in data.get("threads", []) for m in thread.get("messages", [])]
    return sorted(messages, key=lambda m: m["date"], reverse=True)


def thread_text(thread_id, runner=subprocess.run):
    """Plain-text bodies of a thread's messages, oldest first."""
    data = call(THREAD_TOOL, {"threadId": thread_id, "messageFormat": "PLAIN_TEXT"}, runner)
    return [{**_message(m), "body": m.get("plaintextBody") or m.get("plaintext_body") or ""} for m in data.get("messages", [])]


def threads_text(thread_ids, runner=subprocess.run, chunk=8):
    """Plain-text bodies of several threads, a few per headless call; one entry per message."""
    out = []
    for start in range(0, len(thread_ids), chunk):
        ids = thread_ids[start:start + chunk]
        prompt = (f"For each of these Gmail thread ids, call {THREAD_TOOL} once with messageFormat PLAIN_TEXT: "
                  f"{json.dumps(ids)}. Do not call any other tool. Then reply with the single word done.")
        try:
            result = runner(["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", "haiku",
                            "--allowedTools", THREAD_TOOL], capture_output=True, text=True, timeout=600,
                           cwd=Path(__file__).resolve().parent.parent)
            data = parse_stream(result.stdout, THREAD_TOOL)
            batch = [{**_message(m), "body": m.get("plaintextBody") or m.get("plaintext_body") or ""}
                     for m in data.get("messages", [])]
        except (RuntimeError, subprocess.TimeoutExpired):
            batch = []
        out += batch
        returned = {m["thread"] for m in batch if m["thread"]}
        # A multi-call prompt can be only partly executed; retry only the missing threads one at a time.
        for thread_id in (thread_id for thread_id in ids if thread_id not in returned):
            try:
                out += thread_text(thread_id, runner)
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
    return out


def call(tool, arguments, runner=subprocess.run, tries=3):
    """Run one read-only Gmail connector call through the user's Claude Code login and return the
    tool's own JSON. The connector sometimes answers "service is currently unavailable"; that is retried."""
    for attempt in range(tries):
        prompt = (f"Call {tool} exactly once with exactly these arguments: {json.dumps(arguments)}. "
                  "Do not call any other tool. Then reply with the single word done.")
        result = runner(["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", "haiku",
                         "--allowedTools", tool], capture_output=True, text=True, timeout=180,
                        cwd=Path(__file__).resolve().parent.parent)
        try:
            return parse_stream(result.stdout, tool)
        except RuntimeError as error:
            # The connector sometimes answers "unavailable", and the small model now and then replies
            # without making the call: both are worth another try.
            if attempt == tries - 1 or not re.search(r"unavailable|did not run", str(error), re.I):
                raise
            time.sleep(5 * (attempt + 1))


def parse_stream(stdout, tool=SEARCH_TOOL):
    """Pull a tool's JSON result out of a claude stream-json transcript."""
    call_ids, merged, parsed = set(), {}, False
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        message = item.get("message") if isinstance(item, dict) else None
        for block in (message.get("content") or [] if isinstance(message, dict) else []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and re.search(r"session limit|rate limit", block.get("text", ""), re.I):
                raise RuntimeError(f"Claude session limit: {block.get('text', '')[:200]}")
            if block.get("type") == "tool_use" and block.get("name") == tool:
                call_ids.add(block.get("id"))
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in call_ids:
                if block.get("is_error"):
                    raise RuntimeError(f"Gmail call failed: {str(block.get('content'))[:200]}")
                content = block.get("content")
                texts = [c.get("text", "") for c in content if isinstance(c, dict)] if isinstance(content, list) else [str(content)]
                for text in texts:
                    # Big results are written to a file and only a preview is inlined.
                    saved = re.search(r"Full output saved to: (\S+\.txt)", text)
                    if saved and Path(saved.group(1)).exists():
                        text = Path(saved.group(1)).read_text()
                    try:
                        data = json.loads(text)
                    except ValueError:
                        if "unavailable" in text.lower():
                            raise RuntimeError(f"Gmail call failed: {text[:200]}")
                        continue
                    if not isinstance(data, dict):
                        raise RuntimeError("Gmail call returned JSON that was not an object")
                    parsed = True
                    for key, value in data.items():
                        if isinstance(value, list) and isinstance(merged.get(key), list):
                            merged[key] += value
                        else:
                            merged[key] = value
    if not call_ids:
        raise RuntimeError("Gmail call did not run (is the claude.ai Gmail connector connected?)")
    if not parsed:
        raise RuntimeError("Gmail call returned no parseable JSON object")
    return merged


def _message(m):
    return {"id": m.get("id"), "thread": m.get("threadId"), "date": _local(m.get("date", "")), "sender": m.get("sender", ""),
            "subject": m.get("subject", ""), "snippet": m.get("snippet", ""), "to": ", ".join(m.get("toRecipients", []))}


def _local(stamp):
    """Gmail's UTC '2026-09-24T06:26:44Z' as local time in status_log's format."""
    try:
        when = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return stamp[:19]
    return when.astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def _minutes_before(stamp, minutes):
    try:
        return (datetime.strptime((stamp or "")[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ""


def words(company):
    return {w for w in re.findall(r"[a-z0-9]+", company.lower().replace("&", " ")) if w not in STOPWORDS and len(w) > 1}


def mentions(company, text):
    """The company's distinctive words all appear in the text ("XQT Corporation" -> "xqt")."""
    wanted = words(company)
    found = set(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(wanted) and wanted <= found


def _tokens(text):
    return {w[:-3] if w.endswith("ing") and len(w) > 6 else w for w in re.findall(r"[a-z0-9]+", text.lower())}


def names_role(jobs, text):
    """The email names something that tells these jobs' roles apart."""
    roles = [_tokens(j["role"]) for j in jobs]
    return bool((set.union(*roles) - set.intersection(*roles)) & _tokens(text))


def best_role(jobs, text):
    """The jobs whose role title best fits the email: words shared, minus twice the role words the
    email lacks ("Software Engineer" vs "Software Operations Engineer"). Ties are all returned."""
    if len(jobs) < 2:
        return jobs
    if not names_role(jobs, text):
        return jobs
    found = _tokens(text)
    scored = [(len(_tokens(j["role"]) & found) - 2 * len(_tokens(j["role"]) - found), j) for j in jobs]
    top = max(score for score, _ in scored)
    return [j for score, j in scored if score == top]


def code_candidates(messages, company, since):
    """8-character codes for this company sent after `since` (local time), newest first."""
    codes = []
    for m in messages:
        subject = m["subject"].split("application to", 1)[-1]
        found = CODE.search(m["snippet"])
        if found and m["date"] >= since and mentions(company, subject):
            codes.append({"code": found.group(1), "date": m["date"], "message": m["id"]})
    return codes


def _used_path(state):
    return Path(state) / "codes_used.json"


def fill_codes(state, messages=None, enter=None):
    """Enter every fresh emailed code into the job waiting for it. A code is tried at most once."""
    from .ops import enter_code
    enter = enter or enter_code
    queue = Queue(Path(state) / "jobs.sqlite3")
    waiting = [dict(r) for r in queue.db.execute(
        "SELECT j.id, j.company, j.role, (SELECT MAX(at) FROM status_log s WHERE s.job_id=j.id) since FROM jobs j"
        " WHERE j.status='needs_input' AND j.note LIKE '%email_code_required%'")]
    if not waiting:
        return {"waiting": 0, "results": []}
    messages = search(CODE_QUERY, 30) if messages is None else messages
    used = json.loads(_used_path(state).read_text()) if _used_path(state).exists() else {}
    results = []
    for job in waiting:
        # The code is emailed right after the submit that stopped; older codes belong to earlier attempts.
        since = _minutes_before(job["since"], 15)
        outcome = {"id": job["id"], "company": job["company"], "status": "no code yet"}
        for candidate in code_candidates(messages, job["company"], since):
            # A code that submitted one job is spent; a rejected one may still belong to another job here.
            spent = used.get(candidate["message"], {}).get("status") == "submitted"
            if spent or f'{candidate["message"]}:{job["id"]}' in used:
                continue
            status = enter(state, job["id"], candidate["code"]).get("status")
            used[f'{candidate["message"]}:{job["id"]}'] = used[candidate["message"]] = {"job": job["id"], "status": status, "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
            _used_path(state).write_text(json.dumps(used, indent=1))
            outcome = {**outcome, "status": status, "email_at": candidate["date"]}
            if status != "code rejected":
                break
        results.append(outcome)
    return {"waiting": len(waiting), "results": results}


def confirmations(state, days=3, messages=None, record=None):
    """Match employer confirmation emails to submitted jobs that have no evidence yet. A message is
    recorded only when exactly one unconfirmed job at that company fits it (by role words when several)."""
    from .ops import confirm
    record = record or confirm
    queue = Queue(Path(state) / "jobs.sqlite3")
    path = Path(state) / "confirmations.json"
    have = json.loads(path.read_text()) if path.exists() else {}
    jobs = [dict(r) for r in queue.db.execute(
        "SELECT j.id, j.company, j.role, (SELECT MAX(at) FROM status_log s WHERE s.job_id=j.id AND s.new_status IN"
        " ('submitted','submit_uncertain','hand_submit')) sent, j.status FROM jobs j"
        " WHERE j.status IN ('submitted','submit_uncertain','hand_submit')")]
    open_jobs = [j for j in jobs if j["id"] not in have and j["sent"]]
    all_open = list(open_jobs)
    messages = search(CONFIRM_QUERY.format(days=days), 50) if messages is None else messages
    taken = {v.get("email") + "|" + v.get("at", "") for v in have.values()}
    recorded, unmatched = [], []
    for m in sorted(messages, key=lambda m: m["date"]):
        if f"{m['subject']}|{m['date']}" in taken:
            continue
        text = f"{m['sender']} {m['subject']} {m['snippet']}"
        fits = [j for j in open_jobs if mentions(j["company"], text) and j["sent"][:16] <= m["date"][:16]]
        best = best_role(fits, text)
        siblings = [j for j in all_open if best and j["company"] == best[0]["company"]]
        order_matched = len(siblings) > 1 and not names_role(siblings, text)
        if len(best) > 1 and len({j["company"] for j in best}) == 1:
            # Identical emails that don't name the role (Unison AI, Sherwood): one per application,
            # paired in submit order and labelled so the ledger shows the weaker evidence.
            best = sorted(best, key=lambda j: j["sent"])[:1]
        if len(best) == 1:
            job = best[0]
            subject = m["subject"] + (" [matched by order]" if order_matched else "")
            record(state, job["id"], subject, m["date"])
            if job["status"] == "hand_submit":  # the user pressed Submit; the email proves it
                queue.mark(job["id"], "submitted", "Submitted by hand; confirmation: " + subject[:300])
            open_jobs.remove(job)
            recorded.append(f"{job['company']} | {job['role']} <- {subject[:90]}")
        elif best:
            unmatched.append(f"{m['date']} {m['subject'][:80]} (fits {len(best)} jobs: {', '.join(j['id'] for j in best)})")
    return {"recorded": recorded, "ambiguous": unmatched, "still_unconfirmed": len(open_jobs)}


VERIFY_QUERY = 'subject:"Verify your candidate account" newer_than:2d'
ACTIVATE = re.compile(r"https://[\w.-]+\.(?:myworkdayjobs|myworkday|myworkdaysite)\.com/\S*activate/\S+")


def verify_accounts(state, messages=None, fetch=None, opener=None, days=2):
    """Open the "Verify your candidate account" link for every Workday job stopped on account
    verification. Returns the jobs whose account is now verified; they need a refill."""
    from .ops import open_link
    fetch, opener = fetch or thread_text, opener or open_link
    queue = Queue(Path(state) / "jobs.sqlite3")
    waiting = [dict(r) for r in queue.db.execute(
        "SELECT j.id, j.company, j.url, (SELECT MAX(at) FROM status_log s WHERE s.job_id=j.id) since FROM jobs j"
        " WHERE j.status='needs_input' AND j.note LIKE '%email_verification_required%'")]
    if not waiting:
        return {"waiting": 0, "verified": [], "no_email": []}
    messages = search(VERIFY_QUERY.replace("newer_than:2d", f"newer_than:{days}d"), 50) if messages is None else messages
    verified, missing = [], []
    for job in waiting:
        host = re.match(r"https://([^/]+)", job["url"]).group(1).lower()
        since = _minutes_before(job["since"], 30) if days <= 2 else ""  # a wider search accepts older emails
        # The snippet shows the link's host but cuts the token; the full body has the whole link.
        mail = next((m for m in messages if m["date"] >= since and f"https://{host}/" in m["snippet"].lower()), None)
        link = None
        if mail:
            for message in ([mail] if mail.get("body") else fetch(mail["thread"])):
                found = [u for u in ACTIVATE.findall(message.get("body", "")) if f"//{host}/" in u.lower()]
                link = found[-1].rstrip(").>]'\"") if found else link
        if not link:
            missing.append(f"{job['id']} {job['company']}")
            continue
        landed = opener(link)
        verified.append({"id": job["id"], "company": job["company"], "landed": str(landed.get("text", ""))[:100]})
    return {"waiting": len(waiting), "verified": verified, "no_email": missing}
