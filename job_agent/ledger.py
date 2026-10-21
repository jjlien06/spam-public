"""One precise list of every application sent, whoever sent it.

Sources, one row per posting (job_key), first evidence wins:
  - SPAM submissions: queue status "submitted"; time from the status log (or the
    run report written when the submit finished); evidence is the site's
    confirmation text or Job_Application_ID.
  - Simplify: the imported Tracked Jobs export (APPLIED rows, with their date),
    including postings that never reached SPAM's queue.
  - Applied elsewhere: a site said "You applied for this job" (status applied or
    already_applied without a Simplify note), i.e. by hand in the browser.
  - Shortlist: roles marked Applied on the Summer 2027 Shortlist page
    (state/shortlist-marks.json, synced from the page).
  - Email (by hand): state/email_applications.json lists applications known only from an
    employer's confirmation email (a posting SPAM never queued), as {key, company, role,
    applied_at, email}.
  - Outcomes: state/outcomes.json maps a job id (or "email:<key>") to {outcome, at, email}
    (rejected, assessment, interview, offer, withdrawn), shown in the outcome column.
  - Emails: state/confirmations.json maps a job id to {"at", "email"}: the
    employer's "thank you for applying" email and its time, which sets
    applied_at when it is earlier or the only record.

Run `python -m job_agent.ledger` to write state/applications.csv and print totals.
"""
import csv
import datetime
import json
import sys
from pathlib import Path

from .queue import Queue

FIELDS = ["company", "role", "location", "source", "applied_at", "status", "evidence", "email", "job_key", "url", "job_id", "outcome"]


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _run_report(state, job_id):
    path = Path(state) / "runs" / f"{job_id}.json"
    if not path.exists():
        return {}, None
    report = _load(path)
    at = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
    return report, at


def build(state=None):
    from . import paths
    state = state or paths.state()
    queue = Queue(Path(state) / "jobs.sqlite3")
    db = queue.db
    logged = {}
    for row in db.execute("SELECT job_id, new_status, note, at FROM status_log WHERE new_status IN "
                          "('submitted','applied','already_applied','submit_uncertain') ORDER BY at"):
        logged.setdefault(row["job_id"], dict(row))
    shortlist = _load(Path(state) / "shortlist-marks.json")
    emails = _load(Path(state) / "confirmations.json")
    entries, seen = [], set()

    for job in queue.rows():
        if job["job_key"] in seen:
            continue  # the same posting under another listing (LXEG's two career sites)
        status, note = job["status"], job["note"] or ""
        applied_by_shortlist = shortlist.get(job["id"]) == "applied"
        if status not in ("submitted", "applied", "already_applied", "submit_uncertain") and not applied_by_shortlist:
            continue
        report, report_at = _run_report(state, job["id"])
        log = logged.get(job["id"], {})
        if status == "submitted":
            source = "SPAM"
            evidence = report.get("confirmation") or note
        elif status == "submit_uncertain":
            source = "SPAM (unconfirmed)"
            evidence = note or "Submit clicked; no confirmation found"
        elif note.startswith("Simplify tracker"):
            source, evidence = "Simplify", note
        elif applied_by_shortlist:
            source, evidence = "Shortlist (by hand)", "Marked Applied on the shortlist page"
        else:
            source, evidence = "Applied elsewhere", note or "Site said: already applied"
        when = log.get("at") or (note.rsplit(" ", 1)[-1] if source == "Simplify" else report_at) or ""
        email = emails.get(job["id"]) or {}
        if isinstance(email, str):
            email = {"email": email}
        # The employer's confirmation email is the best record of when it went in
        # (SPAM only notices an "already applied" page later).
        if email.get("at") and (not when or source == "Applied elsewhere" or email["at"] < when):
            when = email["at"]
        entries.append({"company": job["company"], "role": job["role"], "location": job["locations"] or job["location"],
                        "source": source, "applied_at": when, "status": status, "evidence": " ".join(evidence.split())[:300],
                        "email": email.get("email", ""), "job_key": job["job_key"], "url": job["url"], "job_id": job["id"]})
        seen.add(job["job_key"])

    # Simplify applications for postings that never entered SPAM's queue.
    for row in db.execute("SELECT * FROM tracked WHERE status='APPLIED'"):
        if row["job_key"] in seen:
            continue
        seen.add(row["job_key"])
        entries.append({"company": row["company"], "role": row["title"], "location": row["location"], "source": "Simplify",
                        "applied_at": row["applied_date"] or "", "status": "applied", "evidence": "Simplify Tracked Jobs export",
                        "email": "", "job_key": row["job_key"], "url": row["url"], "job_id": ""})
    # Applications known only from a confirmation email.
    extra = _load(Path(state) / "email_applications.json")
    for row in extra if isinstance(extra, list) else []:
        key = "email:" + row["key"]
        if key in seen:
            continue
        seen.add(key)
        entries.append({"company": row["company"], "role": row["role"], "location": row.get("location", ""),
                        "source": "Email (by hand)", "applied_at": row.get("applied_at", ""), "status": "applied",
                        "evidence": row.get("email", ""), "email": row.get("email", ""), "job_key": key,
                        "url": row.get("url", ""), "job_id": ""})
    outcomes = _load(Path(state) / "outcomes.json")
    for e in entries:
        found = outcomes.get(e["job_id"]) if e["job_id"] else None
        found = found or outcomes.get(e["job_key"]) or {}
        e["outcome"] = f"{found['outcome']} {found.get('at', '')[:10]}".strip() if found.get("outcome") else ""
    entries.sort(key=lambda e: (e["applied_at"] or "", e["company"].lower()))
    return entries


def duplicates(entries, state=None):
    from . import paths
    state = state or paths.state()
    """Postings submitted more than once: two confirmations for one posting (FIS's
    guest apply gave two Job_Application_IDs), or two submitted entries in the log."""
    import re
    queue = Queue(Path(state) / "jobs.sqlite3")
    submits = {}
    for row in queue.db.execute("SELECT job_id FROM status_log WHERE new_status='submitted' AND old_status!='submitted'"):
        submits[row["job_id"]] = submits.get(row["job_id"], 0) + 1
    found = []
    for e in entries:
        ids = set(re.findall(r"Job_Application_ID[ =]*([0-9a-f]{16,})|\b([0-9a-f]{32})\b", e["evidence"]))
        if len(ids) > 1 or submits.get(e["job_id"], 0) > 1:
            found.append(e)
    return found


def main(argv=None):
    from . import paths
    state = (argv or sys.argv[1:] or [str(paths.state())])[0]
    entries = build(state)
    out = Path(state) / "applications.csv"
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(entries)
    counts = {}
    for e in entries:
        counts[e["source"]] = counts.get(e["source"], 0) + 1
    print(f"{len(entries)} applications -> {out}")
    for source, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {source}: {n}")
    outcomes = {}
    for e in entries:
        if e["outcome"]:
            kind = e["outcome"].split()[0]
            outcomes[kind] = outcomes.get(kind, 0) + 1
    if outcomes:
        print("  outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())))
    dups = duplicates(entries, state)
    print(f"  submitted more than once: {len(dups)}")
    for e in dups:
        print(f"   - {e['company']} | {e['role']} | {e['evidence'][:160]}")
    undated = [e for e in entries if not e["applied_at"]]
    if undated:
        print(f"  no recorded time: {len(undated)} (" + ", ".join(sorted({e['company'] for e in undated})) + ")")


if __name__ == "__main__":
    main()
