from job_agent.batch import pick, queue_status, question_deck, with_company_history
from job_agent.profile import Profile
from job_agent.queue import Queue, parse_list
from job_agent.targeting import apply_targeting

APPLY = '<td><a href="{url}"><img alt="Apply"></a></td>'


def listing(rows):
    return "## 💻 Software Engineering Internship Roles\n<table>" + "".join(
        f"<tr><td>{c}</td><td>{r}</td><td>NYC</td>{APPLY.format(url=u)}<td>{a}</td></tr>" for c, r, u, a in rows) + "</table>"


ROWS = [
    ("Harlow Allen", "Software Developer Intern", "https://hla.wd1.myworkdayjobs.com/x/job/A/Dev_R0000001", "0d"),
    ("Harlow Allen", "Software Developer Intern", "https://hla.wd1.myworkdayjobs.com/x/job/B/Dev_R0000002", "0d"),
    ("Acme", "Software Intern", "https://acme.wd1.myworkdayjobs.com/x/job/C/Intern_R0000003", "1d"),
    ("Greenhouse Co", "Software Intern", "https://boards.greenhouse.io/gh/jobs/12345", "0d"),
    ("Beta", "Software Intern", "https://beta.wd5.myworkdayjobs.com/x/job/D/Intern_R0000004", "2d"),
]
PREFS = {"categories": ["Software Engineering"], "us_only": True, "skip_advanced_degree": True}


def queue(tmp_path):
    q = Queue(tmp_path / "jobs.db")
    q.ingest(list(parse_list(listing(ROWS))))
    apply_targeting(q, PREFS)
    return q


def test_pick_takes_newest_workday_jobs_one_per_company(tmp_path):
    q = queue(tmp_path)
    picked = pick(q, 10, PREFS)
    assert [j["company"] for j in picked] == ["Harlow Allen", "Acme", "Beta"]


def test_pick_respects_the_per_company_cap_and_exclusions(tmp_path):
    q = queue(tmp_path)
    harlow = [j for j in q.rows() if j["company"] == "Harlow Allen"]
    q.mark(harlow[0]["id"], "submitted")
    assert "Harlow Allen" not in [j["company"] for j in pick(q, 10, PREFS, company_cap=1)]
    assert "Acme" not in [j["company"] for j in pick(q, 10, {**PREFS, "exclude_companies": ["acme"]})]


def test_pick_waits_on_a_company_whose_job_needs_input(tmp_path):
    q = queue(tmp_path)
    harlow = [j for j in q.rows() if j["company"] == "Harlow Allen"]
    q.mark(harlow[0]["id"], "needs_input", "{}")
    assert [j["company"] for j in pick(q, 10, PREFS)] == ["Acme", "Beta"]


def test_queue_status_maps_run_reports():
    assert queue_status({"status": "hand_submit", "note": "press Submit"})[0] == "hand_submit"
    assert queue_status({"status": "ready_for_review"})[0] == "review"
    assert queue_status({"status": "already_applied"})[0] == "applied"
    assert queue_status({"status": "posting_closed"})[0] == "closed"
    # Never "review": a retry would submit again on a guest-apply site (FIS).
    assert queue_status({"status": "submit_uncertain"})[0] == "submit_uncertain"
    status, note = queue_status({"status": "submitted", "confirmation": "Workday Job_Application_ID=abc123"})
    assert status == "submitted" and "Job_Application_ID=abc123" in note
    status, note = queue_status({"status": "needs_input", "missing": ["Q1", "Q2"]})
    assert status == "needs_input" and '"Q1"' in note


def test_question_deck_groups_the_same_question_across_jobs():
    deck = question_deck([("Acme", {"status": "needs_input", "missing": ["What is your GPA?", "Degree (saved answer not among options)"]}),
                          ("Beta", {"status": "needs_input", "missing": ["What is your GPA?"]})])
    assert deck[0] == ("What is your GPA?", ["Acme", "Beta"])


def test_prior_application_questions_follow_this_companys_history(tmp_path):
    # LXEG: "Have you already applied for one of our intern programmes?" is Yes after a
    # first application there, No at a company never applied to.
    q = queue(tmp_path)
    harlow = [j for j in q.rows() if j["company"] == "Harlow Allen"]
    acme = next(j for j in q.rows() if j["company"] == "Acme")
    question = "Have you already applied for one of our intern/graduate programmes this academic year?"
    assert Profile(with_company_history({"patterns": []}, q, harlow[1])).lookup(question, choice=True) == "No"
    q.mark(harlow[0]["id"], "submitted")
    assert Profile(with_company_history({"patterns": []}, q, harlow[1])).lookup(question, choice=True) == "Yes"
    assert Profile(with_company_history({"patterns": []}, q, acme)).lookup(question, choice=True) == "No"


def test_prior_application_on_previous_occasions_uses_company_history(tmp_path):
    q = queue(tmp_path)
    harlow = [j for j in q.rows() if j["company"] == "Harlow Allen"]
    question = "Have you applied on any previous occasions for employment in any capacity with Harlow Allen?"
    assert Profile(with_company_history({"patterns": []}, q, harlow[1])).lookup(question, choice=True) == "No"
    q.mark(harlow[0]["id"], "submitted", "submitted")
    assert Profile(with_company_history({"patterns": []}, q, harlow[1])).lookup(question, choice=True) == "Yes"


def test_prior_applications_in_the_simplify_tracker_count_too(tmp_path):
    q = queue(tmp_path)
    acme = next(j for j in q.rows() if j["company"] == "Acme")
    q.db.execute("INSERT INTO tracked (job_key, status, company, title) VALUES ('other:1', 'APPLIED', 'ACME', 'Data Intern')")
    question = "Have you previously applied to a position with us?"
    assert Profile(with_company_history({"patterns": []}, q, acme)).lookup(question, choice=True) == "Yes"


def test_company_specific_answers_are_dropped_for_other_jobs():
    from job_agent.batch import scope_answers
    profile = {"answers": {"Why are you interested in this opportunity?": {"value": "The AI Development internship...", "only_for": r"^Acme Cooperative \| .*AI Development"},
                           "Are you over 18?": {"value": "Yes"}}}
    scope_answers(profile, {"company": "Acme Cooperative", "role": "Data Migration Intern"})
    assert "Why are you interested in this opportunity?" not in profile["answers"] and "Are you over 18?" in profile["answers"]
    kept = {"answers": {"Why": {"value": "x", "only_for": r"^Acme Cooperative \| .*AI Development"}}}
    scope_answers(kept, {"company": "Acme Cooperative", "role": "Software Development Intern - AI Development"})
    assert "Why" in kept["answers"]


def test_answer_variants_apply_to_matching_postings():
    from job_agent.batch import scope_answers
    profile = {"answers": {"Start month": {"value": "May", "variants": [{"when": r"spring 2027", "value": "January"}]}}}
    scope_answers(profile, {"company": "OrbitX", "role": "Spring 2027 Software Engineering Internship/Co-op"})
    assert profile["answers"]["Start month"]["value"] == "January"
    summer = {"answers": {"Start month": {"value": "May", "variants": [{"when": r"spring 2027", "value": "January"}]}}}
    scope_answers(summer, {"company": "OrbitX", "role": "Software Engineering Intern/Co-op"})
    assert summer["answers"]["Start month"]["value"] == "May"


def test_the_cap_counts_applications_from_the_simplify_tracker_too(tmp_path):
    q = queue(tmp_path)
    q.db.execute("INSERT INTO tracked (job_key, status, company, title, url) VALUES ('greenhouse:1', 'APPLIED', 'Acme', 'Old Intern', 'https://x')")
    q.db.commit()
    assert "Acme" not in [j["company"] for j in pick(q, 10, PREFS, company_cap=1)]
    assert "Acme" in [j["company"] for j in pick(q, 10, PREFS, company_cap=2)]
