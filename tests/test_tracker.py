import csv
import io
from job_agent.queue import Queue, job_key, parse_list
from job_agent.targeting import apply_targeting, ranked
from job_agent.tracker import read_tracker, import_tracker

PREFS = {"categories": ["Software Engineering"], "us_only": True, "skip_advanced_degree": True}
APPLY = '<td><a href="{url}"><img alt="Apply"></a></td>'
LISTING = "## 💻 Software Engineering Internship Roles\n<table>" + "".join(
    f"<tr><td>{c}</td><td>{r}</td><td>{l}</td>{APPLY.format(url=u)}<td>{a}</td></tr>" for c, r, l, u, a in [
        ("Kodig", "Software Engineer Intern", "Nashville, TN", "https://kodig.com/careers/job-listing/?gh_jid=5000000001", "0d"),
        ("QTX", "Software Engineering Intern", "Springfield, IL",
         "https://qtxhr.wd5.myworkdayjobs.com/rec_qtx_ext_gateway/job/US-XX-SPRINGFIELD/Software-Engineering-Intern--Summer-2027-_09000002", "3d"),
        ("Voltra", "Software Engineering Intern", "Palo Alto, CA", "https://www.voltra.com/careers/search/job/100001", "1d"),
        ("Tymer", "Software Development Intern", "Plano, TX", "https://jobs.jobvite.com/tymertech/job/aBcDeFgH", "0d"),
        ("Tymer", "Software Development Intern", "Plano, TX", "https://jobs.jobvite.com/tymertech/job/aBcDeFgH?nl=1", "0d"),
        ("Acme", "Software Intern", "NYC", "https://acme.wd1.myworkdayjobs.com/x/job/NYC/Software-Intern_R-1", "0d"),
    ]) + "</table>"
HEADER = "Job Title,Company Name,Location,Job URL,Applied Date,Status,Status Date,Archived,Date Archived,Notes,job_type\n"
TRACKER = HEADER + "\n".join([
    'Software Engineering Intern,Kodig,"Nashville, TN",https://www.kodig.com/careers/job-listing?gh_jid=5000000001&gh_src=Simplify,2026-09-22,APPLIED,2026-09-22,No,N/A,,Internship',
    'Software Engineering Intern,Voltra,"Palo Alto, CA, USA",https://www.voltra.com/careers/search/job/apply/100001,2026-09-21,APPLIED,2026-09-21,No,N/A,,Internship',
    'Software Engineering Intern,QTX,"Springfield, IL, USA",https://qtxhr.wd5.myworkdayjobs.com/rec_qtx_ext_gateway/job/US-XX-SPRINGFIELD-1/Software-Engineering-Intern--Summer-2027-_09000002,N/A,SAVED,2026-09-21,No,N/A,,Internship',
    'FPGA Intern,QRW,"Chicago, IL, USA",https://job-boards.greenhouse.io/qrweng/jobs/8000001,N/A,SAVED,2026-09-20,No,N/A,,Internship',
    'Software Engineer Co-op,Ironvale,"Quincy, MA",https://boards.greenhouse.io/ironvaleindustries/jobs/5000000007,2026-09-20,APPLIED,2026-09-20,No,N/A,,Internship',
    'Winter 2027 Software Engineer Co-op,Ironvale,"Quincy, MA",https://job-boards.greenhouse.io/ironvaleindustries/jobs/5000000007?gh_src=x,N/A,SAVED,2026-09-19,No,N/A,,Internship',
    '"Summer 2026 Intern, Memory Technology",Examdisk,"Milpitas, CA",https://jobs.smartrecruiters.com/Examdisk/700000000000001,N/A,SAVED,2026-09-01,No,N/A,,Internship',
    'Not a Real Job Application,Simplify,Remote,chrome-extension://abc/tutorial.html,N/A,SAVED,2026-09-01,No,N/A,,Full-time',
])


def queue(tmp_path):
    q = Queue(tmp_path / "jobs.db")
    q.ingest(list(parse_list(LISTING)))
    return q


def test_job_key_uses_each_sites_own_job_id():
    assert job_key("https://www.kodig.com/careers/job-listing?gh_jid=5000000001&gh_src=Simplify") == \
        job_key("https://kodig.com/careers/job-listing/?gh_jid=5000000001")
    assert job_key("https://job-boards.greenhouse.io/ironvaleindustries/jobs/5000000007?gh_src=x") == \
        job_key("https://boards.greenhouse.io/ironvaleindustries/jobs/5000000007")
    assert job_key("https://www.voltra.com/careers/search/job/apply/100001") == \
        job_key("https://www.voltra.com/careers/search/job/100001")
    assert job_key("https://qtxhr.wd5.myworkdayjobs.com/rec_qtx_ext_gateway/job/A/Intern_09000002/apply") == \
        job_key("https://qtxhr.wd5.myworkdayjobs.com/fr-CA/other/job/B/Intern_09000002")
    assert job_key("https://jobs.ashbyhq.com/rivel/aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb/application?embed=true") == \
        job_key("https://jobs.ashbyhq.com/rivel/aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb")
    # Different req ids on the same tenant stay different jobs.
    assert job_key("https://acme.wd1.myworkdayjobs.com/x/job/NYC/Software-Intern_R-1") != \
        job_key("https://acme.wd1.myworkdayjobs.com/x/job/NYC/Software-Intern_R-2")


def test_applied_jobs_are_never_queued_again(tmp_path):
    q = queue(tmp_path)
    import_tracker(q, read_tracker(io.StringIO(TRACKER)))
    apply_targeting(q, PREFS)
    status = {r["company"]: (r["status"], r["note"]) for r in q.rows()}
    assert status["Kodig"] == ("applied", "Simplify tracker: applied 2026-09-22")
    assert status["Voltra"][0] == "applied"
    # Re-running targeting with wider prefs never restores an applied job.
    apply_targeting(q, {"categories": [], "us_only": False})
    assert {r["company"]: r["status"] for r in q.rows()}["Kodig"] == "applied"


def test_newest_jobs_go_first_and_missing_saved_internships_are_added(tmp_path):
    q = queue(tmp_path)
    summary = import_tracker(q, read_tracker(io.StringIO(TRACKER)))
    assert summary == {"tracked": 6, "applied": 3, "saved": 3, "added_saved": 2}
    apply_targeting(q, PREFS)
    order = [(r["company"], r["role"]) for r in ranked(q)]
    # Newest listing first; saved breaks ties; tracker-only saves (no listing age) last.
    assert order[0][0] in {"Tymer", "Acme"} and order[-1] == ("QRW", "FPGA Intern")
    assert order.index(("QTX", "Software Engineering Intern")) < order.index(("QRW", "FPGA Intern"))
    status = {r["company"]: (r["status"], r["note"]) for r in q.rows()}
    # APPLIED wins over SAVED for the same Ironvale job: not added.
    assert "Ironvale" not in status
    assert status["Examdisk"] == ("skipped", "targeting: past season (2026)")
    assert "Simplify" not in status, "non-HTTPS tutorial entry is ignored"


def test_later_sync_keeps_tracker_jobs_listed(tmp_path):
    q = queue(tmp_path)
    import_tracker(q, read_tracker(io.StringIO(TRACKER)))
    q.ingest(list(parse_list(LISTING)))
    apply_targeting(q, PREFS)
    assert {r["company"]: r["status"] for r in q.rows()}["QRW"] == "queued"


def test_duplicate_listings_of_one_job_are_applied_once(tmp_path):
    q = queue(tmp_path)
    apply_targeting(q, PREFS)
    tyler = [(r["status"], r["note"]) for r in q.rows() if r["company"] == "Tymer"]
    assert sorted(tyler) == [("queued", ""), ("skipped", "targeting: duplicate listing")]


def test_workday_repost_suffix_is_the_same_job():
    # Upvale R-200759 was reposted under Acmera as R-200759-1.
    assert job_key("https://upvale.wd1.myworkdayjobs.com/Upvale/job/Draper-UT/Software-Engineer-Intern_R-200759-1") == \
        job_key("https://upvale.wd1.myworkdayjobs.com/Upvale/job/Draper-UT/Software-Engineer-Intern_R-200759")
    assert job_key("https://upvale.wd1.myworkdayjobs.com/x/job/P/Intern_R-200761") != \
        job_key("https://upvale.wd1.myworkdayjobs.com/x/job/P/Intern_R-200762")
