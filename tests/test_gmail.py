import json
from types import SimpleNamespace

from job_agent import gmail
from job_agent.queue import Queue
from tests.test_ops import job, setup


def stream(tool, result, tool_id="t1"):
    """A claude stream-json transcript in which `tool` returned `result`."""
    lines = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tool_id, "name": tool, "input": {}}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                        "content": [{"type": "text", "text": result}]}]}}]
    return "\n".join(json.dumps(line) for line in lines)


def mail(i, date, subject, snippet, sender="no-reply@us.greenhouse-mail.io"):
    return {"id": f"m{i}", "thread": f"t{i}", "date": date, "sender": sender, "subject": subject, "snippet": snippet, "to": ""}


def test_search_reads_the_tools_own_result_including_saved_large_output(tmp_path, monkeypatch):
    monkeypatch.setattr(gmail, "uses_connector", lambda: True)
    threads = {"threads": [{"messages": [{"id": "a", "threadId": "a", "date": "2020-01-02T06:00:00Z", "subject": "S",
                                          "sender": "fixture@example.test", "snippet": "code: TEST1234", "toRecipients": ["me"]}]}]}
    saved = tmp_path / "out.txt"
    saved.write_text(json.dumps(threads))
    big = f"<persisted-output>\nOutput too large (56KB). Full output saved to: {saved}\n\nPreview: {{\"threads\""
    runner = lambda *a, **k: SimpleNamespace(stdout=stream(gmail.SEARCH_TOOL, big))
    [message] = gmail.search("q", runner=runner, mailboxes=[])
    assert message["snippet"] == "code: TEST1234" and message["thread"] == "a"
    assert message["date"][:10] in ("2020-01-02", "2020-01-01")  # converted to local time


def test_search_retries_when_the_connector_is_unavailable(monkeypatch):
    monkeypatch.setattr(gmail, "uses_connector", lambda: True)
    monkeypatch.setattr(gmail.time, "sleep", lambda s: None)
    answers = iter(["The service is currently unavailable.", json.dumps({"threads": []})])
    runner = lambda *a, **k: SimpleNamespace(stdout=stream(gmail.SEARCH_TOOL, next(answers)))
    assert gmail.search("q", runner=runner, mailboxes=[]) == []


def test_search_uses_configured_imap_when_connector_fails(monkeypatch):
    monkeypatch.setattr(gmail, "uses_connector", lambda: True)
    monkeypatch.setattr(gmail, "connector_search", lambda *args: (_ for _ in ()).throw(RuntimeError("tool did not run")))
    monkeypatch.setattr(gmail, "imap_search", lambda address, query, limit: [mail(1, "2026-09-28T10:00:00", "Code", "", sender=address)])
    import pytest
    with pytest.warns(RuntimeWarning, match="configured IMAP inboxes only"):
        messages = gmail.search("q", mailboxes=["personal@gmail.com"])
    assert len(messages) == 1 and messages[0]["sender"] == "personal@gmail.com"


def test_parse_stream_surfaces_claude_session_limit():
    stdout = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "You've hit your session limit · resets 8:20pm (America/New_York)"}
    ]}})
    try:
        gmail.parse_stream(stdout)
    except RuntimeError as error:
        assert "session limit" in str(error).lower()
        assert "8:20pm" in str(error)
    else:
        raise AssertionError("session-limit response should not be reported as a disconnected Gmail connector")


def test_parse_stream_rejects_non_object_tool_json():
    stdout = stream(gmail.SEARCH_TOOL, "[]")
    try:
        gmail.parse_stream(stdout)
    except RuntimeError as error:
        assert "object" in str(error).lower()
    else:
        raise AssertionError("a non-object Gmail tool result must not be treated as valid data")


def test_parse_stream_rejects_tool_result_without_json():
    stdout = stream(gmail.SEARCH_TOOL, "done")
    try:
        gmail.parse_stream(stdout)
    except RuntimeError as error:
        assert "json" in str(error).lower()
    else:
        raise AssertionError("an unparseable Gmail tool result must be retried instead of becoming an empty result")


def test_threads_text_refetches_threads_missing_from_a_partial_batch():
    calls = []

    def message(thread):
        return {"id": f"m-{thread}", "threadId": thread, "date": "2026-09-24T12:00:00Z",
                "sender": "assessment@example.com", "subject": thread, "snippet": "", "toRecipients": ["me"],
                "plaintextBody": f"body-{thread}"}

    def runner(*args, **kwargs):
        calls.append(args[0])
        returned = [message("t1")] if len(calls) == 1 else [message("t2")]
        return SimpleNamespace(stdout=stream(gmail.THREAD_TOOL, json.dumps({"messages": returned})))

    messages = gmail.threads_text(["t1", "t2"], runner=runner)
    assert [m["thread"] for m in messages] == ["t1", "t2"]
    assert len(calls) == 2


def test_codes_go_to_the_matching_waiting_job_newest_first_and_never_twice(tmp_path):
    state, _ = setup(tmp_path, [job(1, "XQT Corporation", platform="greenhouse"), job(2, "Verkana", platform="greenhouse")])
    queue = Queue(state / "jobs.sqlite3")
    for i in (1, 2):
        queue.mark(f"{i:020d}", "needs_input", json.dumps({"status": "email_code_required"}))
    since = queue.db.execute("SELECT MAX(at) FROM status_log").fetchone()[0]
    fresh = since[:11] + "23:59:00"
    messages = [mail(1, fresh, "Security code for your application to XQT Corporation",
                     "Copy and paste this code into the security code field on your application: NEWcode1 After"),
                mail(2, "2020-01-01T00:00:00", "Security code for your application to XQT Corporation",
                     "Copy and paste this code into the security code field on your application: OLDcode1 After"),
                mail(3, fresh, "Security code for your application to Other Co",
                     "Copy and paste this code into the security code field on your application: OTHERco1 After")]
    tried = []

    def enter(state, job_id, code):
        tried.append((job_id, code))
        return {"status": "submitted"}
    result = gmail.fill_codes(state, messages, enter)
    assert tried == [(f"{1:020d}", "NEWcode1")]  # old codes and other companies' codes are never typed
    assert {r["company"]: r["status"] for r in result["results"]} == {"XQT Corporation": "submitted", "Verkana": "no code yet"}
    tried.clear()
    gmail.fill_codes(state, messages, enter)
    assert tried == []  # a spent code is not reused


def test_confirmations_match_company_then_role_and_skip_generic_names(tmp_path):
    rows = [job(1, "Verkana", "Mobile Software Engineer Intern", "greenhouse"),
            job(2, "Verkana", "Backend Software Engineer Intern", "greenhouse"),
            job(3, "Keystone Capital Partners", "Data & Technology Summer Associate"),
            job(4, "Unison AI", "Software Engineer Intern", "greenhouse"),
            job(5, "Unison AI", "Systems Research Engineer Intern", "greenhouse")]
    state, _ = setup(tmp_path, rows)
    queue = Queue(state / "jobs.sqlite3")
    for row in rows:
        queue.mark(row["id"], "submitted", "Application submitted")
    later = "2099-01-01T00:00:0"
    messages = [mail(1, later + "1", "Thank you for applying to Verkana",
                     "Hi Alex, Thank you so much for applying to the Backend Software Engineering Intern 2027 role at Verkana!"),
                mail(2, later + "2", "Next Steps with Akuna Capital: HackerRank Coding Challenge", "Thank you for your application"),
                mail(3, later + "3", "Thank you for applying to Unison AI", "Thanks for applying to Unison AI."),
                mail(4, later + "4", "Thank you for applying to Unison AI", "Thanks for applying to Unison AI.")]
    recorded = {}
    gmail.confirmations(state, messages=messages, record=lambda s, i, e, a: recorded.update({i: e}))
    assert recorded[f"{2:020d}"] == "Thank you for applying to Verkana"      # the role named in the email
    assert f"{1:020d}" not in recorded and f"{3:020d}" not in recorded       # "Capital" alone is not Keystone Capital Partners
    assert recorded[f"{4:020d}"].endswith("[matched by order]")               # identical emails: labelled
    assert recorded[f"{5:020d}"].endswith("[matched by order]")


def test_verify_accounts_opens_the_full_link_for_the_jobs_tenant(tmp_path):
    row = job(1, "Corvia")
    row["url"] = "https://corvia.wd5.myworkdayjobs.com/Careers/job/Atlanta/Intern_R1"
    state, _ = setup(tmp_path, [row, job(2, "Sonara")])
    queue = Queue(state / "jobs.sqlite3")
    for i in (1, 2):
        queue.mark(f"{i:020d}", "needs_input", json.dumps({"status": "email_verification_required"}))
    messages = [mail(1, "2099-01-01T00:00:00", "Verify your candidate account",
                     "Click this link to confirm your email https://corvia.wd5.myworkdayjobs.com/Careers/activate/", "corvia@otp.workday.com")]
    body = "Click this link https://corvia.wd5.myworkdayjobs.com/Careers/activate/abc123?redirect=x to confirm"
    opened = []
    result = gmail.verify_accounts(state, messages, fetch=lambda t: [{"body": body}],
                                   opener=lambda url: opened.append(url) or {"text": "Invalid Token"})
    assert opened == ["https://corvia.wd5.myworkdayjobs.com/Careers/activate/abc123?redirect=x"]
    assert [v["company"] for v in result["verified"]] == ["Corvia"]
    assert result["no_email"] == [f"{2:020d} Sonara"]


def test_skills_come_from_the_person_and_the_posting_orders_them():
    from job_agent.skills import ranked
    mine = ["Python", "Java", "JavaScript", "C", "Linux", "Verilog", "FPGA", "Rust", "Figma"]
    order = [c["search"] for c in ranked(mine, "Firmware intern: C, Linux and FPGA/Verilog; JavaScript not needed")["add_all"]]
    assert order[:5] == ["JavaScript", "C", "Linux", "Verilog", "FPGA"]
    assert order.index("Java") > order.index("FPGA")  # "JavaScript" is not "Java"
    assert sorted(order) == sorted(mine)  # only this person's skills, never anyone else's
    assert ranked([], "Python role")["add_all"] == []
    zig = ranked(["Zig"], "")["add_all"][0]  # a skill with no keyword entry still works by its own name
    import re
    assert zig["search"] == "Zig" and re.search(zig["matches"], "zig (programming language)") and not re.search(zig["matches"], "zigbee")


def test_the_shared_skill_map_is_generic():
    from job_agent.skills import KEYWORDS
    assert len(KEYWORDS) >= 60


def test_imap_only_users_never_call_the_claude_connector(tmp_path, monkeypatch):
    from job_agent import ai
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    ai.save_setting("gmail", "imap")
    monkeypatch.setattr(gmail, "imap_search", lambda address, query, limit: [
        {"id": "a:1", "thread": None, "date": "2026-09-25T10:00:00", "sender": "x@y.z", "subject": "Code", "snippet": "123456", "to": address}])
    called = []
    found = gmail.search("newer_than:1d", runner=lambda *a, **k: called.append(a), mailboxes=["alex@gmail.com"])
    assert called == [] and [m["id"] for m in found] == ["a:1"]


def test_without_claude_installed_the_connector_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    monkeypatch.setattr(gmail.shutil, "which", lambda name: None)
    called = []
    assert gmail.search("x", runner=lambda *a, **k: called.append(a), mailboxes=[]) == [] and called == []


def test_a_confirmation_for_a_hand_submit_job_marks_it_submitted(tmp_path):
    rows = [job(1, "Verkana", "Backend Software Engineer Intern", "ashby")]
    state, _ = setup(tmp_path, rows)
    queue = Queue(state / "jobs.sqlite3")
    queue.mark(f"{1:020d}", "hand_submit", "Filled and checked; press Submit yourself")
    messages = [mail(1, "2099-01-01T00:00:01", "Thank you for applying to Verkana", "Thanks for applying to Verkana.")]
    recorded = {}
    gmail.confirmations(state, messages=messages, record=lambda s, i, e, a: recorded.update({i: e}))
    assert recorded == {f"{1:020d}": "Thank you for applying to Verkana"}
    status = queue.db.execute("SELECT status FROM jobs WHERE id=?", (f"{1:020d}",)).fetchone()[0]
    assert status == "submitted"


def test_email_text_drops_style_blocks_so_the_snippet_starts_with_the_message():
    html = ("<html><head><style>body, table { font-family: Verdana; } #outlook a { padding:0; }</style></head>"
            "<body><p>Dear Alex, Thank you for applying for Software Intern R-123</p></body></html>")
    assert gmail.email_text(html) == "Dear Alex, Thank you for applying for Software Intern R-123"
