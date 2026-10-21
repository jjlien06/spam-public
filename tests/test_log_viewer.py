import json
import threading
from http.client import HTTPConnection

import pytest

from job_agent.log_viewer import job_detail, make_server, read_log
from job_agent.queue import Queue


def add_job(queue, job_id, company="Acme", role="Software Intern", status="queued"):
    queue.ingest([{"id": job_id, "company": company, "role": role, "location": "Remote",
                   "url": f"https://example.test/jobs/{job_id}", "platform": "generic"}])
    queue.mark(job_id, status)


def test_read_log_joins_company_and_orders_newest_first(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    add_job(queue, "a", "Acme", status="review")
    add_job(queue, "b", "Beta", status="needs_input")
    queue.mark("a", "submitted", "Confirmation: thanks.test")
    rows = read_log(tmp_path)
    assert [r["job_id"] for r in rows] == ["a", "b", "a"]
    assert rows[0]["new_status"] == "submitted" and rows[0]["note"] == "Confirmation: thanks.test"
    assert rows[0]["company"] == "Acme" and rows[0]["role"] == "Software Intern"


def test_read_log_filters_by_status_and_company(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    add_job(queue, "a", "Acme", status="review")
    add_job(queue, "b", "Beta", status="needs_input")
    assert [r["job_id"] for r in read_log(tmp_path, status="needs_input")] == ["b"]
    assert [r["job_id"] for r in read_log(tmp_path, company="Acme")] == ["a"]


def test_read_log_returns_nothing_before_the_queue_exists(tmp_path):
    assert read_log(tmp_path) == []


def test_job_detail_reads_run_artifacts_when_present(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    add_job(queue, "a", "Acme", status="needs_input")
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "a.log").write_text("Selected: Country\n")
    (runs / "a.review.txt").write_text("Review page text")
    (runs / "a.json").write_text(json.dumps({"status": "needs_input", "missing": ["X"]}))
    job = job_detail(tmp_path, "a")
    assert job["company"] == "Acme"
    assert job["log"] == "Selected: Country\n"
    assert job["review"] == "Review page text"
    assert job["report"] == {"status": "needs_input", "missing": ["X"]}


def test_job_detail_omits_missing_artifacts(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    add_job(queue, "a", "Acme")
    (tmp_path / "runs").mkdir()
    job = job_detail(tmp_path, "a")
    assert "log" not in job and "review" not in job and "report" not in job


def test_job_detail_rejects_unknown_or_path_like_ids(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    add_job(queue, "a", "Acme")
    (tmp_path / "runs").mkdir()
    with pytest.raises(ValueError, match="Unknown job"):
        job_detail(tmp_path, "missing")
    with pytest.raises(ValueError, match="Unknown job"):
        job_detail(tmp_path, "../evil")
    with pytest.raises(ValueError, match="Unknown job"):
        job_detail(tmp_path, "")


@pytest.fixture
def local_viewer(tmp_path):
    queue = Queue(tmp_path / "jobs.sqlite3")
    add_job(queue, "a", "Acme <img src=x onerror=alert(1)>", status="review")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "a.log").write_text("Selected: Country\n")
    server = make_server(tmp_path, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        queue.db.close()


def test_local_http_rejects_foreign_origins_and_unknown_paths(local_viewer):
    server, state = local_viewer
    conn = HTTPConnection("127.0.0.1", server.server_port)
    conn.request("GET", "/", headers={"Host": "attacker.test"})
    response = conn.getresponse(); assert response.status == 403; response.read()
    conn.request("GET", "/api/log", headers={"Origin": "https://attacker.test"})
    response = conn.getresponse(); assert response.status == 403; response.read()
    conn.request("GET", "/")
    response = conn.getresponse(); html = response.read().decode()
    assert "no-store" == response.getheader("Cache-Control")
    assert "__NONCE__" not in html
    conn.request("GET", "/profile.json")
    response = conn.getresponse(); assert response.status == 404; response.read()
    conn.close()


def test_api_log_and_job_endpoints(local_viewer):
    server, state = local_viewer
    conn = HTTPConnection("127.0.0.1", server.server_port)
    conn.request("GET", "/api/log")
    response = conn.getresponse(); data = json.loads(response.read())
    assert len(data["rows"]) == 1 and data["rows"][0]["job_id"] == "a"
    conn.request("GET", "/api/log?status=submitted")
    response = conn.getresponse(); assert json.loads(response.read())["rows"] == []
    conn.request("GET", "/api/job?id=a")
    response = conn.getresponse(); job = json.loads(response.read())
    assert job["log"] == "Selected: Country\n"
    conn.request("GET", "/api/job?id=nope")
    response = conn.getresponse(); assert response.status == 404
    conn.close()


def test_log_viewer_browser_loads_filters_and_drills_into_a_job(local_viewer):
    from playwright.sync_api import sync_playwright
    server, state = local_viewer
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(f"http://127.0.0.1:{server.server_port}/")
        page.get_by_text("1 entry").wait_for()
        assert page.locator("img").count() == 0
        page.get_by_role("row").nth(1).click()
        page.get_by_text("Run log").wait_for()
        assert page.locator("#detail-log-text").inner_text() == "Selected: Country\n"
        page.select_option("#status-filter", "review")
        page.get_by_text("1 entry").wait_for()
        page.select_option("#status-filter", "")
        page.select_option("#company-filter", label="Acme <img src=x onerror=alert(1)>")
        page.get_by_text("1 entry").wait_for()
        browser.close()
