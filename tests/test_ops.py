import json

from job_agent import ops
from job_agent.queue import Queue


def job(i, company, role="Software Engineer Intern", platform="workday", status="queued", age=0):
    return {"id": f"{i:020d}", "company": company, "role": role, "location": "NYC", "platform": platform,
            "url": f"https://x{i}.wd1.myworkdayjobs.com/c/job/NYC/R{i}", "category": "Software Engineering",
            "locations": "NYC", "age_days": age, "status": status}


def setup(tmp_path, rows, profile=None):
    state = tmp_path / "state"
    state.mkdir()
    queue = Queue(state / "jobs.sqlite3")
    queue.ingest(rows)
    for row in rows:
        if row["status"] != "queued":
            queue.mark(row["id"], row["status"], "x")
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile or {"targeting": {"manual_companies_before_2026_09_23": ["BigCo"]}, "answers": {}}))
    return state, path


def test_pick_one_per_company_cap_four_and_big_tech_uncapped(tmp_path):
    rows = [job(1, "Acme"), job(2, "Acme"), job(3, "Busy"), job(4, "Full")] + \
           [job(10 + i, "Full", status="submitted") for i in range(4)] + [job(20, "BigCo"), job(21, "BigCo"),
           job(30, "Waiting"), job(31, "Waiting", status="needs_input")]
    state, profile = setup(tmp_path, rows)
    picked = [j["company"] for j in ops.pick(state, profile, count=10)]
    assert picked.count("Acme") == 1          # one per company per batch
    assert "Full" not in picked               # four already applied
    assert picked.count("BigCo") == 2         # big tech has no cap
    assert "Waiting" not in picked            # a job there is still waiting on an answer


def test_answer_saves_exact_and_pattern_rules(tmp_path):
    state, profile = setup(tmp_path, [job(1, "Acme")], {"answers": {}, "patterns": []})
    ops.answer(profile, "Are you over 18?", "Yes", note="user 2026-09-24: adult")
    ops.answer(profile, "any", [{"matches": "^yes$"}], pattern=r"over 18")
    data = json.loads(profile.read_text())
    assert data["answers"]["Are you over 18?"] == {"value": "Yes", "aliases": [], "note": "user 2026-09-24: adult"}
    assert data["patterns"][0]["match"] == "over 18"


def test_hold_only_from_review_and_confirm_records_evidence(tmp_path):
    state, profile = setup(tmp_path, [job(1, "Acme", status="review"), job(2, "Busy")])
    ops.hold(state, f"{1:020d}", "wrong graduation date")
    row = Queue(state / "jobs.sqlite3").db.execute("SELECT status, note FROM jobs WHERE id=?", (f"{1:020d}",)).fetchone()
    assert row["status"] == "needs_input" and "wrong graduation date" in row["note"]
    try:
        ops.hold(state, f"{2:020d}", "not at review")
        assert False, "a queued job cannot be held"
    except SystemExit:
        pass
    ops.confirm(state, f"{1:020d}", "Acme: Thank you for applying", "2026-09-24T10:00:00")
    saved = json.loads((state / "confirmations.json").read_text())
    assert saved[f"{1:020d}"] == {"at": "2026-09-24T10:00:00", "email": "Acme: Thank you for applying"}


def test_decisions_lists_only_answers_the_user_made(tmp_path):
    state, profile = setup(tmp_path, [job(1, "Acme")], {"answers": {
        "SAT Score": {"value": "1450", "note": "Alex, 2026-09-23 question round 4"},
        "Phone": {"value": "x", "note": "from resume"}},
        "patterns": [{"match": "co-?op", "value": "Yes", "note": "user 2026-01-01: fall term OK"}],
        "targeting": {"note_role_types": "any role type"}, "facts": {"name": "Alex Rivera"}})
    found = ops.decisions(profile)
    assert [d["question"] for d in found] == ["SAT Score", "co-?op", "note_role_types"]
    assert [d["question"] for d in ops.decisions(profile, "fall term")] == ["co-?op"]


def test_cap_counts_one_employer_across_name_spellings(tmp_path):
    rows = [job(1, "Veemo"), job(2, "Veemo Software", status="submitted"), job(3, "Veemo Software", status="submitted"),
            job(4, "Veemo Software, Inc.", status="submitted"), job(5, "Veemo", status="submitted"), job(6, "Acme")]
    state, profile = setup(tmp_path, rows)
    assert [j["company"] for j in ops.pick(state, profile, count=10)] == ["Acme"]


def test_review_report_checks_against_the_postings_variant_answers(tmp_path):
    profile = {"answers": {"When can you join us?": {"value": "May 17, 2027", "aliases": [], "variants": [
        {"when": r"\bwinter 2027\b", "value": "January 4, 2027"}]}}, "patterns": []}
    state, path = setup(tmp_path, [job(1, "Acme", role="Software Intern (Winter 2027)", status="review")], profile)
    (state / "runs").mkdir(exist_ok=True)
    (state / "runs" / f"{1:020d}.review.txt").write_text("When can you join us?\nJanuary 4, 2027\n")
    [report] = ops.review_report(state, path)
    assert report["problems"] == []
