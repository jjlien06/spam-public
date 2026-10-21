import json

from job_agent.ledger import build, duplicates
from job_agent.queue import Queue

JOBS = [
    {"id": "a1", "company": "Acme", "role": "Software Intern", "location": "NYC", "platform": "workday",
     "url": "https://acme.wd1.myworkdayjobs.com/x/job/NYC/Software-Intern_R0000001"},
    {"id": "a2", "company": "Acme", "role": "Software Intern", "location": "NYC", "platform": "workday",
     "url": "https://acme.wd1.myworkdayjobs.com/y/job/NYC/Software-Intern_R0000001"},  # same posting, other career site
    {"id": "b1", "company": "Beta", "role": "Data Intern", "location": "SF", "platform": "workday",
     "url": "https://beta.wd5.myworkdayjobs.com/x/job/SF/Data-Intern_R0000002"},
    {"id": "c1", "company": "Gamma", "role": "ML Intern", "location": "LA", "platform": "workday",
     "url": "https://gamma.wd5.myworkdayjobs.com/x/job/LA/ML-Intern_R0000003"},
]


def state(tmp_path):
    q = Queue(tmp_path / "jobs.sqlite3")
    q.ingest(JOBS)
    return q


def test_every_status_change_is_logged_with_a_time(tmp_path):
    q = state(tmp_path)
    q.mark("b1", "review")
    q.mark("b1", "submitted", "Application submitted; confirmation: Application Submitted")
    log = [dict(r) for r in q.db.execute("SELECT old_status, new_status, at FROM status_log WHERE job_id='b1'")]
    assert [(r["old_status"], r["new_status"]) for r in log] == [("queued", "review"), ("review", "submitted")]
    assert all(r["at"] for r in log)


def test_ledger_merges_spam_simplify_and_email_evidence(tmp_path):
    q = state(tmp_path)
    q.mark("b1", "submitted", "Application submitted; confirmation: Application Submitted")
    q.mark("a1", "applied", "Simplify tracker: applied 2026-09-18")
    q.mark("a2", "applied", "Simplify tracker: applied 2026-09-18")
    q.mark("c1", "already_applied", "Site said: already applied")
    q.db.execute("INSERT INTO tracked VALUES ('greenhouse:9', 'APPLIED', 'Delta', 'SWE Intern', 'https://x/9', 'Remote', '2026-09-15', 'Internship')")
    q.db.commit()
    (tmp_path / "confirmations.json").write_text(json.dumps({"c1": {"at": "2026-09-23T05:17:01", "email": "Gamma: Application Received"}}))
    entries = build(tmp_path)
    by = {(e["company"], e["source"]): e for e in entries}
    assert len(entries) == 4  # Acme's two listings are one posting
    assert by[("Beta", "SPAM")]["evidence"].startswith("Application submitted")
    assert by[("Beta", "SPAM")]["applied_at"]
    assert by[("Gamma", "Applied elsewhere")]["applied_at"] == "2026-09-23T05:17:01"
    assert by[("Gamma", "Applied elsewhere")]["email"] == "Gamma: Application Received"
    assert by[("Delta", "Simplify")]["applied_at"] == "2026-09-15"
    assert duplicates(entries, tmp_path) == []


def test_a_posting_submitted_twice_is_reported(tmp_path):
    q = state(tmp_path)
    q.mark("b1", "submitted", "Workday Job_Application_ID=00000000000000000000000000000001")
    q.mark("b1", "submit_uncertain", "retry")
    q.mark("b1", "submitted", "Workday Job_Application_ID=00000000000000000000000000000002")
    assert [e["company"] for e in duplicates(build(tmp_path), tmp_path)] == ["Beta"]


def test_email_only_applications_and_outcomes(tmp_path):
    q = state(tmp_path)
    q.mark("b1", "submitted", "Application submitted; confirmation: Application Submitted")
    (tmp_path / "email_applications.json").write_text(json.dumps([
        {"key": "zeta-200001", "company": "Zeta", "role": "SWE Intern", "applied_at": "2026-09-03T10:00:00",
         "email": "Thank you for your application! [personal inbox]"}]))
    (tmp_path / "outcomes.json").write_text(json.dumps({
        "b1": {"outcome": "rejected", "at": "2026-09-20T09:00:00", "email": "Update on your application"},
        "email:zeta-200001": {"outcome": "interview", "at": "2026-09-21T09:00:00", "email": "Interview invitation"}}))
    by = {e["company"]: e for e in build(tmp_path)}
    assert by["Zeta"]["source"] == "Email (by hand)" and by["Zeta"]["applied_at"] == "2026-09-03T10:00:00"
    assert by["Zeta"]["outcome"] == "interview 2026-09-21"
    assert by["Beta"]["outcome"] == "rejected 2026-09-20"
