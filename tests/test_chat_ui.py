from types import SimpleNamespace

from job_agent import assessments, chat_ui
from job_agent.queue import Queue


def test_dashboard_includes_assessment_cards(tmp_path, monkeypatch):
    Queue(tmp_path / "jobs.sqlite3").db.close()
    assessments.add(tmp_path, "Acme", "Coding Challenge", due="2026-10-01")
    monkeypatch.setattr(chat_ui.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    monkeypatch.setattr(chat_ui, "batch_running", lambda: False)

    state = chat_ui.dashboard(tmp_path)

    assert [(card["company"], card["title"]) for card in state["assessments"]] == [("Acme", "Coding Challenge")]


def test_mail_step_passes_selected_state_to_cli(tmp_path, monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(chat_ui.subprocess, "run", run)
    assert chat_ui.mail_step(tmp_path, "confirmations") == {}
    assert seen["command"][1:4] == ["--state", str(tmp_path), "confirmations"]


def test_mail_step_accepts_nested_cli_command(tmp_path, monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout='{"added": []}', stderr="")

    monkeypatch.setattr(chat_ui.subprocess, "run", run)
    assert chat_ui.mail_step(tmp_path, ["assessments", "scan", "--days", "30"]) == {"added": []}
    assert seen["command"][1:] == ["--state", str(tmp_path), "assessments", "scan", "--days", "30"]


def test_update_assessment_changes_only_allowed_fields(tmp_path):
    Queue(tmp_path / "jobs.sqlite3").db.close()
    key = assessments.add(tmp_path, "Acme", "Coding Challenge")["added"]

    result = chat_ui.update_assessment(tmp_path, {"id": key, "status": "done", "note": "Completed", "manual": 0})

    assert result == {"updated": 1}
    [card] = assessments.board(tmp_path)
    assert (card["status"], card["note"], card["manual"]) == ("done", "Completed", 1)


def test_scan_assessments_updates_console_status(tmp_path, monkeypatch):
    Queue(tmp_path / "jobs.sqlite3").db.close()
    chat_ui.assessment_scan.update(running=False, last=None, summary="")
    monkeypatch.setattr(chat_ui, "mail_step", lambda state, command: {
        "added": ["Acme: Coding Challenge"], "updated": ["Beta: OA"], "done": ["Gamma: Video"],
    })

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target
        def start(self):
            self.target()

    monkeypatch.setattr(chat_ui.threading, "Thread", ImmediateThread)

    assert chat_ui.scan_assessments(tmp_path, days=30) == {"started": True}
    assert chat_ui.assessment_scan["running"] is False
    assert chat_ui.assessment_scan["summary"] == "1 added; 1 updated; 1 completed"


def test_console_html_has_assessment_board_controls():
    page = chat_ui.PAGE.read_text()
    assert 'id="assessment-scan"' in page
    assert 'id="assessments"' in page
    assert "/api/assessments/update" in page
