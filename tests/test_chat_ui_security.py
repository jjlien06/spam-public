"""Console request-boundary regressions; only synthetic loopback operations.

Missing Host is forbidden. Missing Origin supports CLI clients, but POST still
requires JSON; an empty or opaque (null) Origin is not a missing Origin.
"""
import http.client
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from job_agent import chat_ui


@pytest.fixture
def loopback_server():
    @contextmanager
    def start(handler):
        # Do not call serve(): it starts real background pull/mail workers.
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            assert not thread.is_alive()

    return start


@pytest.fixture
def console(loopback_server, monkeypatch, tmp_path):
    calls = []

    def dashboard(state):
        calls.append(("dashboard", state))
        return {"fixture": True}

    def approve(state, ids):
        calls.append(("approve", state, ids))
        return {"started": len(ids)}

    monkeypatch.setattr(chat_ui, "dashboard", dashboard)
    monkeypatch.setattr(chat_ui, "approve", approve)

    class FixtureHandler(chat_ui.Handler):
        state = str(tmp_path)

    with loopback_server(FixtureHandler) as server:
        yield SimpleNamespace(server=server, calls=calls, state=str(tmp_path))


def request(server, method, path, headers=(), body=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        # Bypass http.client's automatic Host to exercise absent/duplicate headers.
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("host", [
    None, "", "evil.example:{port}", "127.0.0.1.evil.example:{port}",
    "localhost.evil.example:{port}", "127.0.0.1", "localhost", "127.0.0.1:0",
    "localhost:0", "127.0.0.1:{port}@evil.example", "user@localhost:{port}",
    "127.0.0.1:{port}/", "127.0.0.1:{port},evil.example", "[::1]:{port}",
])
def test_rejects_untrusted_or_missing_host_before_operations(console, host, method):
    headers = [] if host is None else [("Host", host.format(port=console.server.server_port))]
    headers.append(("Content-Type", "application/json"))
    path = "/api/state" if method == "GET" else "/api/approve"
    status, _, _ = request(console.server, method, path, headers, b'{"ids": []}')
    assert status == 403
    assert console.calls == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_rejects_duplicate_host(console, method):
    host = f"127.0.0.1:{console.server.server_port}"
    path = "/api/state" if method == "GET" else "/api/approve"
    status, _, _ = request(console.server, method, path, [
        ("Host", host), ("Host", host), ("Content-Type", "application/json"),
    ], b'{"ids": []}')
    assert status == 403
    assert console.calls == []


@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost"])
def test_get_accepts_loopback_host_at_actual_port_without_origin(console, hostname):
    status, _, body = request(console.server, "GET", "/api/state", [
        ("Host", f"{hostname}:{console.server.server_port}"),
    ])
    assert status == 200
    assert json.loads(body) == {"fixture": True}
    assert console.calls == [("dashboard", console.state)]


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("origin", [
    "", "null", "https://evil.example", "http://localhost.evil.example:{port}",
    "http://127.0.0.1.evil.example:{port}", "http://127.0.0.1:{port}@evil.example",
    "http://user@127.0.0.1:{port}", "http://localhost:{port}",
    "http://127.0.0.1:0", "http://127.0.0.1", "https://127.0.0.1:{port}",
    "http://127.0.0.1:{port}/", "http://127.0.0.1:{port}?x=1",
    "http://127.0.0.1:{port} https://evil.example",
])
def test_rejects_non_same_origin_before_operations(console, method, origin):
    port = console.server.server_port
    headers = [("Host", f"127.0.0.1:{port}"), ("Origin", origin.format(port=port)),
               ("Content-Type", "application/json")]
    path = "/api/state" if method == "GET" else "/api/approve"
    status, _, _ = request(console.server, method, path, headers, b'{"ids": []}')
    assert status == 403
    assert console.calls == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_rejects_duplicate_origin(console, method):
    host = f"127.0.0.1:{console.server.server_port}"
    headers = [("Host", host), ("Origin", f"http://{host}"),
               ("Origin", f"http://{host}"), ("Content-Type", "application/json")]
    path = "/api/state" if method == "GET" else "/api/approve"
    status, _, _ = request(console.server, method, path, headers, b'{"ids": []}')
    assert status == 403
    assert console.calls == []


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost"])
@pytest.mark.parametrize("send_origin", [False, True])
def test_same_origin_and_originless_clients_remain_supported(console, method, hostname, send_origin):
    host = f"{hostname}:{console.server.server_port}"
    headers = [("Host", host), ("Content-Type", "application/json")]
    if send_origin:
        headers.append(("Origin", f"http://{host}"))
    path = "/api/state" if method == "GET" else "/api/approve"
    job_id = "a" * 20
    status, _, body = request(console.server, method, path, headers, json.dumps({"ids": [job_id]}).encode())
    assert status == 200
    if method == "POST":
        assert json.loads(body) == {"started": 1}
        assert console.calls == [("approve", console.state, [job_id])]
    else:
        assert json.loads(body) == {"fixture": True}
        assert console.calls == [("dashboard", console.state)]


@pytest.mark.parametrize("content_type", [
    None, "", "text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=fixture", "application/jsonp", "application/json.evil",
    "text/json", "application/json, text/plain",
])
@pytest.mark.parametrize("send_origin", [False, True])
def test_post_requires_json_media_type_before_mutation(console, content_type, send_origin):
    host = f"127.0.0.1:{console.server.server_port}"
    headers = [("Host", host)]
    if send_origin:
        headers.append(("Origin", f"http://{host}"))
    if content_type is not None:
        headers.append(("Content-Type", content_type))
    # Valid JSON under form-compatible headers used to reach the mutation.
    status, _, _ = request(console.server, "POST", "/api/approve", headers, b'{"ids": []}')
    assert status == 415
    assert console.calls == []


def test_post_rejects_duplicate_content_type(console):
    host = f"127.0.0.1:{console.server.server_port}"
    status, _, _ = request(console.server, "POST", "/api/approve", [
        ("Host", host), ("Content-Type", "application/json"), ("Content-Type", "text/plain"),
    ], b'{"ids": []}')
    assert status == 415
    assert console.calls == []


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8", "Application/JSON"])
def test_post_accepts_json_media_type_with_optional_charset(console, content_type):
    host = f"localhost:{console.server.server_port}"
    status, _, body = request(console.server, "POST", "/api/approve", [
        ("Host", host), ("Origin", f"http://{host}"), ("Content-Type", content_type),
    ], b'{"ids": []}')
    assert status == 200
    assert json.loads(body) == {"started": 0}
    assert console.calls == [("approve", console.state, [])]


@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost"])
def test_chat_json_post_still_streams_sse(console, monkeypatch, hostname):
    launched = []

    def popen(command, **kwargs):
        launched.append((command, kwargs["env"]["SPAM_CHAT_AGENT"]))
        return SimpleNamespace(
            stdout=iter([
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "fixture reply"}}),
                json.dumps({"type": "turn.completed"}),
            ]),
            wait=lambda timeout: 0,
            returncode=0,
        )

    monkeypatch.setattr(chat_ui, "agent_command", lambda *args: ["fixture-agent"])
    monkeypatch.setattr(chat_ui.subprocess, "Popen", popen)
    monkeypatch.setattr(chat_ui, "lock", threading.Lock())
    host = f"{hostname}:{console.server.server_port}"
    status, headers, body = request(console.server, "POST", "/api/chat", [
        ("Host", host), ("Origin", f"http://{host}"), ("Content-Type", "application/json"),
    ], b'{"provider": "codex", "message": "fixture request"}')
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert headers["Cache-Control"] == "no-cache"
    events = [json.loads(line.removeprefix("data: ")) for line in body.decode().splitlines() if line.startswith("data: ")]
    assert events == [{"kind": "text", "text": "fixture reply"}, {"kind": "done", "text": ""}, {"kind": "end"}]
    assert launched == [(["fixture-agent"], "1")]
    assert console.calls == []


@pytest.mark.parametrize("path", ["/", "/assessments", "/hand-submit"])
def test_static_console_pages_remain_accessible_without_origin(console, path):
    status, headers, body = request(console.server, "GET", path, [
        ("Host", f"localhost:{console.server.server_port}"),
    ])
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert b"<html" in body.lower()
    assert console.calls == []


def test_cross_origin_preflight_does_not_enable_cors(console):
    status, headers, _ = request(console.server, "OPTIONS", "/api/approve", [
        ("Host", f"127.0.0.1:{console.server.server_port}"),
        ("Origin", "https://evil.example"),
        ("Access-Control-Request-Method", "POST"),
        ("Access-Control-Request-Headers", "content-type"),
    ])
    assert status == 501
    assert "Access-Control-Allow-Origin" not in headers
    assert console.calls == []


@pytest.mark.parametrize("send_origin", [False, True])
def test_live_demo_inherited_console_routes_remain_compatible(loopback_server, monkeypatch, tmp_path, send_origin):
    from job_agent import live_demo, ops

    job_id = "a" * 20
    calls = []
    demo = SimpleNamespace(token="fixture-token", busy=False,
                           state=lambda: {"ready": True}, start=lambda action: calls.append(("demo", action)))
    monkeypatch.setattr(live_demo.paths, "state", lambda: tmp_path)
    monkeypatch.setattr(live_demo, "selected_job", lambda: {"id": job_id})
    monkeypatch.setattr(chat_ui, "approve", lambda state, ids: calls.append(("approve", ids)) or {"started": 1})
    monkeypatch.setattr(ops, "hold", lambda state, key, reason: calls.append(("hold", key)))
    monkeypatch.setattr(chat_ui, "job_page", lambda state, key: "<html>fixture review</html>")
    with loopback_server(live_demo.handler(demo)) as server:
        # Exercise the unchanged demo's fixed-authority checks on a free port.
        monkeypatch.setattr(live_demo, "PORT", server.server_port)
        host = f"127.0.0.1:{server.server_port}"
        monkeypatch.setattr(live_demo, "ORIGIN", f"http://{host}")
        headers = [("Host", host), ("Content-Type", "application/json"), ("X-Demo-Token", demo.token)]
        if send_origin:
            headers.append(("Origin", f"http://{host}"))
        for path, payload in [
            ("/api/demo/action", {"action": "fill"}),
            ("/api/approve", {"ids": [job_id]}),
            ("/api/hold", {"id": job_id}),
        ]:
            assert request(server, "POST", path, headers, json.dumps(payload).encode())[0] == 200
        status, _, body = request(server, "GET", f"/job/{job_id}", headers)
        assert status == 200
        assert body == b"<html>fixture review</html>"
        assert request(server, "GET", "/demo-health", [("Host", f"localhost:{server.server_port}")])[0] == 403
    assert calls == [("demo", "fill"), ("approve", [job_id]), ("hold", job_id)]
