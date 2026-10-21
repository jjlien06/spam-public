"""Import Simplify's "Tracked Jobs" CSV export: never re-apply, and put saved jobs first."""
import csv
import hashlib
import re
from .queue import canonical, job_key, platform

RANK = {"APPLIED": 2, "SAVED": 1}


def read_tracker(handle):
    """One entry per job, APPLIED winning over SAVED; non-HTTPS rows are ignored."""
    entries = {}
    for row in csv.DictReader(handle):
        url, status = row.get("Job URL", "").strip(), row.get("Status", "").strip().upper()
        if status not in RANK or not url.startswith("https://"):
            continue
        entry = {"job_key": job_key(url), "status": status, "company": row.get("Company Name", "").strip(),
                 "title": row.get("Job Title", "").strip(), "url": url, "location": row.get("Location", "").strip(),
                 "applied_date": row.get("Applied Date", "").strip() if status == "APPLIED" else "",
                 "job_type": row.get("job_type", "").strip()}
        current = entries.get(entry["job_key"])
        if not current or RANK[status] > RANK[current["status"]]:
            entries[entry["job_key"]] = entry
    return list(entries.values())


def import_tracker(queue, entries):
    """Record tracked jobs; add saved internships the Simplify list lacks.
    Statuses are applied by targeting, which never re-queues an applied job."""
    known = {row["job_key"] for row in queue.db.execute("SELECT job_key FROM jobs")}
    added = 0
    with queue.db:
        queue.db.executemany("INSERT OR REPLACE INTO tracked VALUES (:job_key,:status,:company,:title,:url,:location,:applied_date,:job_type)", entries)
        for entry in entries:
            if entry["status"] != "SAVED" or entry["job_type"] != "Internship" or entry["job_key"] in known:
                continue
            url = canonical(entry["url"])
            places = "; ".join(p.strip() for p in re.split(r"\s*[|;]\s*", entry["location"]) if p.strip())
            queue.db.execute("INSERT OR IGNORE INTO jobs(id,company,role,location,url,platform,locations,source,job_key)"
                             " VALUES (?,?,?,?,?,?,?,'tracker',?)",
                             (hashlib.sha256(url.encode()).hexdigest()[:20], entry["company"], entry["title"],
                              entry["location"], url, platform(url), places, entry["job_key"]))
            known.add(entry["job_key"])
            added += 1
    count = lambda status: sum(1 for e in entries if e["status"] == status)
    return {"tracked": len(entries), "applied": count("APPLIED"), "saved": count("SAVED"), "added_saved": added}
