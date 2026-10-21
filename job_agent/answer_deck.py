"""Local question review and profile editing. Never opens or submits an application."""
import hashlib
import json
import math
import re
import secrets
import sqlite3
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

from .batch import with_company_history
from .custom_controls import option_match
from .form_controls import field_value
from .profile import Profile, normalize
from .profile_editor import with_answer, write_profile
from .questions import is_diagnostic, question_label

STOPPED = {"needs_input", "review_flagged", "review_mismatch"}


def read_questions(state):
    """Read active stopped runs, preserving each site's distinct set of choices."""
    state = Path(state)
    db_path = state / "jobs.sqlite3"
    if not db_path.exists():
        return [], []
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        jobs = [dict(row) for row in db.execute("SELECT id, job_key, company, role, status FROM jobs ORDER BY id")]
        for job in jobs:
            if job["status"] in STOPPED:
                job["history_pattern"] = with_company_history({}, SimpleNamespace(db=db), job)["patterns"][0]
    finally:
        db.close()
    grouped, issues = {}, []
    for job in jobs:
        if job["status"] not in STOPPED:
            continue
        report_path = state / "runs" / (job["id"] + ".json")
        # IDs originate in the queue, but must never permit reading outside runs/.
        if report_path.parent.resolve() != (state / "runs").resolve():
            continue
        try:
            report = json.loads(report_path.read_text())
        except (OSError, ValueError):
            issues.append({"company": job["company"], "job_id": job["id"], "message": "No readable run report"})
            continue
        if report.get("status") not in STOPPED:
            issues.append({"company": job["company"], "job_id": job["id"],
                           "message": "Finish the existing run: " + str(report.get("status", "unknown"))})
            continue
        for missing in report.get("missing", []):
            label = question_label(missing)
            if not label or is_diagnostic(label) or report["status"] != "needs_input":
                issues.append({"company": job["company"], "job_id": job["id"], "message": missing})
                continue
            options = report.get("options", {}).get(label, [])
            options = list(dict.fromkeys(o.strip() for o in options if isinstance(o, str) and o.strip()))
            entry = grouped.setdefault(normalize(label), {"question": label, "options": [], "observations": []})
            entry["options"] = list(dict.fromkeys(entry["options"] + options))
            observation = {"question": label, "options": options, "job_id": job["id"],
                           "company": job["company"], "role": job["role"], "history_pattern": job["history_pattern"]}
            if observation not in entry["observations"]:
                entry["observations"].append(observation)
    return sorted(grouped.values(), key=lambda q: (-len(q["observations"]), q["question"])), issues


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _validate_value(value):
    if not isinstance(value, (str, bool, int, float)) or value is None or value == "":
        raise ValueError("Choose an answer or enter a verified fact.")
    if isinstance(value, str) and (not value.strip() or len(value) > 12000):
        raise ValueError("The answer must contain between 1 and 12000 characters.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Use a finite number.")


def _job_profile(data, observation):
    return Profile({**data, "patterns": [observation["history_pattern"], *data.get("patterns", [])]})


class AnswerDeck:
    def __init__(self, state, profile_path):
        self.state = Path(state)
        self.profile_path = Path(profile_path)
        self.previews = {}

    def snapshot(self):
        data = json.loads(self.profile_path.read_text())
        Profile(data)
        questions, issues = read_questions(self.state)
        return data, questions, issues

    def view(self):
        data, questions, issues = self.snapshot()
        for entry in questions:
            current = [field_value(_job_profile(data, o), o["question"], choice=bool(o["options"])) for o in entry["observations"]]
            entry["current"] = current[0] if all(v == current[0] for v in current) else "Varies by company; check the preview."
        return {"questions": questions, "issues": issues}

    def preview(self, candidate):
        data, questions, issues = self.snapshot()
        if candidate.get("profile_revision"):
            from .agent_client import digest
            if candidate["profile_revision"] != digest(data):
                raise ValueError("The profile changed since this suggestion. Generate a fresh suggestion.")
        value = candidate.get("value")
        _validate_value(value)
        kind = candidate.get("kind")
        rule = None
        if kind == "exact":
            label = candidate.get("question", "")
            if not isinstance(label, str) or normalize(label) not in {normalize(q["question"]) for q in questions}:
                raise ValueError("Choose a question from the current deck.")
            updated = with_answer(data, label, value)
        elif kind == "pattern":
            expression = candidate.get("match", "")
            exceptions = candidate.get("unless_mentions", [])
            if not isinstance(expression, str) or not expression.strip() or len(expression) > 300:
                raise ValueError("Enter a question pattern of 1–300 characters.")
            if not isinstance(exceptions, list) or any(not isinstance(e, str) or not e.strip() for e in exceptions):
                raise ValueError("Exceptions must be nonempty phrases.")
            rule = re.compile(expression, re.I)
            updated = deepcopy(data)
            updated.setdefault("patterns", []).append({"match": expression, "value": value, "unless_mentions": exceptions})
        else:
            raise ValueError("Choose an exact answer or a choice rule.")
        before, after = Profile(data), Profile(updated)
        rows, changed, invalid = [], 0, False
        for entry in questions:
            for observation in entry["observations"]:
                label, options = observation["question"], observation["options"]
                choice = bool(options)
                old = field_value(_job_profile(data, observation), label, choice=choice)
                new = field_value(_job_profile(updated, observation), label, choice=choice)
                affected = normalize(label) == normalize(candidate.get("question", "")) if kind == "exact" else bool(rule.search(normalize(label)))
                if not affected and old == new:
                    continue
                selected = option_match([{"label": o, "value": o} for o in options], new) if options and new is not None else None
                compatible = not options or new is None or selected is not None
                if old != new:
                    changed += 1
                if not compatible:
                    invalid = True
                rows.append({**observation, "before": old, "after": new, "selected": selected,
                             "changed": old != new, "compatible": compatible,
                             "note": "" if old != new else "Unchanged: existing answer, earlier rule, exception, or no captured choice options."})
        # Include every saved alias affected by an exact update, even when it is not
        # in this batch. The controller's alias semantics must remain visible.
        aliases = sorted(k for k in set(before.answers) | set(after.answers)
                         if before.answers.get(k) != after.answers.get(k))
        can_save = bool(rows) and not invalid and (kind == "exact" or changed > 0)
        result = {"rows": rows, "changed": changed, "affected_aliases": aliases,
                  "can_save": can_save, "kind": kind, "coverage": sum(len(q["observations"]) for q in questions)}
        if invalid:
            result["message"] = "The resulting answer does not match one site's captured options. Use separate exact answers or review the choices."
        elif not rows or (kind == "pattern" and not changed):
            result["message"] = "This rule would not change any captured choice answer. Earlier matching rules and exact answers take priority."
        else:
            result["message"] = "Review the resulting answers below before saving. Refill the jobs afterward to verify the live forms."
        if can_save:
            token = secrets.token_urlsafe(24)
            if len(self.previews) >= 100:
                self.previews.clear()
            self.previews[token] = (_digest([data, questions, issues]), updated)
            result["preview"] = token
        return result

    def save(self, token):
        pending = self.previews.pop(token, None)
        if pending is None:
            raise ValueError("Preview this answer again before saving.")
        data, questions, issues = self.snapshot()
        revision, updated = pending
        if revision != _digest([data, questions, issues]):
            raise ValueError("The profile or run reports changed. Refresh and preview again.")
        write_profile(self.profile_path, updated)
        self.previews.clear()
        return {"saved": True, "message": "Saved to the profile. Refill the affected jobs, then review them."}


def make_server(deck, port=8765):
    """Loopback only; no file serving, remote assets, or application actions."""
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Questions and answers never go to access logs.

        def respond(self, status, data, content_type="application/json"):
            body = data.encode() if isinstance(data, str) else json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-" + csrf + "'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = "127.0.0.1:" + str(self.server.server_port)
            return self.headers.get("Host") == host and self.headers.get("Origin", "http://" + host) == "http://" + host

        def do_GET(self):
            if not self.allowed():
                return self.respond(403, {"error": "Open the local URL printed in the terminal."})
            if self.path == "/":
                html = Path(__file__).with_name("answer_deck.html").read_text().replace("__TOKEN__", csrf)
                return self.respond(200, html, "text/html")
            if self.path == "/api/deck":
                try:
                    return self.respond(200, deck.view())
                except (OSError, ValueError, sqlite3.Error):
                    return self.respond(400, {"error": "Could not read the profile or run reports. Check the local files."})
            if self.path == "/api/agent":
                from .agent_client import status
                return self.respond(200, status(deck.state))
            if self.path == "/api/agent/suggestions":
                from .agent_client import digest
                path=deck.state / 'agent' / 'suggestions.json'
                proposals=json.loads(path.read_text()) if path.exists() else []
                revision=digest(json.loads(deck.profile_path.read_text()))
                return self.respond(200, {'proposals':[p for p in proposals if p.get('profile_revision')==revision]})
            self.respond(404, {"error": "Not found"})

        def do_POST(self):
            if not self.allowed() or self.headers.get("X-Deck-Token") != csrf:
                return self.respond(403, {"error": "Refresh the local Answer Deck before saving."})
            if self.headers.get("Content-Type") != "application/json":
                return self.respond(415, {"error": "JSON required"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 20000:
                    raise ValueError("Request is too large or empty.")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object.")
                if self.path == "/api/preview":
                    result = deck.preview(payload)
                elif self.path == "/api/save":
                    result = deck.save(payload.get("preview", ""))
                elif self.path == "/api/agent/configure":
                    from .agent_client import save_config, status
                    save_config(deck.state, payload)
                    result = status(deck.state)
                elif self.path == "/api/agent/suggest":
                    from .agent_answers import suggest
                    labels = payload.get("questions")
                    if labels is not None and (not isinstance(labels, list) or any(not isinstance(q, str) for q in labels)):
                        raise ValueError("Expected a list of questions.")
                    result = suggest(deck, labels, retry=payload.get("retry") is True)
                else:
                    return self.respond(404, {"error": "Not found"})
                self.respond(200, result)
            except re.error:
                self.respond(400, {"error": "The question pattern is not a valid regular expression."})
            except (ValueError, TypeError) as exc:
                self.respond(400, {"error": str(exc)})
            except (OSError, sqlite3.Error):
                self.respond(400, {"error": "Could not read or save the local files."})

    return HTTPServer(("127.0.0.1", port), Handler)


def serve(state, profile_path, port=8765):
    deck = AnswerDeck(state, profile_path)
    deck.snapshot()  # Fail before starting if the profile cannot be read.
    server = make_server(deck, port)
    print(f"Answer Deck: http://127.0.0.1:{server.server_port}/ (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
