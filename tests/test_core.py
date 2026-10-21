from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import pytest
from job_agent.queue import Queue, parse_list, platform
from job_agent.profile import Profile, audit_profile
from job_agent.accounts import verification_candidates


def test_import_dedup_and_resume(tmp_path):
    html = '''<table><tr><td>Company</td><td>Engineer</td><td>NYC</td><td><a href="https://jobs.ashbyhq.com/company/123?utm_source=x"><img alt="Apply"></a></td></tr>
    <tr><td>↳</td><td>Data</td><td>Boston</td><td><a href="https://jobs.ashbyhq.com/company/456"><img alt="Apply"></a></td></tr></table>'''
    jobs = list(parse_list(html))
    assert jobs[1]["company"] == "Company"
    q = Queue(tmp_path / "jobs.db")
    assert q.ingest(jobs) == 2
    q.mark(jobs[0]["id"], "needs_review")
    assert q.ingest(jobs) == 0
    assert q.rows()[0]["status"] == "needs_review"
    assert "utm_" not in q.rows()[0]["url"]


def test_platform_domain_boundary():
    assert platform("https://evilgreenhouse.io/job") == "generic"
    assert platform("https://boards.greenhouse.io/job") == "greenhouse"


def test_profile_known_unknown_and_conflict():
    profile = Profile({"answers": {"First name": {"value": "Alex", "aliases": ["Given name"]}, "sponsorship": None}})
    assert profile.lookup("Given name *") == "Alex"
    assert profile.lookup("sponsorship") is None
    assert profile.lookup("Preferred name") is None
    with pytest.raises(ValueError):
        Profile({"answers": {"Email": "a", "email": "b"}})


def test_profile_lookup_ignores_terminal_period_in_displayed_label():
    profile = Profile({"answers": {"I am fluent in this language": True}})
    assert profile.lookup("I am fluent in this language.") is True
    assert profile.lookup("I am fluent in this language.", choice=True) is True


def test_mail_correlation_and_bad_links():
    now = datetime.now(timezone.utc)
    msg = EmailMessage()
    msg["Date"] = format_datetime(now)
    msg["To"] = "applicant@gmail.com"
    msg["From"] = "accounts@example.com"
    msg.set_content('Activation code: 123456 https://example.com/activate?t=abc https://example.com.evil.org/reset')
    result = verification_candidates(msg, "applicant@gmail.com", ["example.com"], ["example.com"], now - timedelta(minutes=5))
    assert {x["kind"] for x in result} == {"code", "link"}
    assert all("evil" not in x["value"] for x in result)
    assert verification_candidates(msg, "other@gmail.com", ["example.com"], ["example.com"], now - timedelta(minutes=5)) == []
    assert verification_candidates(msg, "applicant@gmail.com", ["example.com"], ["example.com"], now + timedelta(minutes=1)) == []


def test_profile_pattern_answers_apply_unless_exception_company_named():
    profile = Profile({
        "answers": {},
        "patterns": [{"match": r"previously employed|ever worked (?:for|at)", "value": "No",
                      "unless_mentions": ["Example State University", "Acme Robotics", "Corner Cafe"]}],
    })
    assert profile.lookup("Were you previously employed at Red Maple Bank?*", choice=True) == "No"
    assert profile.lookup("Have you ever worked for Acme Corp?", choice=True) == "No"
    # Naming a real past employer must stop for the user instead of guessing.
    assert profile.lookup("Were you previously employed at Acme Robotics?", choice=True) is None
    assert profile.lookup("Have you ever worked at Corner Cafe?", choice=True) is None
    assert profile.lookup("Do you know anyone who works here?", choice=True) is None
    # Patterns never answer free-text controls.
    assert profile.lookup("Were you previously employed at Red Maple Bank?") is None


def test_profile_detail_exposes_answer_provenance_and_audit_blocks_unknowns():
    profile = Profile({"answers": {"Email": {"value": "a@example.com", "aliases": ["Email address"]}},
                       "patterns": [{"match": "previously employed", "value": "No"}]})
    assert profile.lookup_detail("Email address") == {"value": "a@example.com", "confidence": "explicit_saved", "source": "answers"}
    assert profile.lookup_detail("Were you previously employed at X?", choice=True)["confidence"] == "approved_pattern"
    assert profile.lookup_detail("Veteran status")["confidence"] == "unknown"
    report = audit_profile({"answers": {"Veteran Status": {"value": None, "aliases": []}}})
    assert report["status"] == "needs_review"


def test_platform_lever_and_embedded_greenhouse():
    assert platform("https://jobs.lever.co/acme/1234-abcd") == "lever"
    assert platform("https://jobs.eviljobs.lever.co.evil.com/x") == "generic"
    # A company careers page that embeds a Greenhouse board (gh_jid) is a Greenhouse job.
    assert platform("https://www.acme.com/careers/job?gh_jid=8002989") == "greenhouse"


def test_embedded_greenhouse_resolves_to_board_url():
    from job_agent.queue import greenhouse_board_url
    seen = []

    def fetch(url):
        seen.append(url)
        return url.endswith("/boards/acmetrading/jobs/8002989")

    url = greenhouse_board_url("https://www.acmetrading.com/hr/job?gh_jid=8002989", "Acme Trading", fetch)
    assert url == "https://job-boards.greenhouse.io/acmetrading/jobs/8002989"
    assert greenhouse_board_url("https://boards.greenhouse.io/acme/jobs/1", "Acme", fetch) is None
    assert greenhouse_board_url("https://www.nowhere.com/job?gh_jid=5", "Nobody", lambda u: False) is None


def test_queue_resolves_platforms_and_embedded_boards(tmp_path):
    q = Queue(tmp_path / "jobs.db")
    q.ingest([{"id": "a", "company": "Acme Trading", "role": "SWE Intern", "location": "", "platform": "generic",
               "url": "https://www.acmetrading.com/hr/job?gh_jid=77"},
              {"id": "b", "company": "Lev", "role": "SWE Intern", "location": "", "platform": "generic",
               "url": "https://jobs.lever.co/lev/1111-2222"}])
    changed = q.resolve_platforms(lambda u: "/boards/acmetrading/jobs/77" in u)
    rows = {r["id"]: r for r in q.rows()}
    assert changed == 2
    assert rows["a"]["platform"] == "greenhouse"
    assert rows["a"]["url"] == "https://job-boards.greenhouse.io/acmetrading/jobs/77"
    assert rows["a"]["job_key"] == "greenhouse:77"  # same identity as before, so no duplicate
    assert rows["b"]["platform"] == "lever"
    assert q.resolve_platforms(lambda u: False) == 0
