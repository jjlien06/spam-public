"""Local, read-only view of the status log. Never opens, fills, or submits an application."""
import json
import re
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

JOB_ID = re.compile(r"[A-Za-z0-9_-]+")
DEFAULT_LIMIT = 1000


def _connect(state):
    db_path = Path(state) / "jobs.sqlite3"
    if not db_path.exists():
        return None
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def read_log(state, status=None, company=None, limit=DEFAULT_LIMIT):
    """Every status/note change, newest first, with the job's current company/role/platform."""
    db = _connect(state)
    if db is None:
        return []
    try:
        query = ("SELECT status_log.rowid AS log_id, status_log.job_id, status_log.old_status,"
                 " status_log.new_status, status_log.note, status_log.at,"
                 " jobs.company, jobs.role, jobs.platform"
                 " FROM status_log JOIN jobs ON jobs.id = status_log.job_id WHERE 1=1")
        params = []
        if status:
            query += " AND status_log.new_status = ?"
            params.append(status)
        if company:
            query += " AND jobs.company = ?"
            params.append(company)
        query += " ORDER BY status_log.rowid DESC LIMIT ?"
        params.append(max(1, min(limit, DEFAULT_LIMIT)))
        return [dict(row) for row in db.execute(query, params)]
    finally:
        db.close()


def job_detail(state, job_id):
    """The job's record plus whatever local run artifacts still exist for it."""
    if not job_id or not JOB_ID.fullmatch(job_id):
        raise ValueError("Unknown job id.")
    db = _connect(state)
    if db is None:
        raise ValueError("Unknown job id.")
    try:
        row = db.execute("SELECT id, company, role, url, platform, status, note FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        raise ValueError("Unknown job id.")
    job = dict(row)
    runs = (Path(state) / "runs").resolve()
    for key, suffix in (("log", ".log"), ("review", ".review.txt"), ("report", ".json")):
        path = (runs / (job_id + suffix))
        if path.parent.resolve() != runs:  # job ids come only from the database; still never read outside runs/
            continue
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        job[key] = json.loads(text) if key == "report" else text
    return job


def make_server(state, port=8766):
    """Loopback only; no file serving beyond the page and no write endpoints."""
    nonce = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Job details never go to access logs.

        def respond(self, status, data, content_type="application/json"):
            body = data.encode() if isinstance(data, str) else json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-" + nonce + "'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = "127.0.0.1:" + str(self.server.server_port)
            return self.headers.get("Host") == host and self.headers.get("Origin", "http://" + host) == "http://" + host

        def do_GET(self):
            if not self.allowed():
                return self.respond(403, {"error": "Open the local URL printed in the terminal."})
            from urllib.parse import urlsplit, parse_qs
            path, query = urlsplit(self.path).path, parse_qs(urlsplit(self.path).query)
            if path == "/":
                html = Path(__file__).with_name("log_viewer.html").read_text().replace("__NONCE__", nonce)
                return self.respond(200, html, "text/html")
            if path == "/api/log":
                try:
                    limit = int(query.get("limit", [DEFAULT_LIMIT])[0])
                except ValueError:
                    return self.respond(400, {"error": "limit must be an integer."})
                rows = read_log(state, status=query.get("status", [None])[0], company=query.get("company", [None])[0], limit=limit)
                return self.respond(200, {"rows": rows})
            if path == "/api/job":
                job_id = query.get("id", [""])[0]
                try:
                    return self.respond(200, job_detail(state, job_id))
                except ValueError as exc:
                    return self.respond(404, {"error": str(exc)})
            self.respond(404, {"error": "Not found"})

    return HTTPServer(("127.0.0.1", port), Handler)


def serve(state, port=8766):
    server = make_server(state, port)
    print(f"Log Viewer: http://127.0.0.1:{server.server_port}/ (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
