import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from .queue import Queue, SOURCE, parse_list


def targeting_prefs(path):
    return json.loads(path.read_text()).get("targeting") if path.exists() else None


def main():
    parser = argparse.ArgumentParser(description="Local internship application workbench (development preview)")
    parser.add_argument("--state", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="Import jobs without opening application sites")
    sync.add_argument("--file", type=Path)
    sync.add_argument("--profile", type=Path, default=None)
    commands.add_parser("status")
    commands.add_parser("browser", help="Keep the visible browser open for batch workers")
    chat = commands.add_parser("chat", help="Local chat console: Claude Code or ChatGPT operate SPAM beside a live dashboard")
    chat.add_argument("--port", type=int, default=8770)
    ops_pull = commands.add_parser("pull", help="Pull new postings (Simplify + speedyapply SWE/AI) and apply targeting")
    ops_pull.add_argument("--profile", type=Path, default=None)
    ops_pick = commands.add_parser("pick", help="List the next jobs to fill (one per company, cap 4, big tech uncapped)")
    ops_fill = commands.add_parser("fill", help="Pick the next jobs and fill them to Review (never submits)")
    for command in (ops_pick, ops_fill):
        command.add_argument("--count", type=int, default=10)
        command.add_argument("--platforms", default="workday,greenhouse,lever,icims")
        command.add_argument("--mode", help="A private extension's mode (only its jobs, filled with its profile changes)")
        command.add_argument("--profile", type=Path, default=None)
        command.add_argument("--min-fit", type=float, default=False, help="Skip jobs career-ops scored below this (default: targeting.min_fit_score or 3)")
        command.add_argument("--no-fit", action="store_true", help="Ignore career-ops fit scores")
    ops_fill.add_argument("--parallel", type=int, default=6)
    ops_score = commands.add_parser("score", help="Score queued jobs 1-5 against your resume with career-ops (never fills or submits)")
    ops_score.add_argument("--ids", nargs="*")
    ops_score.add_argument("--count", type=int, default=10)
    ops_score.add_argument("--rescore", action="store_true")
    ops_score.add_argument("--profile", type=Path, default=None)
    ops_tailor = commands.add_parser("tailor", help="Build a per-job resume from your LaTeX source (reorder/reword only, fact-gated); used only after --approve")
    ops_tailor.add_argument("ids", nargs="*")
    ops_tailor.add_argument("--approve", action="store_true", help="Approve the tailored resume(s) for upload")
    ops_tailor.add_argument("--unapprove", action="store_true")
    ops_tailor.add_argument("--source", type=Path, help="Save the LaTeX source of your default resume")
    ops_tailor.add_argument("--profile", type=Path, default=None)
    ops_review = commands.add_parser("review", help="Answer check and Q/A lines for jobs at Review")
    ops_review.add_argument("ids", nargs="*")
    ops_review.add_argument("--profile", type=Path, default=None)
    ops_review.add_argument("--brief", action="store_true", help="Only the answer check")
    commands.add_parser("deck", help="Stuck jobs grouped by question, with the options sites offered")
    ops_answer = commands.add_parser("answer", help="Save a verified answer (exact label, or --pattern regex for choice questions)")
    ops_answer.add_argument("question")
    ops_answer.add_argument("value", help="Text, or JSON (e.g. true, [\"Yes\"], {\"matches\": \"^yes\"})")
    ops_answer.add_argument("--pattern", help="Regex over question labels instead of an exact label")
    ops_answer.add_argument("--text", action="store_true", help="With --pattern: a plain-fact rule for text boxes")
    ops_answer.add_argument("--fact", help="Map this wording to a core fact (job_agent/facts.py) as a private, shareable library rule; VALUE is ignored")
    ops_answer.add_argument("--invert", action="store_true", help="With --fact: the wording asks the opposite of the yes/no fact")
    ops_answer.add_argument("--note", default="")
    ops_answer.add_argument("--profile", type=Path, default=None)
    ops_share = commands.add_parser("share-library", help="Write your shareable question rules (wording + fact, never answers) to a file to preview and send")
    ops_share.add_argument("--out", type=Path, default=None)
    ops_share.add_argument("--pr", action="store_true", help="Open a pull request adding them to the shared library (needs gh, signed in)")
    ops_share.add_argument("--profile", type=Path, default=None)
    ops_merge = commands.add_parser("merge-library", help="Maintainer: add a friend's exported rules to library/community.json (checked, deduplicated)")
    ops_merge.add_argument("file", type=Path)
    ops_code = commands.add_parser("code", help="Enter an emailed Greenhouse security code for an approved submit")
    ops_code.add_argument("id")
    ops_code.add_argument("code")
    ops_mail = commands.add_parser("mail", help="Search the user's Gmail (read-only); prints matching messages as JSON lines")
    ops_mail.add_argument("query", help="Gmail search syntax, e.g. 'subject:\"verify your candidate account\" newer_than:1d'")
    ops_mail.add_argument("--limit", type=int, default=20)
    ops_codes = commands.add_parser("codes", help="Fetch emailed Greenhouse security codes from Gmail and enter them into waiting submits")
    ops_codes.add_argument("--messages-stdin", action="store_true", help="Read Gmail search result messages as a JSON array from stdin")
    ops_verify = commands.add_parser("verify-accounts", help="Open emailed Workday account-verification links for jobs stopped on them")
    ops_verify.add_argument("--days", type=int, default=2)
    ops_mailconfirm = commands.add_parser("confirmations", help="Match employer confirmation emails in Gmail to submitted jobs")
    ops_mailconfirm.add_argument("--days", type=int, default=3)
    assessment = commands.add_parser("assessments", help="Track coding assessments, take-homes, games, and video interviews")
    assessment_commands = assessment.add_subparsers(dest="assessment_command", required=True)
    assessment_commands.add_parser("list", help="List assessment cards as JSON")
    assessment_scan = assessment_commands.add_parser("scan", help="Scan recent Gmail for assessment invitations and completions")
    assessment_scan.add_argument("--days", type=int, default=45)
    assessment_add = assessment_commands.add_parser("add", help="Add an assessment card manually")
    assessment_add.add_argument("company")
    assessment_add.add_argument("title")
    assessment_add.add_argument("--due")
    assessment_add.add_argument("--kind", default="Assessment")
    assessment_add.add_argument("--link")
    assessment_update = assessment_commands.add_parser("update", help="Edit or move an assessment card")
    assessment_update.add_argument("id")
    assessment_update.add_argument("--status", choices=("todo", "in_progress", "done", "expired", "skipped"))
    assessment_update.add_argument("--due")
    assessment_update.add_argument("--note")
    assessment_update.add_argument("--company")
    assessment_update.add_argument("--title")
    assessment_update.add_argument("--link")
    ops_link = commands.add_parser("open-link", help="Open a Workday account-verification link in a background window")
    ops_link.add_argument("url")
    commands.add_parser("health", help="Live workers, browser, queue counts and the latest status changes")
    ops_hold = commands.add_parser("hold", help="Take a job at Review off the Approve list until it is refilled")
    ops_hold.add_argument("id")
    ops_hold.add_argument("reason")
    ops_confirm = commands.add_parser("confirm", help="Record an employer confirmation email as evidence")
    ops_confirm.add_argument("id")
    ops_confirm.add_argument("--email", required=True, help="The email's subject line")
    ops_confirm.add_argument("--at", required=True, help="When it arrived, YYYY-MM-DDTHH:MM:SS local time")
    ops_decisions = commands.add_parser("decisions", help="Answers the user decided, to answer similar questions the same way")
    ops_decisions.add_argument("match", nargs="?", default="", help="Regex over question and note")
    ops_decisions.add_argument("--profile", type=Path, default=None)
    deck = commands.add_parser("answer-deck", help="Review unanswered questions and preview profile edits locally")
    deck.add_argument("--profile", type=Path, default=None)
    deck.add_argument("--port", type=int, default=8765)
    log_viewer = commands.add_parser("log-viewer", help="Browse the status log and each job's run artifacts locally")
    log_viewer.add_argument("--port", type=int, default=8766)
    agent = commands.add_parser("agent", help="Configure economical CLI assistance and draft unknown answers")
    agent.add_argument("action", choices=["status", "configure", "suggest", "check"])
    agent.add_argument("--provider", choices=["codex", "claude"], default="codex")
    agent.add_argument("--model", default=None)
    agent.add_argument("--profile", type=Path, default=None)
    agent.add_argument("--disable", action="store_true")
    agent.add_argument("--retry", action="store_true")
    repair = commands.add_parser("agent-repair", help="Propose one bounded function patch from a redacted diagnostic")
    repair.add_argument("path")
    repair.add_argument("function")
    repair.add_argument("--diagnostic", type=Path, required=True)
    repair_check=commands.add_parser("agent-repair-check", help="Test a proposed repair in an isolated checkout")
    repair_check.add_argument("id")
    repair_check.add_argument("tests", nargs="+")
    repair_apply=commands.add_parser("agent-repair-apply", help="Apply a reviewed repair that passed its fixture checks")
    repair_apply.add_argument("id")
    one = commands.add_parser("run-one", help="Fill one queued job to its Review page")
    one.add_argument("id")
    one.add_argument("--submit", action="store_true", help="Submit after the Review audit passes (user-approved jobs only)")
    one.add_argument("--profile", type=Path, default=None)
    batch = commands.add_parser("batch", help="Fill the newest jobs to Review in parallel windows")
    batch.add_argument("--submit", action="store_true", help="Submit explicitly approved jobs after the Review audit and answer check pass")
    batch.add_argument("--count", type=int, default=6)
    batch.add_argument("--parallel", type=int, default=2)
    batch.add_argument("--company-cap", type=int, default=4)  # the user's choice
    batch.add_argument("--platforms", default="workday", help="Comma-separated, e.g. workday,greenhouse")
    batch.add_argument("--ids", nargs="*", help="Specific job ids instead of the newest picks")
    batch.add_argument("--profile", type=Path, default=None)
    batch.add_argument("--assist", action="store_true", help="Draft blocked answers after filling; never auto-save or submit them")
    tracker = commands.add_parser("import-tracker", help="Import Simplify's Tracked Jobs CSV export")
    tracker.add_argument("file", type=Path)
    tracker.add_argument("--profile", type=Path, default=None)
    targets = commands.add_parser("targets", help="Apply profile targeting and list jobs in apply order")
    targets.add_argument("--profile", type=Path, default=None)
    targets.add_argument("--top", type=int, default=15)
    audit = commands.add_parser("profile-audit", help="Validate saved answers and report unresolved fields")
    audit.add_argument("--profile", type=Path, default=None)
    commands.add_parser("gmail-login", help="Store a Gmail app password in Keychain")
    account = commands.add_parser("account-login", help="Store an existing hiring-site credential in Keychain")
    account.add_argument("origin", help="Hiring-site HTTPS origin, e.g. https://example.wd5.myworkdayjobs.com")
    scan = commands.add_parser("inspect", help="Inspect a job; optionally fill exact approved answers")
    scan.add_argument("id")
    scan.add_argument("--profile", type=Path, default=None)
    scan.add_argument("--fill", action="store_true")
    prep = commands.add_parser("prepare", help="Fill saved answers and advance supported pages; stop before submission")
    prep.add_argument("id")
    prep.add_argument("--profile", type=Path, default=None)
    prep.add_argument("--max-steps", type=int, default=12)
    prep.add_argument("--decision-engine", choices=["laya", "rules"], default="laya")
    prep.add_argument("--submit", action="store_true", help="Submit when the review page is reached and nothing is missing")
    setup_cmd = commands.add_parser("setup", help="First-time setup: create me/ and open the Setup page (or let your AI agent do it)")
    setup_cmd.add_argument("--port", type=int, default=8771)
    setup_cmd.add_argument("--no-page", action="store_true", help="Only create me/ and print what is still needed")
    facts_cmd = commands.add_parser("facts", help="Core facts: status, draft from a resume, or confirm values")
    facts_sub = facts_cmd.add_subparsers(dest="facts_command", required=True)
    facts_sub.add_parser("status", help="Every core fact, its value, and whether the user confirmed it")
    facts_draft = facts_sub.add_parser("draft", help="Ask your AI to read the resume; prints a draft, saves nothing")
    facts_draft.add_argument("resume", type=Path)
    facts_set = facts_sub.add_parser("confirm", help="Save facts the user confirmed: KEY VALUE pairs (VALUE as JSON or text)")
    facts_set.add_argument("pairs", nargs="+")
    facts_target = facts_sub.add_parser("targeting", help="Save targeting as JSON, e.g. '{\"categories\": [\"Software Engineering\"]}'")
    facts_target.add_argument("json")
    mig_check = commands.add_parser("migrate-check", help="Replay recent Review pages: would any answer change after moving into me/ with core facts?")
    mig_check.add_argument("--root", type=Path, default=Path.cwd())
    mig_check.add_argument("--sample", type=int, default=40)
    mig_check.add_argument("--with-core", action="store_true", help="Also derive core facts from the old profile (as the switch-over will)")
    mig = commands.add_parser("migrate", help="Copy root profile.json, accounts.json and state/ into me/ (after a backup, only when migrate-check is clean)")
    mig.add_argument("--root", type=Path, default=Path.cwd())
    mig.add_argument("--from", dest="source", type=Path, default=None,
                     help="An older SPAM folder to copy from (profile.json, accounts.json, state/, me/extensions) into this checkout's me/")
    commands.add_parser("account", help="Save the default hiring-site login (asks for the password without showing it)")
    draft = commands.add_parser("draft")
    draft.add_argument("question")
    draft.add_argument("--profile", type=Path, default=None)
    args = parser.parse_args()
    from . import paths
    if args.state is None:
        args.state = paths.state()
    if getattr(args, "profile", "absent") is None:
        args.profile = paths.profile()
    profile_file = args.profile if isinstance(getattr(args, "profile", None), Path) else paths.profile()
    if args.command in ("migrate-check", "migrate"):
        from . import migrate
        if args.command == "migrate":
            if args.source:
                print(json.dumps(migrate.run(args.source, dest=args.root), indent=2))
            else:
                print(json.dumps(migrate.run(args.root), indent=2))
            return
        profile = json.loads((args.root / "profile.json").read_text())
        if args.with_core:
            core = migrate.core_from_facts(profile.get("facts", {}))
            profile = {**profile, "core": core, "confirmed_facts": sorted(core)}
        report = migrate.check(args.root, profile, args.sample)
        print(f"{report['questions']} questions replayed; {len(report['changed'])} would change; "
              f"{len(report['newly_answered'])} newly answered by the library")
        for label, entries in (("CHANGED", report["changed"]), ("NEW", report["newly_answered"])):
            for entry in entries:
                print(f"{label}: {entry['question'][:120]}\n   before {entry['before']}\n   after  {entry['after']}")
        raise SystemExit(1 if report["changed"] else 0)
    if args.command == "setup":
        from . import setup
        print(json.dumps(setup.start()))
        for check in setup.checks():
            print(("ok   " if check["ok"] else "TODO ") + check["name"] + ("" if check["ok"] else f"  ->  {check['fix']}"))
        if not args.no_page:
            from .setup_ui import serve
            serve(args.port)
        return
    if args.command != "setup" and not profile_file.exists():
        print(f"No profile found at {profile_file}.\nRun: job-agent setup   (or ask your AI agent: \"set me up\")")
        raise SystemExit(2)
    os.umask(0o077)
    args.state.mkdir(parents=True, exist_ok=True)
    queue = Queue(args.state / "jobs.sqlite3")
    if getattr(args, "submit", False) and os.environ.get("SPAM_CHAT_AGENT"):
        raise SystemExit("Submitting is the user's Approve button in the chat console; agents cannot run --submit.")
    if args.command == "facts":
        from . import setup
        if args.facts_command == "status":
            state = setup.status()
            for group in state["groups"]:
                print(f"== {group['title']}")
                for fact in group["facts"]:
                    mark = "ok " if fact["confirmed"] else "-- "
                    print(f"  {mark}{fact['key']:22} {json.dumps(fact['value']) if fact['value'] is not None else ''}   ({fact['ask']})")
            print(f"{len(state['missing'])} fact(s) not confirmed yet.")
        elif args.facts_command == "draft":
            print(json.dumps(setup.draft_from_resume(args.resume), indent=2))
            print("Draft only: show it to the user and save what they confirm with `job-agent facts confirm KEY VALUE ...`.")
        elif args.facts_command == "confirm":
            if len(args.pairs) % 2:
                raise SystemExit("Give KEY VALUE pairs")
            values = {}
            for key, raw in zip(args.pairs[::2], args.pairs[1::2]):
                try:
                    values[key] = json.loads(raw)
                except ValueError:
                    values[key] = raw
            try:
                print(json.dumps(setup.confirm(values)))
            except ValueError as error:
                raise SystemExit(f"Not saved: {error}")
        else:
            print(json.dumps(setup.save_targeting(json.loads(args.json))))
        return
    if args.command == "account":
        import getpass
        from . import setup
        email = input("Hiring-site email: ").strip()
        password = getpass.getpass("Password to use on hiring sites (hidden): ")
        print(json.dumps(setup.save_account(email, password)))
        return
    if args.command == "chat":
        from .chat_ui import serve
        serve(args.state, args.port)
        return
    if args.command == "assessments":
        from . import assessments
        if args.assessment_command == "list":
            print(json.dumps(assessments.board(args.state), indent=2))
        elif args.assessment_command == "scan":
            print(json.dumps(assessments.scan(args.state, days=args.days), indent=2))
        elif args.assessment_command == "add":
            print(json.dumps(assessments.add(args.state, args.company, args.title, args.due, args.kind, args.link), indent=2))
        elif args.assessment_command == "update":
            fields = {name: getattr(args, name) for name in ("status", "due", "note", "company", "title", "link")
                      if getattr(args, name) is not None}
            print(json.dumps(assessments.update(args.state, args.id, **fields), indent=2))
        return
    if args.command == "merge-library":
        from . import library
        print(json.dumps(library.merge(json.loads(args.file.read_text())), indent=2))
        return
    if args.command == "share-library":
        from . import library, paths
        profile_data = json.loads(Path(args.profile).read_text())
        if args.pr:
            result = library.contribute(profile_data, Path.cwd())
            print(json.dumps(result, indent=2) if result["pr"] else "Nothing new to share: every shareable wording is already in the library.")
            return
        exported = library.export(profile_data)
        out = args.out or paths.home() / "share" / f"library-{time.strftime('%Y%m%d')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"version": 1, "rules": exported["rules"]}, indent=2))
        print(f"{len(exported['rules'])} rule(s) written to {out}. Open it and read every line before you send it.")
        for rule in exported["refused"]:
            print(f"left out (contains something personal): {rule.get('id') or rule.get('match')}")
        return
    if args.command == "tailor":
        from . import tailor, ai as ai_settings
        try:
            if args.source:
                ai_settings.save_setting("resume_tex", str(args.source.expanduser().resolve()))
                print(f"resume source: {args.source}")
            if args.approve or args.unapprove:
                for job_id in args.ids:
                    meta = tailor.approve(args.state, job_id, approved=args.approve)
                    print(f"{job_id}  {'approved' if args.approve else 'not approved'}  {meta['pdf']}")
            elif args.ids:
                results = tailor.tailor_jobs(args.state, args.profile, args.ids)
                print(f"{sum('pdf' in r for r in results)}/{len(results)} built. Read each changes.md and PDF in "
                      f"{args.state / 'tailored'}, then: job-agent tailor --approve ID")
        except tailor.TailorError as error:
            raise SystemExit(str(error))
        return
    if args.command == "score":
        from . import fit
        try:
            results = fit.score_jobs(args.state, args.profile, ids=args.ids, count=args.count, rescore=args.rescore)
        except fit.FitError as error:
            raise SystemExit(str(error))
        scored = [r for r in results if r["score"] is not None]
        print(f"{len(scored)}/{len(results)} scored; reports in {args.state / 'fit'}")
        return
    if args.command in {"pull", "pick", "fill", "review", "deck", "answer", "code", "open-link",
                        "health", "hold", "confirm", "decisions", "mail", "codes", "confirmations", "verify-accounts"}:
        from . import ops
        if args.command == "pull":
            print(json.dumps(ops.pull(args.state, args.profile), indent=2))
        elif args.command in {"pick", "fill"}:
            jobs = ops.pick(args.state, args.profile, args.count, tuple(args.platforms.split(",")), args.mode,
                            None if args.no_fit else args.min_fit)
            for job in jobs:
                fit = job.get("fit_score")
                print(f"{job['id']}  {job['company'][:28]:28}  {job['role'][:60]:60}  {job['platform']}"
                      + (f"  fit {fit:g}" if fit is not None else ""))
            if args.command == "fill" and jobs:
                from .batch import run_batch
                if args.mode:
                    os.environ["SPAM_MODE"] = args.mode
                results = run_batch(jobs, args.state, parallel=max(1, min(args.parallel, 8)))
                print(json.dumps({"review": [f"{j['company']} | {j['role']}" for j, r in results if r.get("status") == "ready_for_review"],
                                  "other": [f"{j['company']} | {j['role']}: {r.get('status')}" for j, r in results
                                            if r.get("status") != "ready_for_review"]}, indent=2))
        elif args.command == "review":
            for item in ops.review_report(args.state, args.profile, args.ids):
                print(f"######## {item['company']} | {item['role']} | {item['id']} | mode={item['mode'] or '-'}"
                      f" | applied here before: {item['prior_at_company']}")
                print("  CHECK:", item["problems"] or "clean")
                resume = item.get("tailored_resume")
                print("  RESUME:", "default" if not resume else
                      f"tailored {'(approved: used by fills after approval)' if resume['approved'] else '(NOT approved: fills use the default)'} {resume['pdf']}")
                if not args.brief:
                    print("  " + "\n  ".join(line[:200] for line in item["lines"]))
        elif args.command == "deck":
            for entry in ops.deck(args.state):
                print(json.dumps({k: entry[k] for k in ("question", "companies", "options", "reason", "jobs")}, ensure_ascii=False))
        elif args.command == "answer" and args.fact:
            from . import library
            print(json.dumps(library.learn(args.profile, args.question, args.fact, invert=args.invert)))
        elif args.command == "answer":
            try:
                value = json.loads(args.value)
            except ValueError:
                value = args.value
            print(json.dumps(ops.answer(args.profile, args.question, value, args.pattern, args.note, not args.text)))
        elif args.command == "code":
            print(json.dumps(ops.enter_code(args.state, args.id, args.code)))
        elif args.command == "open-link":
            print(json.dumps(ops.open_link(args.url)))
        elif args.command == "mail":
            from . import gmail
            for message in gmail.search(args.query, args.limit):
                print(json.dumps(message, ensure_ascii=False))
        elif args.command == "codes":
            from . import gmail
            messages = json.load(sys.stdin) if args.messages_stdin else None
            if messages is not None and not isinstance(messages, list):
                raise SystemExit("--messages-stdin requires a JSON array")
            print(json.dumps(gmail.fill_codes(args.state, messages=messages), indent=2))
        elif args.command == "verify-accounts":
            from . import gmail
            print(json.dumps(gmail.verify_accounts(args.state, days=args.days), indent=2))
        elif args.command == "confirmations":
            from . import gmail
            print(json.dumps(gmail.confirmations(args.state, args.days), indent=2))
        elif args.command == "health":
            print(json.dumps(ops.health(args.state), indent=2))
        elif args.command == "hold":
            print(json.dumps(ops.hold(args.state, args.id, args.reason)))
        elif args.command == "confirm":
            print(json.dumps(ops.confirm(args.state, args.id, args.email, args.at)))
        elif args.command == "decisions":
            for row in ops.decisions(args.profile, args.match):
                print(json.dumps(row, ensure_ascii=False))
        return
    if args.command in {"agent-repair-check", "agent-repair-apply"}:
        from .agent_repair import check, apply
        result=check(Path.cwd(),args.state,args.id,args.tests) if args.command=='agent-repair-check' else apply(Path.cwd(),args.state,args.id)
        print(json.dumps(result,indent=2))
    elif args.command == "agent-repair":
        from .agent_repair import propose
        result = propose(Path.cwd(), args.state, args.path, args.function, args.diagnostic.read_text())
        print(json.dumps(result, indent=2))
    elif args.command == "agent":
        from .agent_client import AgentClient, AgentConfig, save_config, status
        from dataclasses import asdict
        if args.action == "configure":
            config = AgentConfig(enabled=not args.disable, provider=args.provider,
                                 model=args.model or ("gpt-6-luna" if args.provider == "codex" else "haiku"))
            save_config(args.state, asdict(config))
            print(json.dumps(status(args.state), indent=2))
        elif args.action == "status":
            print(json.dumps(status(args.state), indent=2))
        elif args.action == "check":
            client = AgentClient(args.state)
            schema = {"type": "object", "additionalProperties": False, "required": ["ready"], "properties": {"ready": {"type": "boolean"}}}
            def validate(result):
                if result != {"ready": True}:
                    raise ValueError("Invalid connection check")
            result = client.request("connection_check", {"instruction": "Return ready true. No personal data."}, schema, validate, retry=args.retry)
            print(json.dumps({"ready": True, "model": client.config.model, "cached": result["cached"], "usage": result.get("usage")}, indent=2))
        else:
            from .agent_answers import suggest
            from .answer_deck import AnswerDeck
            result = suggest(AnswerDeck(args.state, args.profile), retry=args.retry)
            print(json.dumps({"proposals": len(result["proposals"]), "calls": result["calls"],
                              "saved_to": str(args.state / "agent" / "suggestions.json"), "needs_review": True}))
    elif args.command == "answer-deck":
        from .answer_deck import serve
        serve(args.state, args.profile, args.port)
    elif args.command == "log-viewer":
        from .log_viewer import serve
        serve(args.state, args.port)
    elif args.command in {"gmail-login", "account-login"}:
        import getpass
        from .accounts import Vault
        origin = "imap.gmail.com"
        if args.command == "account-login":
            from urllib.parse import urlsplit
            parsed = urlsplit(args.origin)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise SystemExit("Provide an HTTPS origin without a path, query, or credentials")
            origin = "https://" + parsed.netloc.lower()
        email = input("Account email: ").strip()
        password = getpass.getpass("Password (hidden): ")
        if args.command == "gmail-login":
            password = password.replace(" ", "")
        if not email or not password:
            raise SystemExit("Email and app password required")
        Vault().set(origin, email, password)
        if args.command == "gmail-login":
            from .gmail import mailboxes_file, extra_mailboxes, imap_search
            imap_search(email, "newer_than:1d", 1)  # fails loudly on a wrong app password
            mailboxes_file().parent.mkdir(parents=True, exist_ok=True)
            mailboxes_file().write_text(json.dumps(sorted(set(extra_mailboxes()) | {email})))
            print(f"Connected {email}: codes, verification links and confirmations now search it too.")
        print("Stored in system credential store.")
    elif args.command == "sync":
        if args.file:
            content = args.file.read_text()
        else:
            with urlopen(SOURCE, timeout=30) as response:
                content = response.read().decode()
        jobs = list(parse_list(content))
        if not jobs:
            raise SystemExit("No application rows found; source format may have changed")
        report = {"parsed": len(jobs), "added": queue.ingest(jobs)}
        prefs = targeting_prefs(args.profile)
        if prefs:
            from .targeting import apply_targeting
            report["targeting"] = apply_targeting(queue, prefs)
        print(json.dumps(report, indent=2))
    elif args.command == "import-tracker":
        from .tracker import read_tracker, import_tracker
        with args.file.open(newline="") as handle:
            report = import_tracker(queue, read_tracker(handle))
        prefs = targeting_prefs(args.profile)
        if prefs:
            from .targeting import apply_targeting
            report["targeting"] = apply_targeting(queue, prefs)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.command == "targets":
        from .targeting import apply_targeting, ranked
        prefs = targeting_prefs(args.profile)
        if not prefs:
            raise SystemExit("Add a \"targeting\" block to " + str(args.profile))
        report = apply_targeting(queue, prefs)
        report["next"] = [{key: job[key] for key in ("id", "company", "role", "platform", "age_days", "saved")}
                          for job in ranked(queue)[:args.top]]
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.command == "browser":
        from .batch import serve_browser
        serve_browser(args.state)
    elif args.command == "run-one":
        from .batch import run_one
        report = run_one(args.id, args.state, args.profile, args.profile.parent / "accounts.json", submit=args.submit)
        print(json.dumps({"status": report.get("status"), "missing": report.get("missing", [])}, indent=2))
    elif args.command == "batch":
        if args.assist and args.submit:
            raise SystemExit("Use --assist on a fill-only batch, then review answers before approving a separate submission.")
        from .batch import pick, question_deck, run_batch
        prefs = targeting_prefs(args.profile) or {}
        rows = {r["id"]: r for r in queue.rows()}
        if args.ids is not None and not args.ids:
            raise SystemExit("--ids was given without any job ids; refusing to pick jobs on its own")
        jobs = [rows[i] for i in args.ids] if args.ids else pick(queue, args.count, prefs, args.company_cap, tuple(args.platforms.split(',')))
        if args.submit:
            from .batch import NO_RESUBMIT
            held = [j for j in jobs if j["status"] in NO_RESUBMIT]
            for job in held:
                print(f"Not submitting {job['company']} | {job['role']}: status {job['status']} (a person must check it)", flush=True)
            jobs = [j for j in jobs if j["status"] not in NO_RESUBMIT]
        print(f"Running {len(jobs)} jobs, {args.parallel} at a time:", ", ".join(j["company"] for j in jobs), flush=True)
        results = run_batch(jobs, args.state, args.parallel, submit=args.submit)
        summary = {"review": [], "other": [], "deck": []}
        for job, report in results:
            line = f"{job['company']} | {job['role']}"
            done = report.get("status") in ("ready_for_review", "submitted")
            (summary["review"] if done else summary["other"]).append(
                f"{line}: submitted" if report.get("status") == "submitted" else line if done else f"{line}: {report.get('status')}")
        from .questions import question_label
        options = {}
        for _, report in results:
            options.update(report.get("options", {}))
        summary["deck"] = [{"question": q, "companies": cs,
                            "options": options.get(question_label(q), [])}
                           for q, cs in question_deck([(j["company"], r) for j, r in results])]
        (args.state / "last-batch.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        if args.assist:
            from .agent_answers import suggest
            from .answer_deck import AnswerDeck
            from .agent_client import AgentError
            try:
                result = suggest(AnswerDeck(args.state, args.profile), [entry["question"] for entry in summary["deck"]])
                print(f"Prepared {len(result['proposals'])} answer proposals for review in Answer Deck.")
            except AgentError as exc:
                print("Agent assistance stopped: " + str(exc))
    elif args.command == "status":
        print(json.dumps(queue.rows(), indent=2))
    elif args.command == "profile-audit":
        from .profile import audit_profile
        if not args.profile.exists():
            raise SystemExit("Profile not found: " + str(args.profile))
        report = audit_profile(json.loads(args.profile.read_text()))
        print(json.dumps(report, indent=2))
        if report["status"] != "verified":
            raise SystemExit(2)
    elif args.command == "draft":
        from .models import LocalWriter
        print(json.dumps(LocalWriter().draft(args.question, json.loads(args.profile.read_text())), indent=2))
    else:
        from .browser import inspect
        job = next((j for j in queue.rows() if j["id"] == args.id), None)
        if not job:
            raise SystemExit("Unknown job ID")
        profile = json.loads(args.profile.read_text()) if args.profile.exists() else {}
        try:
            if args.command == "prepare":
                from .runner import run
                decider = None
                if args.decision_engine == "laya":
                    from .decisions import LayaDecider
                    decider = LayaDecider()
                from .account_store import AccountStore
                account_path = args.profile.parent / "accounts.json"
                store = AccountStore(account_path, args.state) if account_path.exists() else None
                try:
                    report = run(job["url"], profile, args.state / "browser", args.max_steps, decider, store, submit=args.submit)
                finally:
                    if store:
                        store.db.close()
            else:
                report = inspect(job["url"], profile, args.state / "browser", args.fill)
            queue.mark(args.id, report["status"], report.get("note", ""))
            (args.state / (args.id + ".json")).write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
        except Exception as exc:
            queue.mark(args.id, "needs_review", type(exc).__name__)
            raise SystemExit("Inspection failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
