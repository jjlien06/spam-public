import json

from job_agent import fit, ops
from job_agent.queue import Queue
from tests.test_ops import job, setup

REPORT = "Block A...\n---SCORE_SUMMARY---\nCOMPANY: Acme\nROLE: SWE Intern\nSCORE: 4.2/5\nARCHETYPE: SWE\nLEGITIMACY: High Confidence\n---END_SUMMARY---"


def test_parse_summary():
    assert fit.parse(REPORT) == {"score": 4.2, "archetype": "SWE", "legitimacy": "High Confidence"}
    assert fit.parse("no block")["score"] is None
    assert fit.parse(REPORT.replace("4.2/5", "9"))["score"] is None


def test_score_stores_report_and_never_changes_status(tmp_path):
    state, profile = setup(tmp_path, [job(1, "Acme"), job(2, "Nojd")])
    seen = []
    def ask(prompt, cwd, timeout):
        seen.append(prompt)
        return REPORT
    fetch = lambda url: None if "x2." in url else "JD text " * 50
    results = fit.score_jobs(state, profile, count=5, ask=ask, fetch=fetch, cv="my cv", log=lambda *_: None)
    by = {r["company"]: r for r in results}
    assert by["Acme"]["score"] == 4.2 and by["Nojd"]["score"] is None and "left unscored" in by["Nojd"]["error"]
    assert "my cv" in seen[0] and "untrusted" in seen[0]
    rows = {r["company"]: r for r in Queue(state / "jobs.sqlite3").rows()}
    assert rows["Acme"]["fit_score"] == 4.2 and rows["Acme"]["status"] == "queued"
    assert rows["Nojd"]["fit_score"] is None
    assert (state / "fit" / f"{rows['Acme']['id']}.md").exists()


def test_pick_skips_low_fit_but_keeps_unscored(tmp_path):
    state, profile = setup(tmp_path, [job(1, "Low"), job(2, "High"), job(3, "Unscored")])
    q = Queue(state / "jobs.sqlite3")
    with q.db:
        q.db.execute("UPDATE jobs SET fit_score=2.1 WHERE company='Low'")
        q.db.execute("UPDATE jobs SET fit_score=4.0 WHERE company='High'")
    names = lambda **kw: {j["company"] for j in ops.pick(state, profile, count=10, **kw)}
    assert names() == {"High", "Unscored"}
    assert names(min_fit=None) == {"Low", "High", "Unscored"}
    assert names(min_fit=4.5) == {"Unscored"}


def test_fetch_falls_back_to_rendered_page_and_names_misses(tmp_path):
    (tmp_path / "fetch-jd.mjs").write_text("")
    miss = lambda cmd, cwd: (1, "")
    long = "Software Engineer Intern. Responsibilities: " + "build things. " * 150
    assert fit.fetch_jd("u", tmp_path, miss, render=lambda u: long).startswith("Software Engineer Intern")
    assert fit.fetch_jd("u", tmp_path, miss, render=lambda u: "We are no longer accepting applications.") == "posting looks closed"
    assert fit.fetch_jd("u", tmp_path, miss, render=lambda u: "Performing security verification") == "bot check blocked reading the posting"
    assert fit.fetch_jd("u", tmp_path, lambda c, w: (0, "API JD " * 60), render=None).startswith("API JD")
