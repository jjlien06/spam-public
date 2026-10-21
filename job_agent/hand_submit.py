"""Hand-submit board: every job waiting for the user's own Submit click (Ashby, Lever CAPTCHA), matched to
its open browser window, so the console can bring one window forward at a time."""
import json
import re
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

CDP = "http://127.0.0.1:9333"
USER_SUBMITTED = "User pressed Submit; waiting for the confirmation email."


def posting_keys(url):
    """The ids that identify a posting inside any of its URLs (Ashby/Lever UUIDs, Greenhouse tokens)."""
    path, _, query = url.partition("?")
    keys = [part for part in path.split("/") if len(part) >= 8 and re.search(r"\d", part)]
    keys += [value for value in re.findall(r"(?:token|gh_jid|jid)=([\w-]{6,})", query)]
    return keys


def match_windows(jobs, targets):
    pages = [t for t in targets if t.get("type") == "page"]
    rows = []
    for job in jobs:
        keys = posting_keys(job["url"])
        target = next((t["id"] for t in pages if any(k in t["url"] for k in keys)), None)
        rows.append({"id": job["id"], "company": job["company"], "role": job["role"], "url": job["url"],
                     "target": target, "user_submitted": str(job.get("note") or "").startswith(USER_SUBMITTED)})
    return rows


def _cdp(path):
    with urllib.request.urlopen(CDP + path, timeout=5) as response:
        return response.read().decode()


def _jobs(state):
    db = sqlite3.connect(Path(state) / "jobs.sqlite3")
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute("SELECT id, company, role, url, note FROM jobs WHERE status='hand_submit' ORDER BY company")]


def board(state):
    try:
        targets = json.loads(_cdp("/json/list"))
    except OSError:
        targets = []
    return match_windows(_jobs(state), targets)


def show(state, job_id):
    """Bring this job's window to the front (the user asked for it on the board)."""
    row = next((r for r in board(state) if r["id"] == job_id), None)
    if not row or not row["target"]:
        return {"error": "That window is no longer open. Use Open posting to apply in your own browser."}
    _cdp(f"/json/activate/{row['target']}")
    return {"message": f"{row['company']} is in front."}


def mark_submitted(state, job_id):
    """The user says they pressed Submit: note it and close the window. The job stays hand_submit until the
    employer's email arrives (`confirmations` turns it submitted), so it is never counted early."""
    row = next((r for r in board(state) if r["id"] == job_id), None)
    if not row:
        return {"error": "unknown job"}
    db = sqlite3.connect(Path(state) / "jobs.sqlite3")
    db.execute("UPDATE jobs SET note=? WHERE id=? AND status='hand_submit'",
               (f"{USER_SUBMITTED} {datetime.now().isoformat(timespec='seconds')}", job_id))
    db.commit()
    if row["target"]:
        try:
            _cdp(f"/json/close/{row['target']}")
        except OSError:
            pass
    return {"message": f"{row['company']}: marked. It counts as applied when the confirmation email arrives."}
