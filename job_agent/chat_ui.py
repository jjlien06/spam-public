"""Local chat console for SPAM: talk to Claude Code or ChatGPT (Codex CLI) about the
queue while a live dashboard shows what they do. Agents run in the repository with
the user's own CLI logins; submitting stays a button the user clicks (agents get
SPAM_CHAT_AGENT=1, and `--submit` refuses to run under it)."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).with_name("chat_ui.html")
GUIDE = Path(__file__).resolve().parent.parent / "docs" / "OPERATOR.md"


def system_prompt():
    return GUIDE.read_text() if GUIDE.exists() else "You operate SPAM from a chat window. Never submit; ask the user."


CLAUDE_TOOLS = ["Read", "Grep", "Glob", "Edit", "Write",
                "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
                "Bash(.venv/bin/job-agent:*)", "Bash(.venv/bin/python:*)", "Bash(sqlite3:*)",
                "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(ls:*)", "Bash(cat:*)",
                "Bash(tail:*)", "Bash(head:*)", "Bash(grep:*)"]
MODELS = {"claude": ["default", "opus", "sonnet", "haiku"], "codex": ["default", "gpt-6-luna", "gpt-5.6-luna", "gpt-5.4-mini"]}
pulling = {"last": None, "running": False}
mail = {"running": False, "last": None, "summary": ""}
assessment_scan = {"running": False, "last": None, "summary": ""}
sessions = {"claude": None, "codex": None}
lock = threading.Lock()


def db(state):
    connection = sqlite3.connect(Path(state) / "jobs.sqlite3", timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def dashboard(state):
    from . import assessments
    with db(state) as conn:
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
        review = [dict(r) for r in conn.execute(
            "SELECT id, company, role, location, platform FROM jobs WHERE status='review' ORDER BY company")]
        stuck = []
        for r in conn.execute("SELECT j.id, j.company, j.role, j.note, MAX(s.at) at FROM jobs j JOIN status_log s ON s.job_id=j.id"
                              " WHERE j.status='needs_input' GROUP BY j.id ORDER BY at DESC LIMIT 40"):
            note = r["note"] or ""
            try:
                missing = json.loads(note).get("missing", [])
                note = "; ".join(missing)[:200]
            except ValueError:
                note = note[:200]
            stuck.append({"id": r["id"], "company": r["company"], "role": r["role"], "needs": note, "at": r["at"]})
        recent = [dict(r) for r in conn.execute(
            "SELECT s.at, j.company, j.role, s.new_status status FROM status_log s JOIN jobs j ON j.id=s.job_id"
            " ORDER BY s.rowid DESC LIMIT 25")]
    ledger = Path(state) / "applications.csv"
    applied = max(0, sum(1 for _ in ledger.open()) - 1) if ledger.exists() else None
    running = []
    out = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout
    for line in out.splitlines():
        found = re.search(r"run-one (\w{20})( --submit)?", line)
        if found:
            running.append({"id": found.group(1), "submit": bool(found.group(2))})
    if running:
        with db(state) as conn:
            names = {r["id"]: (r["company"], r["role"]) for r in conn.execute(
                "SELECT id, company, role FROM jobs WHERE id IN (%s)" % ",".join("?" * len(running)), [r["id"] for r in running])}
        for r in running:
            r["company"], r["role"] = names.get(r["id"], ("?", "?"))
    # A job the user approved stays at Review until its submit finishes: show it as submitting.
    submitting = {r["id"] for r in running if r["submit"]}
    for r in review:
        r["submitting"] = r["id"] in submitting
        r["hand"] = r["platform"] == "ashby"  # Approve fills it and leaves the window for the user's Submit
    with db(state) as conn:
        hand = [dict(r) for r in conn.execute(
            "SELECT id, company, role FROM jobs WHERE status='hand_submit' ORDER BY company")]
    last_pull = None
    try:
        last_pull = json.loads((Path(state) / "last-pull.json").read_text())
    except (OSError, ValueError):
        pass
    return {"counts": counts, "review": review, "hand": hand, "stuck": stuck, "recent": recent, "running": running,
            "internlist": internlist_view(state),
            "assessments": assessments.board(state),
            "applied": applied, "time": datetime.now().strftime("%H:%M:%S"), "models": MODELS,
            "last_pull": last_pull, "pulling": pulling["running"], "mail": mail, "modes": __import__("job_agent.extensions", fromlist=["modes"]).modes(),
            "assessment_scan": assessment_scan, "batch": batch_running()}


def internlist_view(state):
    """intern-list.com roles: how the linked ones are doing, and the ones with no employer link for the user."""
    from . import internlist
    with db(state) as conn:
        linked = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM jobs WHERE source LIKE 'intern-list%' GROUP BY status")}
        found = internlist.discovered(conn, limit=40)
        try:
            unlinked = conn.execute("SELECT COUNT(*) FROM discovered WHERE status='new'").fetchone()[0]
        except sqlite3.OperationalError:
            unlinked = 0
    for row in found:
        row["search"] = internlist.careers_search(row["company"], row["role"])
    return {"linked": linked, "unlinked": unlinked, "found": found}


def dismiss_discovered(state, key):
    with db(state) as conn:
        conn.execute("UPDATE discovered SET status='dismissed' WHERE key=?", (key,))
    return {"ok": True}


def batch_running():
    out = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout
    return any(re.search(r"job_agent\.cli .*\b(batch|fill)\b|job-agent (batch|fill)\b", line) for line in out.splitlines())


def run_job_agent(state, args, log_name, env=None):
    log = Path(state) / log_name
    handle = log.open("w")
    return subprocess.Popen([str(ROOT / ".venv/bin/job-agent"), *args], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                            env=env or {k: v for k, v in os.environ.items() if k != "SPAM_CHAT_AGENT"}, start_new_session=True)


def pull_now(state):
    if pulling["running"]:
        return {"started": False, "reason": "already pulling"}
    pulling["running"] = True

    def work():
        try:
            run_job_agent(state, ["pull"], "pull-ui.out").wait(timeout=600)
        finally:
            pulling["running"] = False
            pulling["last"] = time.time()
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


def auto_pull(state, every=3600):
    """Pull new postings at startup and then every hour."""
    while True:
        pull_now(state)
        time.sleep(every)


def waiting_on(state, marker):
    with db(state) as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM jobs WHERE status='needs_input' AND note LIKE ?", (f"%{marker}%",))]


def update_assessment(state, data):
    from . import assessments
    key = str(data.get("id") or "")
    if not key or len(key) > 300:
        raise ValueError("bad assessment id")
    fields = {}
    for name in ("status", "due", "note", "company", "title", "link"):
        if name in data:
            fields[name] = str(data[name])[:1000 if name == "note" else 500]
    return assessments.update(state, key, **fields)


def mail_step(state, command):
    """Run one Gmail command (`codes`, `verify-accounts`, `confirmations`) in its own process; its JSON result."""
    parts = [command] if isinstance(command, str) else list(command)
    out = subprocess.run([str(ROOT / ".venv/bin/job-agent"), "--state", str(state), *parts], cwd=ROOT, capture_output=True, text=True, timeout=900,
                         env={k: v for k, v in os.environ.items() if k != "SPAM_CHAT_AGENT"})
    with (Path(state) / "mail-ui.out").open("a") as log:
        log.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(parts)}\n{out.stdout}{out.stderr}")
    if out.returncode:
        raise RuntimeError((out.stderr or out.stdout).strip().splitlines()[-1][:200] if (out.stderr or out.stdout).strip() else f"{command} failed")
    return json.loads(out.stdout)


def scan_assessments(state, days=45):
    if assessment_scan["running"]:
        return {"started": False, "reason": "assessment scan already running"}
    assessment_scan["running"] = True

    def work():
        try:
            result = mail_step(state, ["assessments", "scan", "--days", str(max(1, min(int(days), 365)))])
            assessment_scan["summary"] = (f"{len(result.get('added', []))} added; "
                                          f"{len(result.get('updated', []))} updated; "
                                          f"{len(result.get('done', []))} completed")
        except Exception as error:
            assessment_scan["summary"] = f"scan failed: {error}"[:240]
        finally:
            assessment_scan["running"] = False
            assessment_scan["last"] = datetime.now().strftime("%H:%M")
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


def check_mail(state, codes=True, verify=True, confirm=True):
    """Read Gmail for the user: enter security codes into waiting submits, open Workday account-verification
    links (then refill those jobs), and record confirmation emails in the ledger."""
    if mail["running"]:
        return {"started": False, "reason": "already checking Gmail"}
    mail["running"] = True

    def work():
        notes = []
        try:
            if codes and waiting_on(state, "email_code_required"):
                result = mail_step(state, "codes")
                notes += [f"{r['company']}: {r['status']}" for r in result["results"]]
            if verify and waiting_on(state, "email_verification_required"):
                result = mail_step(state, "verify-accounts")
                ids = [v["id"] for v in result["verified"]]
                if ids:
                    notes.append("verified accounts: " + ", ".join(v["company"] for v in result["verified"]))
                    if not batch_running():
                        run_job_agent(state, ["batch", "--parallel", str(min(4, len(ids))), "--ids", *ids],
                                      f"refill-mail-{datetime.now():%H%M%S}.out")
                        notes.append("refilling them")
            if confirm:
                result = mail_step(state, "confirmations")
                if result["recorded"]:
                    subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "job_agent.ledger", str(state)], cwd=ROOT,
                                   capture_output=True, timeout=300)
                    notes.append(f"{len(result['recorded'])} confirmation emails recorded")
        except Exception as error:  # a Gmail hiccup must not take the console down
            notes.append(f"Gmail check failed: {error}"[:240])
        finally:
            mail.update(running=False, last=datetime.now().strftime("%H:%M"), summary="; ".join(notes) or "nothing new")
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


def auto_mail(state, tick=60):
    """Codes expire within minutes, so waiting submits are checked every minute; account-verification
    emails every 5 minutes; confirmation emails every 30. Gmail is only read when something waits."""
    n = 0
    while True:
        time.sleep(tick)
        n += 1
        code = bool(waiting_on(state, "email_code_required"))
        verify = n % 5 == 0 and bool(waiting_on(state, "email_verification_required"))
        confirm = n % 30 == 0
        if code or verify or confirm:
            check_mail(state, codes=code, verify=verify, confirm=confirm)
        if confirm:
            scan_assessments(state, days=14)  # new assessment invitations, reminders and completions


def fill_now(state, count, parallel, mode=None):
    from . import extensions
    if batch_running():
        return {"started": False, "reason": "a batch is already running"}
    if mode and mode not in extensions.modes():
        return {"started": False, "reason": f"unknown mode {mode}"}
    args = ["fill", "--count", str(max(1, min(count, 60))), "--parallel", str(max(1, min(parallel, 8)))] + (["--mode", mode] if mode else [])
    run_job_agent(state, args, f"fill-ui-{datetime.now():%H%M%S}.out")
    return {"started": True}


def approve(state, ids):
    """Submit jobs the user approved with a click: only rows still at Review, each in the mode it was filled in."""
    from .ops import recorded_mode
    with db(state) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, company, role, note FROM jobs WHERE status='review' AND id IN (%s)" % ",".join("?" * len(ids)), ids)] if ids else []
    if not rows:
        return {"started": 0}
    from . import extensions
    groups, refused = {}, []
    installed = set(extensions.modes())
    for row in rows:
        mode = recorded_mode(state, row)
        if mode and mode not in installed:
            refused.append(f"{row['company']}: filled in mode {mode!r}, which is not installed here")
            continue
        groups.setdefault(mode, []).append(row["id"])
    if not groups:
        return {"started": 0, "refused": "; ".join(refused)}
    env = {k: v for k, v in os.environ.items() if k not in ("SPAM_CHAT_AGENT", "SPAM_MODE")}
    logs = []
    for mode, ready in groups.items():
        log = Path(state) / f"batch-submit-ui-{datetime.now():%H%M%S}{'-' + mode if mode else ''}.out"
        with log.open("w") as handle:
            subprocess.Popen([str(ROOT / ".venv/bin/job-agent"), "batch", "--submit", "--parallel", str(min(6, len(ready))),
                              "--ids", *ready], cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT,
                             env={**env, "SPAM_MODE": mode} if mode else env, start_new_session=True)
        logs.append(log.name)
    return {"started": sum(map(len, groups.values())), "log": ", ".join(logs), "refused": "; ".join(refused)}


JOB_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>
:root {{ --bg:#f5f6f4; --panel:#fff; --ink:#1c211d; --muted:#66706a; --line:#dde2dd; --accent:#1f6f4a; --accent-ink:#fff;
  --bad:#a3322b; --good:#1f6f4a; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111412; --panel:#181c19; --ink:#e6ebe7; --muted:#95a09a; --line:#2a302c;
  --accent:#4fb784; --accent-ink:#0d1a13; --bad:#ef7d74; --good:#4fb784; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,"SF Pro Text","Segoe UI",sans-serif; }}
main {{ max-width: 860px; margin: 0 auto; padding: 20px 16px 60px; }}
h1 {{ font-size: 20px; margin: 0 0 2px; }} .muted {{ color: var(--muted); }}
.bar {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:14px 0; }}
a.btn, button {{ font:inherit; color:var(--ink); background:var(--panel); border:1px solid var(--line); border-radius:8px;
  padding:6px 12px; text-decoration:none; cursor:pointer; }}
button.primary {{ background:var(--accent); color:var(--accent-ink); border-color:var(--accent); font-weight:600; }}
.check {{ padding:10px 12px; border-radius:8px; border:1px solid var(--line); background:var(--panel); margin:10px 0; }}
.check.ok {{ border-color: var(--good); }} .check.bad {{ border-color: var(--bad); color: var(--bad); }}
.sheet {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:6px 14px; }}
.sheet div {{ padding:2px 0; overflow-wrap:anywhere; font-weight:600; }}
.sheet .q {{ color:var(--muted); margin-top:10px; font-weight:400; }}
input[type=text] {{ font:inherit; color:var(--ink); background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:6px 10px; flex:1; min-width:180px; }}
</style></head><body><main>
<p><a href="/" class="muted">\u2190 Console</a></p>
<h1>{company}</h1><div class="muted">{role} \u00b7 {platform} \u00b7 status <b>{status}</b> \u00b7 {prior}</div>
<div class="bar"><a class="btn" href="{url}" target="_blank" rel="noopener">Open posting \u2197</a>{actions}</div>
{body}
</main><script>
async function post(path, body) {{
  const r = await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}}).then(r => r.json());
  document.getElementById('said').textContent = r.error || r.message || (r.started ? 'Submitting now; the console shows the result.' : r.started === 0 ? 'Not at Review anymore.' : JSON.stringify(r));
}}
</script></body></html>"""


def esc(text):
    import html
    return html.escape(str(text if text is not None else ""))


def _verdict(report):
    match = (re.search(r"^#+\s*(?:Verdict|Recommendation)\s*$\n+(.+?)(?:\n\n|\n---|$)", report, re.M | re.S)
             or re.search(r"^\*\*(?:Verdict|Recommendation)\b[^\n]*", report, re.M))
    text = match.group(1) if match and match.groups() else (match.group(0) if match else "")
    text = re.sub(r"[*_`]", "", text).strip()
    return re.sub(r"^(?:Verdict|Recommendation)\s*:\s*", "", text)[:400]


def fit_board(state):
    """Queued jobs career-ops scored, best first, with each report's verdict line."""
    from .fit import min_score
    db = sqlite3.connect(Path(state) / "jobs.sqlite3")
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT id, company, role, url, platform, fit_score, fit_legitimacy, fit_report FROM jobs"
                          " WHERE status='queued' AND fit_score IS NOT NULL ORDER BY fit_score DESC, rowid DESC").fetchall()
    except sqlite3.OperationalError:
        rows = []
    jobs = []
    for r in rows:
        try:
            verdict = _verdict(Path(r["fit_report"]).read_text(errors="ignore"))
        except OSError:
            verdict = ""
        jobs.append({"id": r["id"], "company": r["company"], "role": r["role"], "url": r["url"], "platform": r["platform"],
                     "score": r["fit_score"], "legitimacy": r["fit_legitimacy"], "verdict": verdict})
    try:
        cutoff = min_score(json.loads(paths.profile().read_text()))
    except (OSError, ValueError):
        cutoff = 3.0
    return {"jobs": jobs, "cutoff": cutoff}


def fit_page(state, job_id):
    """One fit report as a readable page (minimal Markdown: headings, bold, tables, lists)."""
    import html as html_lib
    path = Path(state) / "fit" / f"{job_id}.md"
    if not path.exists():
        return None
    out, table = [], []

    def inline(text):
        text = html_lib.escape(text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        return re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noreferrer">\1</a>', text)

    def flush():
        if table:
            cells = [[c.strip() for c in row.strip("|").split("|")] for row in table if not re.fullmatch(r"[|\s:-]+", row)]
            out.append("<table>" + "".join("<tr>" + "".join(f"<{'th' if i == 0 else 'td'}>{inline(c)}</{'th' if i == 0 else 'td'}>"
                       for c in row) + "</tr>" for i, row in enumerate(cells)) + "</table>")
            table.clear()

    for line in path.read_text(errors="ignore").splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if m := re.match(r"(#{1,4})\s+(.*)", line):
            level = min(len(m.group(1)) + 0, 4)
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
        elif re.match(r"\s*[-*]\s+", line):
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append(f"<li>{inline(item)}</li>")
        elif line.strip() and not line.startswith("---"):
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return ("<!doctype html><meta charset=utf-8><title>Fit report</title><style>"
            "body{font:15px/1.55 -apple-system,sans-serif;max-width:860px;margin:32px auto;padding:0 20px;color:#1c211d;background:#f5f7f5}"
            "@media(prefers-color-scheme:dark){body{background:#101411;color:#e6ebe7}td,th{border-color:#303a32!important}}"
            "h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid #dbe3dc;padding-bottom:4px}"
            "table{border-collapse:collapse;width:100%;font-size:13.5px;margin:8px 0}td,th{border:1px solid #dbe3dc;padding:6px 8px;text-align:left;vertical-align:top}"
            "th{background:rgba(31,111,74,.12)}a{color:#1f6f4a}li{margin:3px 0 3px 18px}</style>" + "\n".join(out))


def job_page(state, job_id):
    """Everything about one job the user may want to read before approving: the answer check and
    every line the site will receive (Review), or what it is stuck on."""
    from . import ops
    with db(state) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    row = dict(row)
    actions, body, prior = "", "", ""
    if row["status"] == "review":
        [report] = ops.review_report(state, paths.profile(), [job_id]) or [None]
        prior = f"applied here before: {report['prior_at_company']}" + (f" \u00b7 {report['mode']} mode" if report.get("mode") else "")
        problems = report["problems"]
        body += (f'<div class="check bad"><b>Check found problems:</b><br>{"<br>".join(esc(p) for p in problems)}</div>' if problems
                 else '<div class="check ok">Answer check: clean (every answer matches your profile)</div>')
        shown = report["lines"]
        # Workday page text starts with site chrome and the step list; the answers begin after "Review".
        heads = [i for i, line in enumerate(shown[:60]) if line == "Review"]
        shown = shown[heads[-1] + 1:] if heads else shown
        chrome = re.compile(r"^(Back|Submit|Follow Us|Skip to main content|Settings|Search for Jobs|Candidate Home|Job Alerts|©.*|.*All rights reserved\.?)$")
        lines = []
        for line in (l for l in shown if not chrome.match(l)):
            question = line.rstrip().endswith(("?", ":", "*", "?*", ":*")) or len(line) > 90
            lines.append(f'<div class="{"q" if question else ""}">{esc(line)}</div>')
        body += '<h2 style="font-size:15px">What the site will receive</h2><div class="sheet">' + "".join(lines) + "</div>"
        label = ("Approve &amp; open for my Submit" if row.get("platform") == "ashby" else "Approve &amp; submit")
        if row.get("platform") == "ashby":
            body = ('<div class="check">Ashby blocks automated submits. Approve fills this form again, checks it and leaves the '
                    'window open: press Submit there yourself.</div>') + body
        actions = ('<button class="primary" onclick="post(\'/api/approve\', {ids:[\'' + job_id + '\']})">' + label + '</button>'
                   '<input type="text" id="why" placeholder="Reason to hold (optional)">'
                   '<button onclick="post(\'/api/hold\', {id:\'' + job_id + '\', reason: document.getElementById(\'why\').value || \'held by user\'})">Hold</button>'
                   '<span id="said" class="muted"></span>')
    else:
        run = Path(state) / "runs" / f"{job_id}.json"
        try:
            detail = json.loads(run.read_text())
        except (OSError, ValueError):
            detail = {}
        try:
            missing = json.loads(row["note"] or "{}").get("missing") or []
        except ValueError:
            missing = [row["note"]]
        options = detail.get("options") or {}
        items = "".join(f'<div class="q">{esc(m)}</div><div>{esc(", ".join(map(str, options.get(m, []))) or "")}</div>' for m in missing)
        body += (f'<h2 style="font-size:15px">Stuck on</h2><div class="sheet">{items or esc(row["note"])}</div>' if row["status"] == "needs_input"
                 else f'<div class="check">{esc(row["note"])}</div>')
        review = Path(state) / "runs" / f"{job_id}.review.txt"
        if review.exists():
            body += ('<h2 style="font-size:15px">Last Review snapshot (may be stale)</h2><div class="sheet">'
                     + "".join(f"<div>{esc(l)}</div>" for l in review.read_text(errors="ignore").splitlines() if l.strip()) + "</div>")
        actions = '<span id="said" class="muted"></span>'
    return JOB_PAGE.format(title=esc(f"{row['company']} review"), company=esc(row["company"]), role=esc(row["role"]),
                           platform=esc(row.get("platform")), status=esc(row["status"]), prior=esc(prior), url=esc(row["url"]),
                           actions=actions, body=body)


def agent_command(provider, message, model="default"):
    if provider == "claude":
        command = ["claude", "-p", message, "--output-format", "stream-json", "--verbose",
                   "--append-system-prompt", system_prompt(), "--permission-mode", "acceptEdits", "--allowedTools", *CLAUDE_TOOLS]
        if model in MODELS["claude"][1:]:
            command += ["--model", model]
        if sessions["claude"]:
            command += ["--resume", sessions["claude"]]
        else:
            sessions["claude"] = str(uuid.uuid4())
            command += ["--session-id", sessions["claude"]]
        return command
    base = ["codex", "exec"]
    if sessions["codex"]:
        base += ["resume", sessions["codex"]]
    if model in MODELS["codex"][1:]:
        base += ["-m", model]
    return base + ["--json", "--skip-git-repo-check", "-s", "workspace-write",
                   "-c", "sandbox_workspace_write.network_access=true",
                   (system_prompt() + "\n\nUser: " + message) if not sessions["codex"] else message]


def events(provider, line):
    """Turn one CLI JSON line into chat events: text, tool (a command/edit), or done."""
    try:
        item = json.loads(line)
    except ValueError:
        return [{"kind": "tool", "text": line[:400]}] if line.strip() else []  # CLI log lines, not the reply
    out = []
    if provider == "claude":
        if item.get("type") == "system" and item.get("session_id"):
            sessions["claude"] = item["session_id"]
        if item.get("type") == "assistant":
            for block in item.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    out.append({"kind": "text", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    data = block.get("input", {})
                    out.append({"kind": "tool", "text": f'{block.get("name")}: {data.get("command") or data.get("file_path") or data.get("pattern") or ""}'[:400]})
        if item.get("type") == "result":
            cost = item.get("total_cost_usd")
            out.append({"kind": "done", "text": f"${cost:.3f}" if isinstance(cost, (int, float)) else ""})
        return out
    kind = item.get("type", "")
    if kind == "thread.started" and item.get("thread_id"):
        sessions["codex"] = item["thread_id"]
    entry = item.get("item") or {}
    if kind == "item.completed":
        if entry.get("type") in ("agent_message", "assistant_message") and entry.get("text"):
            out.append({"kind": "text", "text": entry["text"]})
        elif entry.get("type") == "command_execution":
            out.append({"kind": "tool", "text": ("$ " + str(entry.get("command", "")))[:400]})
        elif entry.get("type") == "file_change":
            out.append({"kind": "tool", "text": "edit: " + ", ".join(c.get("path", "") for c in entry.get("changes", []))[:400]})
    if kind == "turn.completed":
        out.append({"kind": "done", "text": ""})
    if kind in ("error", "turn.failed"):
        out.append({"kind": "error", "text": str(item.get("message") or item.get("error") or item)[:400]})
    return out


class Handler(BaseHTTPRequestHandler):
    state = None

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
        # Callers also validate Host (the focused demo has its own Host policy).
        # No Origin supports CLI clients; a present Origin must match exactly.
        origins = [value for name, value in self.headers.items() if name.lower() == "origin"]
        return not origins or origins == [f"http://{self.headers.get('Host')}"]

    def valid_host(self):
        # Pin the HTTP authority to this listener, not DNS or a hostname prefix.
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        return len(hosts) == 1 and hosts[0] in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def do_GET(self):
        if not self.valid_host() or not self.local_only():
            return self.send_json({"error": "forbidden"}, 403)
        if self.path == "/":
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif re.fullmatch(r"/job/\w{20}", self.path):
            page = job_page(self.state, self.path.rsplit("/", 1)[1])
            if page is None:
                return self.send_json({"error": "unknown job"}, 404)
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/assessments":
            body = (Path(__file__).with_name("assessments.html")).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/hand-submit":
            body = (Path(__file__).with_name("hand_submit.html")).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/hand-submit":
            from . import hand_submit
            self.send_json(hand_submit.board(self.state))
        elif self.path == "/api/assessments":
            from . import assessments
            self.send_json({"cards": assessments.board(self.state), "scan": assessment_scan})
        elif self.path == "/api/fit":
            self.send_json(fit_board(self.state))
        elif re.fullmatch(r"/fit/\w{20}", self.path):
            page = fit_page(self.state, self.path.rsplit("/", 1)[1])
            if page is None:
                return self.send_json({"error": "no report"}, 404)
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            self.send_json(dashboard(self.state))
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.valid_host() or not self.local_only():
            return self.send_json({"error": "forbidden"}, 403)
        # Ordinary cross-site forms cannot send application/json. Keep this
        # check even for Origin-less clients, before reading or dispatching.
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            return self.send_json({"error": "application/json required"}, 415)
        length = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/approve":
            return self.send_json(approve(self.state, [i for i in data.get("ids", []) if re.fullmatch(r"\w{20}", i)]))
        if self.path == "/api/hold":
            from . import ops
            if not re.fullmatch(r"\w{20}", str(data.get("id", ""))):
                return self.send_json({"error": "bad id"}, 400)
            try:
                ops.hold(self.state, data["id"], str(data.get("reason") or "held by user")[:300])
            except SystemExit as error:
                return self.send_json({"error": str(error)})
            return self.send_json({"message": "Held: it is off the Approve list until it is refilled."})
        if self.path in ("/api/hand-submit/show", "/api/hand-submit/done"):
            from . import hand_submit
            job_id = str(data.get("id", ""))
            if not re.fullmatch(r"\w{20}", job_id):
                return self.send_json({"error": "bad id"}, 400)
            act = hand_submit.show if self.path.endswith("show") else hand_submit.mark_submitted
            return self.send_json(act(self.state, job_id))
        if self.path == "/api/assessments/scan":
            return self.send_json(scan_assessments(self.state, data.get("days", 45)))
        if self.path == "/api/assessments/add":
            from . import assessments
            company, title = str(data.get("company", "")).strip()[:120], str(data.get("title", "")).strip()[:300]
            if not company or not title:
                return self.send_json({"error": "company and title are required"}, 400)
            due = str(data.get("due") or "")[:10]
            if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
                return self.send_json({"error": "due must be YYYY-MM-DD"}, 400)
            link = str(data.get("link") or "")[:500]
            if link and not link.startswith(("https://", "http://")):
                return self.send_json({"error": "link must be a web address"}, 400)
            return self.send_json(assessments.add(self.state, company, title, due or None, str(data.get("kind") or "Assessment")[:30], link or None))
        if self.path == "/api/assessments/update":
            try:
                return self.send_json(update_assessment(self.state, data))
            except ValueError as error:
                return self.send_json({"error": str(error)}, 400)
        if self.path == "/api/mail":
            return self.send_json(check_mail(self.state))
        if self.path == "/api/internlist/dismiss":
            return self.send_json(dismiss_discovered(self.state, str(data.get("key", ""))))
        if self.path == "/api/pull":
            return self.send_json(pull_now(self.state))
        if self.path == "/api/fill":
            return self.send_json(fill_now(self.state, int(data.get("count", 10)), int(data.get("parallel", 6)), (str(data.get("mode") or "") or None)))
        if self.path == "/api/reset":
            sessions[data.get("provider", "claude")] = None
            return self.send_json({"ok": True})
        if self.path != "/api/chat":
            return self.send_json({"error": "not found"}, 404)
        provider = "codex" if data.get("provider") == "codex" else "claude"
        message = str(data.get("message", "")).strip()
        if not message:
            return self.send_json({"error": "empty"}, 400)
        if not lock.acquire(blocking=False):
            return self.send_json({"error": "The agent is still working on the last message."}, 409)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            env = {**os.environ, "SPAM_CHAT_AGENT": "1"}
            process = subprocess.Popen(agent_command(provider, message, str(data.get("model", "default"))), cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, env=env, bufsize=1)
            try:
                for line in process.stdout:
                    for event in events(provider, line.rstrip("\n")):
                        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                process.wait(timeout=5)
                if process.returncode:
                    self.wfile.write(("data: " + json.dumps({"kind": "error", "text": f"{provider} exited {process.returncode}"}) + "\n\n").encode())
                self.wfile.write(b'data: {"kind": "end"}\n\n')
            except (BrokenPipeError, ConnectionResetError):
                process.terminate()
        finally:
            lock.release()


def serve(state, port=8770):
    Handler.state = str(state)
    threading.Thread(target=auto_pull, args=(str(state),), daemon=True).start()
    threading.Thread(target=auto_mail, args=(str(state),), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"SPAM chat on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    from . import paths
    serve(sys.argv[1] if len(sys.argv) > 1 else str(paths.state()))
