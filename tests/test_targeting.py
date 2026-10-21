from job_agent.queue import Queue, parse_list
from job_agent.targeting import apply_targeting, ranked, reason_skipped

APPLY = '<td><div align="center"><a href="{url}"><img src="x" alt="Apply"></a></div></td>'


def row(company, role, location, url, age):
    return f"<tr><td>{company}</td><td>{role}</td><td>{location}</td>{APPLY.format(url=url)}<td>{age}</td></tr>"


LIST = f"""## 💻 Software Engineering Internship Roles
<table><tbody>
{row('<strong>Kodig</strong>', 'Software Engineer Intern', 'Nashville, TN<br>Richmond, VA', 'https://kodig.com/job?gh_jid=1', '0d')}
{row('🔥 <strong>Wayvo</strong>', 'Software Engineer Intern - MS/PhD 🎓', 'Mountain View, CA', 'https://careers.withwayvo.example/jobs?gh_jid=2', '3d')}
{row('<strong>Maplewood</strong>', 'Developer Intern', 'Toronto, ON, Canada<br>London, UK', 'https://jobs.ashbyhq.com/maplewood/3', '1d')}
</tbody></table>
## 🔧 Hardware Engineering Internship Roles
<table><tbody>
{row('🔥 <strong>Voltra</strong>', 'Firmware Intern 🇺🇸', 'Toronto, ON, Canada<br>Palo Alto, CA', 'https://voltra.wd1.myworkdayjobs.com/x/job/4', '1mo')}
{row('<strong>Acme</strong>', 'Robotics Intern', 'NYC', 'https://acme.wd5.myworkdayjobs.com/x/job/5', '0d')}
</tbody></table>"""

PREFS = {"categories": ["Software Engineering", "Hardware Engineering"], "us_only": True, "skip_advanced_degree": True}


def test_parse_list_keeps_category_markers_age_and_locations():
    jobs = {j["company"]: j for j in parse_list(LIST)}
    assert jobs["Kodig"]["category"] == "Software Engineering"
    assert jobs["Kodig"]["locations"] == "Nashville, TN; Richmond, VA"
    assert jobs["Kodig"]["age_days"] == 0
    assert jobs["Wayvo"]["company"] == "Wayvo"
    assert set(jobs["Wayvo"]["flags"]) == {"🔥", "🎓"}
    assert jobs["Voltra"]["category"] == "Hardware Engineering"
    assert jobs["Voltra"]["age_days"] == 30


def test_skip_reasons_are_conservative():
    jobs = {j["company"]: j for j in parse_list(LIST)}
    assert reason_skipped(jobs["Kodig"], PREFS) is None
    assert reason_skipped(jobs["Wayvo"], PREFS) == "advanced degree required"
    assert reason_skipped(jobs["Maplewood"], PREFS) == "no US location"
    # One US location among foreign ones keeps the job; NYC is US.
    assert reason_skipped(jobs["Voltra"], PREFS) is None
    assert reason_skipped(jobs["Acme"], PREFS) is None
    assert reason_skipped(jobs["Kodig"], {**PREFS, "categories": ["Hardware Engineering"]}) == "category not selected"


def test_targeting_skips_only_queued_rows_and_restores_when_prefs_change(tmp_path):
    q = Queue(tmp_path / "jobs.db")
    jobs = {j["company"]: j for j in parse_list(LIST)}
    q.ingest(list(jobs.values()))
    q.mark(jobs["Wayvo"]["id"], "submitted")
    summary = apply_targeting(q, PREFS)
    status = {r["company"]: (r["status"], r["note"]) for r in q.rows()}
    assert status["Wayvo"][0] == "submitted", "never overrides a job already worked on"
    assert status["Maplewood"] == ("skipped", "targeting: no US location")
    assert summary["eligible"] == 3
    apply_targeting(q, {**PREFS, "us_only": False})
    assert {r["company"]: r["status"] for r in q.rows()}["Maplewood"] == "queued"


def test_rows_missing_from_a_new_sync_are_delisted_and_skipped(tmp_path):
    q = Queue(tmp_path / "jobs.db")
    jobs = list(parse_list(LIST))
    q.ingest(jobs)
    q.ingest([j for j in jobs if j["company"] != "Acme"])
    apply_targeting(q, PREFS)
    status = {r["company"]: (r["status"], r["note"]) for r in q.rows()}
    assert status["Acme"] == ("skipped", "targeting: no longer listed")


def test_ranked_orders_newest_first_then_faang_then_list_order(tmp_path):
    q = Queue(tmp_path / "jobs.db")
    q.ingest(list(parse_list(LIST)))
    apply_targeting(q, PREFS)
    assert [r["company"] for r in ranked(q)] == ["Kodig", "Acme", "Voltra"]


def test_us_places_with_foreign_words_are_us():
    from job_agent.targeting import foreign
    assert not foreign("Albuquerque, New Mexico")
    assert not foreign("Santa Fe, NM")
    assert foreign("Mexico City, Mexico")
    assert foreign("Markham, ON, Canada")


def test_excluded_companies_are_skipped_case_insensitively():
    jobs = {j["company"]: j for j in parse_list(LIST)}
    assert reason_skipped(jobs["Acme"], {**PREFS, "exclude_companies": ["acme"]}) == "company excluded"
    assert reason_skipped(jobs["Kodig"], {**PREFS, "exclude_companies": ["acme"]}) is None


def test_titles_outside_the_field_are_skipped():
    prefs = {**PREFS, "skip_title_patterns": [r"\bMBA\b", r"\bPhD\b"], "skip_field_patterns": [r"sustainab", r"\btax\b"],
             "keep_title_patterns": [r"software", r"\bdata\b", r"\bAI\b"]}
    base = {"company": "Acme", "listed": 1, "category": "Software Engineering", "flags": "", "locations": "NYC"}
    assert reason_skipped({**base, "role": "Product Management Intern (MBA)"}, prefs) == "degree level not yours"
    assert reason_skipped({**base, "role": "Software Engineer Intern - PhD"}, prefs) == "degree level not yours"
    assert reason_skipped({**base, "role": "Sustainability Intern"}, prefs) == "role outside your field"
    assert reason_skipped({**base, "role": "AI Intern - Tax Technology"}, prefs) is None
    assert reason_skipped({**base, "role": "Data-Driven Sustainability Intern"}, prefs) is None
    assert reason_skipped({**base, "role": "Software Engineer Intern"}, prefs) is None


def test_only_required_role_types_are_kept():
    prefs = {**PREFS, "require_title_patterns": [r"software", r"developer", r"\bquant"],
             "reject_title_patterns": [r"product manag", r"business"]}
    base = {"company": "Acme", "listed": 1, "category": "Software Engineering", "flags": "", "locations": "NYC"}
    assert reason_skipped({**base, "role": "Software Developer Intern"}, prefs) is None
    assert reason_skipped({**base, "role": "Quantitative Research Intern"}, prefs) is None
    assert reason_skipped({**base, "role": "Industrial Engineering Intern"}, prefs) == "not a role type you want"
    assert reason_skipped({**base, "role": "Software Product Manager Intern"}, prefs) == "not a role type you want"
    assert reason_skipped({**base, "role": "Business Software Analyst"}, prefs) == "not a role type you want"


def test_priority_companies_become_manual_and_are_never_picked(tmp_path):
    q = Queue(tmp_path / "jobs.db")
    q.ingest(list(parse_list(LIST)))
    prefs = {**PREFS, "manual_companies": ["KODIG"]}
    apply_targeting(q, prefs)
    kodig = next(r for r in q.rows() if r["company"] == "Kodig")
    assert kodig["status"] == "manual"
    assert "Kodig" not in [j["company"] for j in ranked(q)]
    apply_targeting(q, PREFS)  # dropped from the list: back in the queue
    assert next(r for r in q.rows() if r["company"] == "Kodig")["status"] == "queued"


def test_other_listings_of_an_applied_role_are_skipped(tmp_path):
    from job_agent.targeting import role_key
    q = Queue(tmp_path / "jobs.sqlite3")
    q.ingest([{"id": "a", "company": "QTX", "role": "Software Engineer Intern (Summer 2027)", "location": "Fort Wayne, IN",
               "url": "https://qtxhr.wd5.myworkdayjobs.com/x/job/City-A/SWE_09000001", "platform": "workday",
               "category": "Software Engineering", "locations": "Fort Wayne, IN"},
              {"id": "b", "company": "QTX", "role": "Software Engineer Intern", "location": "City-B, MA",
               "url": "https://qtxhr.wd5.myworkdayjobs.com/x/job/City-B/SWE_09000003", "platform": "workday",
               "category": "Software Engineering", "locations": "City-B, MA"},
              {"id": "c", "company": "QTX", "role": "Software Engineer Intern - Radar Systems", "location": "City-C, MA",
               "url": "https://qtxhr.wd5.myworkdayjobs.com/x/job/City-C/SWE_01877000", "platform": "workday",
               "category": "Software Engineering", "locations": "City-C, MA"}])
    q.mark("a", "submitted", "confirmed")
    apply_targeting(q, PREFS)
    assert {r["id"]: r["status"] for r in q.rows()}["b"] == "queued"  # off by default: different postings are fine
    apply_targeting(q, {**PREFS, "one_application_per_role": True})
    status = {r["id"]: (r["status"], r["note"]) for r in q.rows()}
    assert status["b"] == ("skipped", "targeting: same role already applied at this company")
    assert status["c"][0] == "queued"  # a different team is a different role
    assert role_key({"company": "QTX", "role": "Software Engineer Intern (Summer 2027)"}) == ("qtx", "software engineer")
