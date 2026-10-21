"""Focused control page for one real, user-selected SPAM application.

All filling, review, submission and duplicate guards use the existing SPAM code.
The launcher reads the user's private config; it never stores answers in shared files.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from . import paths
from .batch import NO_RESUBMIT, protected_job_keys
from .queue import Queue

ROOT = Path(__file__).resolve().parent.parent
PORT = 8787
ORIGIN = f"http://127.0.0.1:{PORT}"


def selected_job():
    config = json.loads((paths.home() / "coffee-chat-demo.json").read_text())
    queue = Queue(paths.state() / "jobs.sqlite3")
    try:
        row = queue.db.execute("SELECT * FROM jobs WHERE id=?", (config["job_id"],)).fetchone()
        if not row:
            raise ValueError("The selected demo job is no longer in the queue.")
        job = dict(row)
        if job["url"] != config["url"]:
            raise ValueError("The employer URL changed; recheck the application before presenting.")
        job["protected"] = job["status"] in NO_RESUBMIT or bool(job.get("job_key") and
            job["job_key"] in protected_job_keys(queue, exclude_id=job["id"]))
        report_path = paths.state() / "runs" / f"{job['id']}.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            job["protected"] |= report.get("status") in set(NO_RESUBMIT) | {"email_code_required", "captcha_required"}
        return job
    finally:
        queue.db.close()


def preflight():
    from .ops import workers
    job = selected_job()
    if job["protected"] or job["status"] not in {"queued", "review", "needs_input"}:
        raise ValueError("This application is submitted, possibly submitted, or unavailable. Do not repeat it.")
    if workers():
        raise ValueError("Other SPAM application workers are active. Wait for them to finish.")
    data = json.loads(paths.profile().read_text())
    resumes = [Path(v).expanduser() for k, v in data.get("documents", {}).items()
               if "resume" in k.lower() and isinstance(v, str)]
    if not resumes or not all(p.is_file() for p in resumes):
        raise ValueError("Your saved resume is missing. Ask Codex to repair it before presenting.")
    return job


def show_application(job):
    """Focus only this real application in SPAM's shared visible browser."""
    from playwright.sync_api import sync_playwright
    from .batch import CDP, ensure_browser, window_for
    ensure_browser(paths.state())
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        context = browser.contexts[0]
        requisition = job["job_key"].rsplit(":", 1)[-1]
        page = next((pg for pg in context.pages if requisition in pg.url), None)
        if page is None:
            session = browser.new_browser_cdp_session()
            target = session.send("Target.createTarget", {"url": job["url"], "newWindow": True})["targetId"]
            page = window_for(context, target)
            page.wait_for_load_state("domcontentloaded", timeout=45000)
        page.bring_to_front()
        heading = page.get_by_role("heading", name="Apply for this job", exact=True)
        if heading.count():
            heading.first.scroll_into_view_if_needed()


class LiveDemo:
    def __init__(self):
        self.lock = threading.RLock()
        self.busy = False
        self.error = None
        self.progress = []
        self.token = secrets.token_urlsafe(24)

    def state(self):
        from .ops import review_report
        with self.lock:
            job = selected_job()
            ready = False
            if job["status"] == "review" and not self.busy and not self.error:
                report_path = paths.state() / "runs" / f"{job['id']}.json"
                try:
                    report = json.loads(report_path.read_text())
                    review = review_report(paths.state(), paths.profile(), [job["id"]])
                    ready = report.get("status") == "ready_for_review" and bool(review) and not review[0]["problems"]
                except (OSError, ValueError):
                    pass
            return {"demo": "spam-live-demo-v1", "token": self.token, "busy": self.busy,
                "error": self.error, "ready": ready, "protected": job["protected"],
                "company": job["company"], "role": job["role"], "status": job["status"],
                "review_url": "/job/" + job["id"], "progress": list(self.progress[-12:])}

    def start(self, action):
        with self.lock:
            if self.busy:
                raise ValueError("The demo is already working. Use Watch application when it finishes.")
            if action == "fill":
                preflight()
            elif action != "open":
                raise ValueError("Unknown demo action.")
            self.busy = True
            self.error = None
            if action == "fill":
                self.progress = ["Opening your real application…"]
            threading.Thread(target=self.work, args=(action,), daemon=True).start()

    def work(self, action):
        try:
            job = selected_job()
            show_application(job)
            if action == "open":
                return
            env = dict(os.environ, SPAM_KEEP_WINDOW="1", SPAM_CHAT_AGENT="1")
            # The subprocess makes the worker visible to SPAM health; its agent
            # environment also blocks accidental --submit use.
            log_path = paths.state() / "coffee-chat-demo-fill.log"
            with log_path.open("w") as log:
                log_path.chmod(0o600)
                process = subprocess.Popen([sys.executable, "-m", "job_agent.cli", "run-one", job["id"]],
                    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=log, text=True)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    text = line.strip()
                    if text.startswith(("Selected:", "Answered:", "Step ", "Advancing", "Review ")):
                        with self.lock:
                            self.progress.append(text)
                process.wait()
            report = json.loads((paths.state() / "runs" / f"{job['id']}.json").read_text())
            if process.returncode or report.get("status") != "ready_for_review":
                raise ValueError("SPAM paused for " + str(report.get("status", "an error")) +
                                 ". Leave the employer window open and ask Codex to resolve the saved report.")
            show_application(job)
            with self.lock:
                self.progress.append("Filled and checked. Ready for your review; nothing submitted.")
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.busy = False


def handler(demo):
    from .chat_ui import Handler

    class DemoHandler(Handler):
        state = str(paths.state())

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{PORT}":
                return self.send_json({"error": "Invalid host"}, 403)
            if self.path == "/demo-health":
                return self.send_json({"demo": "spam-live-demo-v1"})
            if self.path == "/api/demo/state":
                return self.send_json(demo.state())
            if self.path == "/":
                body = (Path(__file__).with_name("live_demo.html")).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if self.path == "/job/" + selected_job()["id"]:
                return super().do_GET()
            return self.send_json({"error": "Not part of the focused demo"}, 404)

        def do_POST(self):
            if (not self.local_only() or self.headers.get("Host") != f"127.0.0.1:{PORT}"
                    or self.headers.get("Origin") not in {None, ORIGIN}):
                return self.send_json({"error": "Invalid local request"}, 403)
            if self.path == "/api/demo/action":
                if self.headers.get("X-Demo-Token") != demo.token:
                    return self.send_json({"error": "Invalid demo token"}, 403)
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length < 4096:
                        raise ValueError("Invalid request size")
                    demo.start(json.loads(self.rfile.read(length))["action"])
                    return self.send_json({"ok": True})
                except (ValueError, KeyError, TypeError) as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if self.path in {"/api/approve", "/api/hold"}:
                if demo.busy:
                    return self.send_json({"error": "Wait until filling finishes."}, 409)
                import io
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length < 4096:
                        raise ValueError("Invalid request size")
                    raw = self.rfile.read(length)
                    body = json.loads(raw)
                    job_id = selected_job()["id"]
                    if self.path == "/api/approve":
                        if body.get("ids") != [job_id] or not demo.state()["ready"]:
                            return self.send_json({"error": "Only the selected checked application may be approved."}, 400)
                    elif body.get("id") != job_id:
                        return self.send_json({"error": "Only the selected application may be held."}, 400)
                    self.rfile = io.BytesIO(raw)
                except (ValueError, TypeError, AttributeError) as exc:
                    return self.send_json({"error": str(exc)}, 400)
                return super().do_POST()
            return self.send_json({"error": "Not part of the focused demo"}, 404)
    return DemoHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Local preflight only; no employer contacted")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.check:
        preflight()
        print("PASS: selected application is fillable, saved resume is present, no active workers. Nothing transmitted.")
        return
    if args.serve:
        ThreadingHTTPServer(("127.0.0.1", PORT), handler(LiveDemo())).serve_forever()
        return
    import webbrowser
    selected_job()  # fail clearly if the private config is missing
    try:
        with urlopen(ORIGIN + "/demo-health", timeout=1) as response:
            existing = json.load(response)
        if existing.get("demo") != "spam-live-demo-v1":
            raise SystemExit("Port 8787 is used by another app. Ask Codex to repair the demo launcher.")
    except OSError:
        log_path = paths.state() / "coffee-chat-demo-server.log"
        with log_path.open("a") as log:
            log_path.chmod(0o600)
            subprocess.Popen([sys.executable, "-m", "job_agent.live_demo", "--serve"],
                             cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        for _ in range(30):
            try:
                with urlopen(ORIGIN + "/demo-health", timeout=1) as response:
                    if json.load(response).get("demo") == "spam-live-demo-v1":
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("Demo control page could not start. Ask Codex to inspect the private server log.")
    webbrowser.open(ORIGIN)
    print("SPAM real application demo is open. Click Start live fill.")


if __name__ == "__main__":
    main()
