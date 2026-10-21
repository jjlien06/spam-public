"""The Setup page on 127.0.0.1:8771: resume → AI draft → confirm each group → targeting → login → Gmail."""
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ai, setup


def state():
    return {**setup.status(), "checks": setup.checks(), "providers": ai.providers(), "provider": ai.provider()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_only(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        return host in ("127.0.0.1", "localhost") and (not origin or origin.split("//")[-1].split(":")[0] in ("127.0.0.1", "localhost"))

    def do_GET(self):
        if not self.local_only():
            return self.send_json({"error": "forbidden"}, 403)
        if self.path == "/":
            body = Path(__file__).with_name("setup.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            self.send_json(state())
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.local_only():
            return self.send_json({"error": "forbidden"}, 403)
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            if self.path == "/api/provider":
                ai.save_provider(data.get("provider"))
                return self.send_json({"ok": True})
            if self.path == "/api/draft":
                return self.send_json({"draft": setup.draft_from_resume(data.get("resume", ""))})
            if self.path == "/api/confirm":
                return self.send_json(setup.confirm(data.get("values") or {}))
            if self.path == "/api/targeting":
                return self.send_json(setup.save_targeting(data.get("targeting") or {}))
            if self.path == "/api/account":
                return self.send_json(setup.save_account(data.get("email", ""), data.get("password", "")))
        except (ValueError, FileNotFoundError, ai.AIError) as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json({"error": "not found"}, 404)


def serve(port=8771, open_page=True):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Setup page: {url}  (Ctrl-C when done)")
    if open_page:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
