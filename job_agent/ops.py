"""Operator commands the chat console's agent (or a person) uses to run SPAM day to day:
pull new postings, pick and fill jobs, read Review pages, group stuck questions, save
answers, finish emailed security codes, and open account-verification links.
None of these submits a new application; submitting stays the Approve button."""
import collections
import json
import os
import re
import time
from pathlib import Path
from urllib.request import urlopen

from .queue import Queue, SOURCE, company_key, parse_list
from .targeting import apply_targeting, ranked

CDP = "http://127.0.0.1:9333"
APPLIED = ("submitted", "applied", "already_applied", "submit_uncertain", "hand_submit")
CAPPED = ("submitted", "applied", "review", "already_applied", "submit_uncertain", "spam_flagged", "hand_submit")


def pull(state, profile_path):
    """New postings from the Simplify list, the speedyapply SWE/AI lists and intern-list.com, then targeting."""
    from .sources import SPEEDYAPPLY, parse_speedyapply
    queue = Queue(Path(state) / "jobs.sqlite3")
    jobs = list(parse_list(urlopen(SOURCE, timeout=30).read().decode()))
    report = {"simplify_parsed": len(jobs), "simplify_added": queue.ingest(jobs) if jobs else 0}
    categories = {"speedyapply-swe": "Software Engineering", "speedyapply-ai": "Data Science, AI & Machine Learning"}
    for name, url in SPEEDYAPPLY.items():
        try:
            rows = list(parse_speedyapply(urlopen(url, timeout=30).read().decode(), categories[name]))
            report[name + "_added"] = queue.add(rows, name)
        except OSError as error:
            report[name + "_error"] = str(error)
    # intern-list.com SWE and ML/AI lists: only roles that resolve to the employer's own posting are queued.
    from .internlist import pull as pull_internlist
    try:
        report.update(pull_internlist(queue))
    except Exception as error:  # a changed page must not stop the other sources
        report["intern-list_error"] = str(error)[:200]
    report["platforms_resolved"] = queue.resolve_platforms(board_exists)
    prefs = json.loads(Path(profile_path).read_text()).get("targeting") or {}
    if prefs:
        report["eligible"] = apply_targeting(queue, prefs)["eligible"]
    report["pulled_at"] = time.strftime("%Y-%m-%d %H:%M")
    (Path(state) / "last-pull.json").write_text(json.dumps(report, indent=2))
    return report


def board_exists(url):
    """True when a Greenhouse board API URL answers 200."""
    from urllib.error import URLError
    try:
        with urlopen(url, timeout=15) as response:
            return response.status == 200
    except (URLError, OSError, ValueError):
        return False


def big_tech(profile):
    targeting = profile.get("targeting", {})
    return set(targeting.get("manual_companies_before_2026_09_23", [])) | set(targeting.get("no_cap_companies", []))


def pick(state, profile_path, count=10, platforms=("workday", "greenhouse", "lever", "icims"), mode=None, min_fit=False):
    """Next jobs to fill: newest first, one per company per batch, at most 4 per company
    (big tech uncapped), skipping companies with a job still waiting on an answer.
    With a mode (a private extension), only the queued jobs that mode claims, under the same cap.
    Jobs career-ops scored below the fit threshold (min_fit, else targeting.min_fit_score, default 3) are left
    out; unscored jobs stay pickable. min_fit=None turns the filter off."""
    from .fit import min_score
    from . import extensions
    profile = json.loads(Path(profile_path).read_text())
    queue = Queue(Path(state) / "jobs.sqlite3")
    uncapped = {company_key(c) for c in big_tech(profile)}
    from .batch import applied_counts
    taken = applied_counts(queue)
    waiting = {company_key(r[0]) for r in queue.db.execute("SELECT company FROM jobs WHERE status='needs_input'")}
    threshold = min_score(profile) if min_fit is False else min_fit
    chosen, per = [], collections.Counter()
    for job in ranked(queue):
        if threshold is not None and job.get("fit_score") is not None and job["fit_score"] < threshold:
            continue
        company = company_key(job["company"])
        if job["platform"] not in platforms or not extensions.pick_filter(job, mode):
            continue
        if company not in uncapped and (company in waiting or per[company] or taken[company] >= 4):
            continue
        chosen.append(job)
        per[company] += 1
        if len(chosen) >= count:
            break
    return chosen


def recorded_mode(state, job):
    """The mode a job was last filled in (from its run record), else the mode that claims it."""
    from . import extensions
    try:
        mode = json.loads((Path(state) / "runs" / f"{job['id']}.json").read_text()).get("mode")
    except (OSError, ValueError):
        mode = None
    return mode or extensions.mode_tagged(job)


def review_report(state, profile_path, ids=None):
    """Each job at Review: the answer check (against the profile it was filled with) and
    the question/answer lines to read before asking the user to approve."""
    from .review_check import check_review
    from . import extensions
    profile = json.loads(Path(profile_path).read_text())
    queue = Queue(Path(state) / "jobs.sqlite3")
    ids = ids or [r[0] for r in queue.db.execute("SELECT id FROM jobs WHERE status='review'")]
    out = []
    for job_id in ids:
        row = queue.db.execute("SELECT id, company, role, status, note FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            continue
        path = Path(state) / "runs" / f"{job_id}.review.txt"
        text = path.read_text(errors="ignore") if path.exists() else ""
        mode = recorded_mode(state, dict(row))
        try:
            used = extensions.adjust_profile(profile, dict(row), mode)
            # The same per-posting answers the fill used (only_for, variants).
            from copy import deepcopy
            from .batch import scope_answers
            used = deepcopy(used)
            scope_answers(used, dict(row))
        except extensions.UnknownMode as error:
            used, text = None, ""
            out.append({"id": job_id, "company": row["company"], "role": row["role"], "status": row["status"],
                        "mode": mode, "problems": [str(error)], "lines": []})
            continue
        prior = queue.db.execute("SELECT COUNT(*) FROM jobs WHERE company=? AND id!=? AND status IN (%s)" % ",".join("?" * len(APPLIED)),
                                 (row["company"], job_id, *APPLIED)).fetchone()[0]
        out.append({"id": job_id, "company": row["company"], "role": row["role"], "status": row["status"],
                    "mode": mode, "prior_at_company": prior, "tailored_resume": _tailored(state, job_id), "problems": check_review(text, used) if text else ["no review snapshot"],
                    "lines": [line.strip() for line in text.splitlines() if line.strip()][:400]})
    return out


def _tailored(state, job_id):
    from .tailor import tailored_dir
    try:
        meta = json.loads((tailored_dir(state, job_id) / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    return {"pdf": meta.get("pdf"), "approved": bool(meta.get("approved"))}


def deck(state):
    """Stuck jobs grouped by question, with the options each site offered."""
    queue = Queue(Path(state) / "jobs.sqlite3")
    questions = collections.OrderedDict()
    for job_id, company in queue.db.execute("SELECT id, company FROM jobs WHERE status='needs_input'"):
        try:
            report = json.loads((Path(state) / "runs" / f"{job_id}.json").read_text())
        except (OSError, ValueError):
            continue
        options = report.get("options") or {}
        for missing in report.get("missing", []):
            key = re.sub(r" \((?:saved answer not among options|no saved answer matches its options|selection did not apply|"
                         r"no text answer saved|did not stick|validation failed|boolean answer required)\)$", "", missing)
            entry = questions.setdefault(key, {"question": key, "jobs": [], "companies": [], "options": None, "reason": missing[len(key):].strip()})
            entry["jobs"].append(job_id)
            entry["companies"].append(company)
            entry["options"] = entry["options"] or options.get(key)
    return list(questions.values())


def answer(profile_path, question, value, pattern=None, note="", choice_only=True):
    """Save a verified answer: an exact label, or a regex rule (choice controls only unless
    choice_only=False, which adds a plain-fact text rule)."""
    from .profile_editor import normalize, with_answer, write_profile
    if not pattern:
        before = json.loads(Path(profile_path).read_text())
        # Report what an exact answer replaces: a plain value can silently lose a better saved
        # rule (2026-09-24: "Location (City)" lost its search pick).
        replaced = next((e.get("value") if isinstance(e, dict) else e for k, e in before.get("answers", {}).items()
                         if normalize(question) in [normalize(k), *[normalize(a) for a in (e.get("aliases", []) if isinstance(e, dict) else [])]]), None)
        data = with_answer(before, question, value)
        if note:
            key = next(k for k, e in data["answers"].items() if normalize(question) in
                       [normalize(k), *[normalize(a) for a in (e.get("aliases", []) if isinstance(e, dict) else [])]])
            data["answers"][key]["note"] = note
        write_profile(profile_path, data)
        result = {"saved": "exact", "question": question}
        if replaced is not None and replaced != value:
            result["replaced"] = replaced
        return result
    data = json.loads(Path(profile_path).read_text())
    rule = {"match": pattern, "value": value, "note": note or "saved from the SPAM console"}
    re.compile(pattern)
    key = "patterns" if choice_only else "text_patterns"
    data.setdefault(key, []).insert(0, rule)
    write_profile(profile_path, data)
    return {"saved": key, "match": pattern}


def workers():
    """run-one workers alive right now (from this console or any other session)."""
    import subprocess
    out = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout
    return [{"id": m.group(1), "submit": bool(m.group(2))}
            for m in (re.search(r"run-one (\w{20})( --submit)?", line) for line in out.splitlines()) if m]


def health(state, recent=15):
    """What a new session checks before acting: live workers, the browser, the queue, and
    the latest status changes (anything you did not do was another session)."""
    queue = Queue(Path(state) / "jobs.sqlite3")
    report = {"workers": workers()}
    try:
        pages = json.loads(urlopen(CDP + "/json/list", timeout=3).read())
        report["browser"] = {"up": True, "windows": sum(1 for p in pages if p.get("type") == "page"),
                             "code_windows": [p["url"][:90] for p in pages if p.get("type") == "page" and "greenhouse" in p.get("url", "")]}
    except OSError:
        report["browser"] = {"up": False}
    report["counts"] = dict(queue.db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    report["waiting_code"] = [f"{r['id']} {r['company']} | {r['role']}" for r in queue.db.execute(
        "SELECT id, company, role FROM jobs WHERE status='needs_input' AND note LIKE '%email_code_required%'")]
    report["recent"] = [f"{r['at']} {r['company'][:24]} {r['old_status']}->{r['new_status']}" for r in queue.db.execute(
        "SELECT s.at, j.company, s.old_status, s.new_status FROM status_log s JOIN jobs j ON j.id=s.job_id"
        " WHERE s.old_status IS NOT s.new_status ORDER BY s.rowid DESC LIMIT ?", (recent,))]
    try:
        report["last_pull"] = json.loads((Path(state) / "last-pull.json").read_text()).get("pulled_at")
    except (OSError, ValueError):
        report["last_pull"] = None
    return report


def hold(state, job_id, reason):
    """Take a job off the Approve list (Review -> needs_input) until it is refilled."""
    queue = Queue(Path(state) / "jobs.sqlite3")
    row = queue.db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row["status"] != "review":
        raise SystemExit("Only a job at Review can be held")
    queue.mark(job_id, "needs_input", f"Held by the operator: {reason}")
    return {"held": job_id}


def confirm(state, job_id, email, at):
    """Record the employer's confirmation email as evidence (state/confirmations.json)."""
    queue = Queue(Path(state) / "jobs.sqlite3")
    if not queue.db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
        raise SystemExit("Unknown job id")
    time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S")
    path = Path(state) / "confirmations.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[job_id] = {"at": at[:19], "email": email}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(path)
    return {"recorded": job_id, "total": len(data)}


def decisions(profile_path, match=""):
    """Every saved answer the user decided (its note cites the user), so a new question
    is answered the way the user already answered similar ones."""
    profile = json.loads(Path(profile_path).read_text())
    name = str(profile.get("facts", {}).get("name") or "").split()
    decided = re.compile(r"\b(user" + "".join("|" + re.escape(n) for n in name[:1]) + r")\b", re.I)
    rows = [("answer", label, e.get("value"), e.get("note")) for label, e in profile.get("answers", {}).items()
            if isinstance(e, dict) and decided.search(str(e.get("note", "")))]
    for key in ("patterns", "text_patterns"):
        rows += [(key, r.get("match"), r.get("value"), r.get("note")) for r in profile.get(key, [])
                 if decided.search(str(r.get("note", "")))]
    rows += [("targeting", k, None, v) for k, v in profile.get("targeting", {}).items() if k.startswith("note")]
    wanted = re.compile(match, re.I) if match else None
    return [{"kind": kind, "question": q, "value": v, "note": n} for kind, q, v, n in rows
            if not wanted or wanted.search(f"{q} {n}")]


def _browser():
    from playwright.sync_api import sync_playwright
    return sync_playwright()


def enter_code(state, job_id, code):
    """Finish an approved Greenhouse submit that stopped on an emailed 8-character code."""
    queue = Queue(Path(state) / "jobs.sqlite3")
    row = queue.db.execute("SELECT url, status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise SystemExit("Unknown job id")
    posting = re.search(r"/jobs/(\d+)", row["url"])
    if not posting or not re.fullmatch(r"[A-Za-z0-9]{8}", code):
        raise SystemExit("Needs a Greenhouse job and an 8-character code")
    with _browser() as p:
        ctx = p.chromium.connect_over_cdp(CDP).contexts[0]
        # Boards that redirect to a company site are filled on Greenhouse's embed form (…?token=<id>).
        page = next((pg for pg in ctx.pages if (f"/jobs/{posting.group(1)}" in pg.url or f"token={posting.group(1)}" in pg.url)
                     and "confirmation" not in pg.url), None)
        if page is None or not page.locator("#security-input-0").count():
            return {"status": "no open code window", "hint": "resubmit the approved job to get a fresh code"}
        for i in range(8):
            page.locator(f"#security-input-{i}").fill("")
        page.locator("#security-input-0").click()
        page.keyboard.type(code, delay=60)
        page.get_by_role("button", name=re.compile(r"^submit application$", re.I)).click()
        for _ in range(25):
            page.wait_for_timeout(1000)
            if "/confirmation" in page.url:
                queue.mark(job_id, "submitted", f"Application submitted after entering the emailed security code; landed on {page.url.split('greenhouse.io')[-1]}")
                page.close()
                return {"status": "submitted"}
            if re.search(r"incorrect security code|invalid security code", page.locator("body").inner_text(), re.I):
                return {"status": "code rejected"}
    return {"status": "no confirmation yet"}


def open_link(url):
    """Open an account-verification link in a background window, report where it landed, close it."""
    if not re.match(r"^https://[\w.-]+\.(?:myworkdayjobs\.com|myworkday\.com|myworkdaysite\.com)/", url):
        raise SystemExit("Only Workday account-verification links are opened here")
    with _browser() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        cdp = browser.new_browser_cdp_session()
        from .batch import window_for
        target = cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True, "background": True})["targetId"]
        page = window_for(ctx, target)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            # Workday shows "Invalid Token" once the link has been used; the account is verified either way.
            return {"landed": page.url[:120], "text": " ".join(page.locator("body").inner_text().split())[:160]}
        finally:
            cdp.send("Target.closeTarget", {"targetId": target})
