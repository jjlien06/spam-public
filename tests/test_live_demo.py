"""Mock-only guards for the focused live-application demo."""
import io
import json
from types import SimpleNamespace

import pytest

from job_agent import chat_ui, live_demo


JOB_ID = "a" * 20
OTHER_ID = "b" * 20


def _job(**overrides):
    return {"id": JOB_ID, "job_key": "pure:123", "url": "https://jobs.example/apply",
            "company": "Example Employer", "role": "Fictional Intern", "status": "queued",
            "protected": False, **overrides}


def test_selected_job_rejects_changed_configured_url(tmp_path, monkeypatch):
    (tmp_path / "coffee-chat-demo.json").write_text(json.dumps({"job_id": JOB_ID, "url": "https://old.example/"}))
    queue = SimpleNamespace(
        db=SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: _job()), close=lambda: None),
    )
    monkeypatch.setattr(live_demo.paths, "home", lambda: tmp_path)
    monkeypatch.setattr(live_demo.paths, "state", lambda: tmp_path / "state")
    monkeypatch.setattr(live_demo, "Queue", lambda path: queue)
    monkeypatch.setattr(live_demo, "protected_job_keys", lambda *a, **k: set())

    with pytest.raises(ValueError, match="URL changed"):
        live_demo.selected_job()


def test_selected_job_marks_duplicate_requisition_as_protected(tmp_path, monkeypatch):
    job = _job()
    (tmp_path / "coffee-chat-demo.json").write_text(json.dumps({"job_id": JOB_ID, "url": job["url"]}))
    queue = SimpleNamespace(
        db=SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: job), close=lambda: None),
    )
    monkeypatch.setattr(live_demo.paths, "home", lambda: tmp_path)
    monkeypatch.setattr(live_demo.paths, "state", lambda: tmp_path / "state")
    monkeypatch.setattr(live_demo, "Queue", lambda path: queue)
    monkeypatch.setattr(live_demo, "protected_job_keys", lambda *a, **k: {"pure:123"})

    assert live_demo.selected_job()["protected"] is True


@pytest.mark.parametrize("job", [
    _job(status="submitted", protected=True),
    _job(status="possibly_submitted", protected=True),
    _job(status="hand_submit", protected=True),
    _job(status="ready_for_review"),
])
def test_preflight_blocks_protected_or_nonfillable_jobs(tmp_path, monkeypatch, job):
    monkeypatch.setattr(live_demo, "selected_job", lambda: job)
    monkeypatch.setattr("job_agent.ops.workers", lambda: [])

    with pytest.raises(ValueError, match="Do not repeat"):
        live_demo.preflight()


def test_preflight_requires_idle_workers_and_existing_saved_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(live_demo, "selected_job", lambda: _job())
    monkeypatch.setattr(live_demo.paths, "profile", lambda: tmp_path / "profile.json")
    monkeypatch.setattr("job_agent.ops.workers", lambda: [{"pid": 123}])
    (tmp_path / "profile.json").write_text("{}")
    with pytest.raises(ValueError, match="workers are active"):
        live_demo.preflight()

    monkeypatch.setattr("job_agent.ops.workers", lambda: [])
    (tmp_path / "profile.json").write_text(json.dumps({"documents": {"Resume": str(tmp_path / "missing.pdf")}}))
    with pytest.raises(ValueError, match="resume is missing"):
        live_demo.preflight()


@pytest.mark.parametrize("status,busy,error,report_status,problems,expected", [
    ("review", False, None, "ready_for_review", [], True),
    ("review", False, None, "ready_for_review", ["Answer differs"], False),
    ("review", False, None, "needs_input", [], False),
    ("queued", False, None, "ready_for_review", [], False),
    ("review", True, None, "ready_for_review", [], False),
    ("review", False, "paused", "ready_for_review", [], False),
])
def test_state_ready_only_for_clean_review_report(tmp_path, monkeypatch, status, busy, error,
                                                   report_status, problems, expected):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / f"{JOB_ID}.json").write_text(json.dumps({"status": report_status}))
    monkeypatch.setattr(live_demo, "selected_job", lambda: _job(status=status))
    monkeypatch.setattr(live_demo.paths, "state", lambda: tmp_path)
    monkeypatch.setattr(live_demo.paths, "profile", lambda: tmp_path / "profile.json")
    # review_report is imported inside state() from ops.
    monkeypatch.setattr("job_agent.ops.review_report", lambda *a: [{"problems": problems}])
    page = live_demo.LiveDemo()
    page.busy, page.error = busy, error

    assert page.state()["ready"] is expected


class _Headers(dict):
    pass


def _handler(instance, method, path, headers, body=None):
    cls = live_demo.handler(instance)
    request = object.__new__(cls)
    encoded = json.dumps(body or {}).encode()
    request.headers = _Headers({"Content-Length": str(len(encoded)), **headers})
    request.path = path
    request.rfile = io.BytesIO(encoded)
    request.send_json = lambda data, code=200: setattr(request, "response", (data, code))
    request.response = None
    getattr(request, method)()
    return request.response


def test_action_http_requires_exact_host_origin_and_demo_token(monkeypatch):
    page = live_demo.LiveDemo()
    calls = []
    monkeypatch.setattr(page, "start", lambda action: calls.append(action))

    valid = {"Host": "127.0.0.1:8787", "Origin": live_demo.ORIGIN, "X-Demo-Token": page.token}
    assert _handler(page, "do_POST", "/api/demo/action", valid, {"action": "fill"}) == ({"ok": True}, 200)
    assert calls == ["fill"]

    evil_origin = {**valid, "Origin": "http://127.0.0.1.evil:8787"}
    assert _handler(page, "do_POST", "/api/demo/action", evil_origin, {"action": "fill"})[1] == 403
    bad_host = {**valid, "Host": "localhost:8787"}
    assert _handler(page, "do_POST", "/api/demo/action", bad_host, {"action": "fill"})[1] == 403
    bad_token = {**valid, "X-Demo-Token": "wrong"}
    assert _handler(page, "do_POST", "/api/demo/action", bad_token, {"action": "fill"})[1] == 403
    assert calls == ["fill"]


def test_approve_endpoint_is_limited_to_selected_job(monkeypatch):
    page = live_demo.LiveDemo()
    captured = []
    selected = _job()
    monkeypatch.setattr(live_demo, "selected_job", lambda: selected)
    monkeypatch.setattr(page, "state", lambda: {"ready": False})

    def inherited_do_post(handler):
        payload = json.loads(handler.rfile.read().decode())
        captured.append(payload)
        handler.send_json({"approved": payload.get("ids")})

    monkeypatch.setattr(chat_ui.Handler, "do_POST", inherited_do_post)
    headers = {"Host": "127.0.0.1:8787", "Origin": live_demo.ORIGIN}
    response = _handler(page, "do_POST", "/api/approve", headers, {"ids": [JOB_ID]})
    assert response[1] == 400
    page.state = lambda: {"ready": True}
    response = _handler(page, "do_POST", "/api/approve", headers, {"ids": [JOB_ID, OTHER_ID]})
    assert response[1] == 400
    response = _handler(page, "do_POST", "/api/approve", headers, {"ids": [JOB_ID]})
    assert response == ({"approved": [JOB_ID]}, 200)
    assert captured == [{"ids": [JOB_ID]}]


def test_hold_endpoint_is_limited_to_selected_job(monkeypatch):
    page = live_demo.LiveDemo()
    captured = []
    monkeypatch.setattr(live_demo, "selected_job", lambda: _job())

    def inherited_do_post(handler):
        payload = json.loads(handler.rfile.read().decode())
        captured.append(payload)
        handler.send_json({"held": payload.get("id")})

    monkeypatch.setattr(chat_ui.Handler, "do_POST", inherited_do_post)
    headers = {"Host": "127.0.0.1:8787", "Origin": live_demo.ORIGIN}
    response = _handler(page, "do_POST", "/api/hold", headers, {"id": OTHER_ID, "reason": "fixture"})
    assert response[1] == 400
    response = _handler(page, "do_POST", "/api/hold", headers, {"id": JOB_ID, "reason": "fixture"})
    assert response == ({"held": JOB_ID}, 200)
    assert captured == [{"id": JOB_ID, "reason": "fixture"}]


def test_approval_waits_until_live_fill_has_finished(monkeypatch):
    page = live_demo.LiveDemo()
    page.busy = True
    delegated = []
    monkeypatch.setattr(chat_ui.Handler, "do_POST", lambda handler: delegated.append(True))
    headers = {"Host": "127.0.0.1:8787", "Origin": live_demo.ORIGIN}

    assert _handler(page, "do_POST", "/api/approve", headers, {"ids": [JOB_ID]}) == (
        {"error": "Wait until filling finishes."}, 409)
    assert delegated == []
