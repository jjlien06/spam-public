"""Visible multi-window batch runner.

Fills the newest queued jobs up to the Review page in a shared visible browser
(one window per job, closed when its run ends). It submits only with --submit,
for jobs the user approved, after the Review audit and answer check pass. Results
are written back to the queue; unanswered questions are grouped across jobs so
they can be answered once.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from .queue import company_key
from .targeting import ranked

CDP = "http://127.0.0.1:9333"
# Statuses that count toward a company's cap: already applied or waiting at Review.
CAPPED = ("submitted", "applied", "review", "already_applied", "submit_uncertain", "spam_flagged", "hand_submit")
# Never submitted again by SPAM: a guest-apply site (no account) takes a second copy.
# Sent (or possibly sent) applications: they count as company history.
APPLIED_STATUSES = ("submitted", "applied", "already_applied", "submit_uncertain")
# Never submitted again by SPAM: sent, possibly sent, or waiting for the user's own click.
NO_RESUBMIT = APPLIED_STATUSES + ("spam_flagged", "hand_submit")
# "Have you already applied to one of our programmes?" depends on this company's history.
APPLIED_BEFORE = (r"(?:already|previously|ever) applied|applied (?:before|previously|in the past|on any previous occasions)"
                  r"|prior application|(?:submitted|made) an? (?:previous |prior )?application")


def protected_job_keys(queue, exclude_id=None):
    """Canonical postings already applied to, possibly submitted, or reserved for hand submit."""
    keys = {row[0] for row in queue.db.execute(
        "SELECT job_key FROM jobs WHERE status IN (" + ",".join("?" for _ in NO_RESUBMIT) + ")"
        + (" AND id != ?" if exclude_id is not None else ""),
        (*NO_RESUBMIT, *((exclude_id,) if exclude_id is not None else ()))) if row[0]}
    keys.update(row[0] for row in queue.db.execute(
        "SELECT job_key FROM tracked WHERE status='APPLIED'") if row[0])
    return keys


def with_company_history(profile, queue, job):
    """Profile copy that answers prior-application questions from the queue: Yes when any
    other job at this company was applied to (SPAM, Simplify tracker, or found already
    applied), otherwise No. LXEG asks this and a fixed No was false after two applications."""
    prior = queue.db.execute(
        f"SELECT count(*) FROM jobs WHERE company = ? AND id != ? AND status IN ({', '.join('?' * len(APPLIED_STATUSES))})",
        (job["company"], job["id"], *APPLIED_STATUSES)).fetchone()[0]
    # Simplify's tracker also lists applications to postings SPAM never queued.
    prior += queue.db.execute(
        "SELECT count(*) FROM tracked WHERE status = 'APPLIED' AND lower(company) = lower(?) AND job_key != ?",
        (job["company"], job["job_key"] or "")).fetchone()[0]
    profile = dict(profile)
    profile["patterns"] = [{"match": APPLIED_BEFORE, "value": "Yes" if prior else "No",
                            "unless_mentions": ["clearance", "visa", "sponsorship", "patent", "license"],
                            "note": f"{prior} earlier application(s) at {job['company']} in the queue"}] + list(profile.get("patterns", []))
    return profile


def browser_ready():
    try:
        with urlopen(CDP + "/json/version", timeout=1):
            return True
    except OSError:
        return False


def serve_browser(state):
    """Keep the visible browser (with saved logins) open for workers to attach to."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(Path(state) / "watch-browser"), headless=False, no_viewport=True,
            args=["--remote-debugging-port=9333", "--remote-debugging-address=127.0.0.1",
                  # Windows sit behind the user's apps: keep them rendering at full speed.
                  "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                  "--disable-background-timer-throttling"])
        if not ctx.pages:
            ctx.new_page()
        print("Browser ready", flush=True)
        while ctx.browser is None or ctx.browser.is_connected():
            try:
                (ctx.pages[0] if ctx.pages else ctx.new_page()).wait_for_timeout(1000)
            except Exception:
                time.sleep(1)


def ensure_browser(state):
    if browser_ready():
        return
    subprocess.Popen([sys.executable, "-m", "job_agent.cli", "--state", str(state), "browser"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        if browser_ready():
            return
        time.sleep(0.5)
    raise SystemExit("Visible browser did not start")


def applied_counts(queue):
    """Applications per company: SPAM's own (CAPPED statuses) plus ones in the Simplify tracker that SPAM never
    filled (2026-09-25: OrbitX refused a 5th because a January hand application was not counted)."""
    import collections
    counts = collections.Counter()
    keys = set()
    for row in queue.db.execute("SELECT company, job_key FROM jobs WHERE status IN (%s)" % ",".join("?" * len(CAPPED)), CAPPED):
        counts[company_key(row[0])] += 1
        keys.add(row[1])
    try:
        for company, key in queue.db.execute("SELECT company, job_key FROM tracked WHERE status='APPLIED'"):
            if key not in keys:
                counts[company_key(company)] += 1
    except Exception:
        pass  # no tracker imported
    return counts


def pick(queue, count, prefs, company_cap=4, platforms=("workday",)):
    """Newest queued jobs on the given platforms, one per company, skipping capped/excluded companies
    and companies whose earlier job is still waiting on an answer or a login (a new
    job there would stop at the same place)."""
    from .targeting import TAKEN, role_key
    excluded = {company_key(c) for c in (prefs or {}).get("exclude_companies", [])}
    done, taken = {}, set()
    protected = protected_job_keys(queue)
    for row in queue.db.execute("SELECT company, role, status FROM jobs"):
        if row["status"] in TAKEN:
            taken.add(role_key(dict(row)))
        if row["status"] == "needs_input":
            excluded.add(company_key(row["company"]))
    done = applied_counts(queue)
    chosen, companies = [], set()
    for job in ranked(queue):
        company = company_key(job["company"])
        if (job["platform"] not in platforms or company in companies or company in excluded
                or (job.get("job_key") and job["job_key"] in protected)):
            continue
        if done.get(company, 0) >= company_cap or ((prefs or {}).get("one_application_per_role") and role_key(job) in taken):
            continue
        chosen.append(job)
        companies.add(company)
        if len(chosen) >= count:
            break
    return chosen


def queue_status(report):
    """Queue status and note for a run report."""
    status = report.get("status", "needs_input")
    missing = report.get("missing", [])
    mapped = {"ready_for_review": "review", "already_applied": "applied", "posting_closed": "closed",
              "skipped_transcript": "skipped", "submitted": "submitted",
              "submit_uncertain": "submit_uncertain", "spam_flagged": "spam_flagged",
              "hand_submit": "hand_submit", "captcha_required": "captcha_required"}.get(status)
    if mapped == "submitted" and report.get("confirmation"):
        # Keep the site's own evidence (confirmation text or application id) for the ledger.
        evidence = " ".join(str(report["confirmation"]).split())
        return mapped, "Application submitted; confirmation: " + evidence[:400]
    if mapped:
        return mapped, report.get("note", "") or status
    return "needs_input", json.dumps({"status": status, "missing": missing})


def question_deck(results):
    """[(question, [companies])] for every unanswered question, most shared first."""
    deck = {}
    for company, report in results:
        if report.get("status") not in ("needs_input", "review_flagged", "review_mismatch"):
            continue
        for question in report.get("missing", []):
            deck.setdefault(question, [])
            if company not in deck[question]:
                deck[question].append(company)
    return sorted(deck.items(), key=lambda item: -len(item[1]))


def run_one(job_id, state, profile_path, accounts_path, max_steps=16, submit=False):
    from .agent_client import operation_lock
    with operation_lock(state, 'applications', shared=True):
        return _run_one(job_id, state, profile_path, accounts_path, max_steps, submit)


def _run_one(job_id, state, profile_path, accounts_path, max_steps=16, submit=False):
    """Fill one job to Review in its own window of the shared browser; return the report.

    submit=True (only for jobs the user approved) clicks Submit once the Review page
    passes the audit, and reports submitted only on the site's confirmation."""
    from playwright.sync_api import sync_playwright
    from .queue import Queue

    state = Path(state)
    queue = Queue(state / "jobs.sqlite3")
    job = next(r for r in queue.rows() if r["id"] == job_id)
    profile = with_company_history(json.loads(Path(profile_path).read_text()), queue, job)
    # A private extension's mode (me/extensions/) may adjust the profile for this job.
    from . import extensions
    mode = os.environ.get("SPAM_MODE") or None
    try:
        profile = extensions.adjust_profile(profile, job, mode)
    except extensions.UnknownMode as error:
        return {"status": "needs_input", "missing": [str(error)]}
    if submit and job["status"] in NO_RESUBMIT:
        return {"status": job["status"], "missing": [f"Not resubmitting: status {job['status']} needs a person to check"]}
    if submit and job.get("job_key") and job["job_key"] in protected_job_keys(queue, exclude_id=job_id):
        return {"status": "needs_input", "missing": ["Not submitting: this posting is already applied, possibly submitted, or reserved for hand submit"]}
    from .targeting import role_key
    same = [r for r in queue.rows() if r["id"] != job_id and r["status"] in APPLIED_STATUSES and role_key(r) == role_key(job)]
    if submit and same and profile.get("targeting", {}).get("one_application_per_role"):
        return {"status": "needs_input", "missing": [f"Not submitting: same role already applied at {job['company']} "
                                                     f"({same[0]['role']}, {same[0]['location']})"]}
    requisition = job["job_key"].rsplit(":", 1)[-1]
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        cdp = browser.new_browser_cdp_session()
        page = next((pg for pg in ctx.pages if requisition and requisition in pg.url.lower()), None)
        target = None
        if page is None:
            # Our own new window, found by its target id: parallel workers open
            # windows at the same moment, and taking "any new page" grabbed another
            # worker's window and left ours blank.
            # Each job gets its own window, opened in the
            # background so it does not take the keyboard from whatever the user is doing.
            target = cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True, "background": True})["targetId"]
            page = window_for(ctx, target)
        report = None
        try:
            report = _fill(page, ctx, job, job_id, state, profile, accounts_path, max_steps, submit)
        finally:
            # Close the window even when the run fails: every run re-enters from the
            # posting, and windows left open piled up until the Mac ran out of RAM.
            # Keep it only when a person has to finish there: an emailed security code
            # to enter (Greenhouse) or a Submit to press by hand (Ashby).
            keep = os.environ.get("SPAM_KEEP_WINDOW") or (report or {}).get("status") in ("email_code_required", "spam_flagged", "hand_submit",
                                                                              "captcha_required", "account_signin_required")
            try:
                if keep:
                    pass
                elif len(ctx.pages) > 1:
                    page.close()
                if target and not keep:
                    cdp.send("Target.closeTarget", {"targetId": target})  # no-op once closed
            except Exception:
                pass
    status, note = queue_status(report)
    queue.mark(job_id, status, note)
    runs = state / "runs"
    runs.mkdir(exist_ok=True)
    report["mode"] = mode  # the submit of this job must run in the same mode
    (runs / f"{job_id}.json").write_text(json.dumps(report, indent=2))
    return report


def window_for(ctx, target, timeout=10):
    """The Playwright page for a CDP target this worker created; a new tab if it never appears."""
    checked = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for pg in ctx.pages:
            if pg in checked:
                continue
            try:
                session = ctx.new_cdp_session(pg)
                found = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                session.detach()
            except Exception:
                continue  # not attached yet: look again
            if found == target:
                return pg
            checked.add(pg)
        time.sleep(0.1)
    return ctx.new_page()


def scope_answers(profile, job):
    """Answers written for one employer or posting (an essay naming the company) carry `only_for`, a regex
    over "Company | Role". Elsewhere they are dropped, so the job stops and asks instead of reusing them."""
    target = f"{job.get('company', '')} | {job.get('role', '')}"
    answers = profile.get("answers", {})
    for label in [k for k, e in answers.items() if isinstance(e, dict) and e.get("only_for")]:
        if not re.search(answers[label]["only_for"], target, re.I):
            del answers[label]
    # `variants`: [{"when": regex over "Company | Role", "value": ...}], e.g. a Spring co-op starts in January.
    for entry in answers.values():
        if isinstance(entry, dict):
            for variant in entry.get("variants", []):
                if re.search(variant["when"], target, re.I):
                    entry["value"] = variant["value"]
                    break


def with_skills(profile, job):
    """Skills prompts get every verified skill, the posting's keywords first."""
    from .skills import ranked
    from .workday import POSTING
    entry = profile.setdefault("answers", {}).get("Type to Add Skills")
    if isinstance(entry, dict) and isinstance(entry.get("value"), dict) and "add_all" in entry["value"]:
        # The person's own skills: their saved list, else their profile's skills.
        saved = [c.get("search") for c in entry["value"].get("add_all") or [] if isinstance(c, dict)]
        facts = profile.get("facts", {}).get("skills") or profile.get("core", {}).get("skills") or []
        skills = saved or [s for s in facts if isinstance(s, str)]
        entry["value"] = ranked(skills, f"{job.get('role', '')}\n{POSTING['text']}", entry["value"].get("max", 20))


HAND_SUBMIT_NOTE = "Filled and checked. Ashby blocks automated submits: press Submit yourself in the open window"


def hand_submit_only(job):
    """Ashby flags automated submits as spam, so SPAM fills them and the user presses Submit."""
    return job.get("platform") == "ashby" or "ashbyhq.com" in str(job.get("url") or "")


def _fill(page, ctx, job, job_id, state, profile, accounts_path, max_steps, submit):
    """Enter the application from its posting and fill it (to Review, or Submit when approved)."""
    from .account_store import AccountStore
    from .runner import prepare, dismiss_cookie_overlays
    from .workday import open_application
    # A window the user is not looking at must keep running its scripts.
    ctx.new_cdp_session(page).send("Emulation.setFocusEmulationEnabled", {"enabled": True})

    def enter():
        store = AccountStore(accounts_path, state)
        try:
            try:
                return open_application(page, job["url"], store, on_event=lambda text: print(text, flush=True))
            except Exception as exc:
                if "ERR_ABORTED" not in str(exc):
                    raise
                page.wait_for_timeout(3000)  # a redirect aborted the first load (Northwestern Mutual)
                return open_application(page, job["url"], store, on_event=lambda text: print(text, flush=True))
        finally:
            store.db.close()

    # Always re-enter from the posting: a window left open from an earlier run
    # can hold an expired Workday session, and its pages then fail to save
    # ("Something went wrong") or ask for the password again.
    for attempt in range(2):
        report = enter()
        if report is None:
            page.wait_for_timeout(1000)
            dismiss_cookie_overlays(page)
            with_skills(profile, job)
            scope_answers(profile, job)
            from .tailor import apply as use_tailored
            if (tailored := use_tailored(profile, job, state)):
                print(f"Uploading the approved tailored resume {tailored}", flush=True)
            report = prepare(page, profile, max_steps=max_steps, on_event=lambda text: print(text, flush=True),
                             submit=submit and not hand_submit_only(job), bug_dir=state / "bugs")
        expired = (report.get("missing") == ["Navigation made no observable progress"]
                   and re.search(r"something went wrong", page.locator("body").inner_text(), re.I))
        if not expired:
            break
        print("Workday could not save the page; entering again from the posting", flush=True)
    if submit and hand_submit_only(job) and report.get("status") == "ready_for_review":
        # Filled and past both Review gates; the user presses Submit in the window left open.
        report = {**report, "status": "hand_submit", "note": HAND_SUBMIT_NOTE}
    if report.get("status") in ("ready_for_review", "hand_submit"):
        # What the site will receive, for the user's yes/no before submitting (private).
        review = state / "runs" / f"{job_id}.review.txt"
        review.parent.mkdir(exist_ok=True)
        from .review_check import form_snapshot, is_form_page
        # One-page forms (Greenhouse, Ashby) hold their answers in the controls.
        # An employer page can embed the form in a frame (Greenhouse's job-board iframe).
        form = next((f for f in [page.main_frame, *page.frames] if _is_form(is_form_page, f)), None)
        review.write_text(form_snapshot(form) if form else page.locator("body").inner_text())
        review.chmod(0o600)
    return report


def _is_form(is_form_page, frame):
    try:
        return is_form_page(frame)
    except Exception:
        return False  # a detached or cross-origin frame that cannot be read


def close_blank_windows():
    """Close leftover empty windows (keep one so the browser stays open)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.connect_over_cdp(CDP).contexts[0]
        blanks = [pg for pg in ctx.pages if pg.url in ("about:blank", "")]
        keep = 1 if len(blanks) == len(ctx.pages) else 0
        for pg in blanks[keep:]:
            pg.close()


def run_batch(jobs, state, parallel=2, timeout=900, submit=False):
    from .agent_client import operation_lock
    with operation_lock(state, 'applications', shared=True):
        return _run_batch(jobs, state, parallel, timeout, submit)


def _run_batch(jobs, state, parallel=2, timeout=900, submit=False):
    """Run jobs through `job-agent run-one`, `parallel` at a time; return [(job, report)]."""
    ensure_browser(state)
    close_blank_windows()
    runs = Path(state) / "runs"
    runs.mkdir(exist_ok=True)
    pending, running, results = list(jobs), [], []
    while pending or running:
        while pending and len(running) < parallel:
            job = pending.pop(0)
            log = open(runs / f"{job['id']}.log", "w")  # the worker's own log, for diagnosing a crash
            proc = subprocess.Popen([sys.executable, "-m", "job_agent.cli", "--state", str(state), "run-one", job["id"]]
                                    + (["--submit"] if submit else []),
                                    stdout=log, stderr=subprocess.STDOUT)
            running.append((job, proc, time.time()))
            time.sleep(3)  # stagger window openings
        for entry in list(running):
            job, proc, started = entry
            if proc.poll() is None and time.time() - started < timeout:
                continue
            if proc.poll() is None:
                proc.kill()
            running.remove(entry)
            report_path = Path(state) / "runs" / f"{job['id']}.json"
            report = json.loads(report_path.read_text()) if report_path.exists() and report_path.stat().st_mtime > started - 1 \
                else {"status": "needs_input", "missing": ["Run crashed or timed out"]}
            results.append((job, report))
        time.sleep(1)
    try:
        close_blank_windows()
    except Exception:
        pass
    return results
