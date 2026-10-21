import json
from pathlib import Path

import pytest

from job_agent import ai


class Recorder:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, command, cwd, timeout):
        self.calls.append(command)
        if "-o" in command:  # codex writes its last message to the -o file
            Path(command[command.index("-o") + 1]).write_text(self.reply)
            return ""
        return self.reply


def test_claude_runs_headless_read_only_with_the_prompt(tmp_path, monkeypatch):
    run = Recorder("hello")
    assert ai.run("Say hello", provider="claude", runner=run, cwd=tmp_path) == "hello"
    [command] = run.calls
    assert command[:3] == ["claude", "-p", "Say hello"] and "--allowedTools" in command
    assert "Edit" not in command and "Bash" not in command  # reading only


def test_codex_runs_exec_in_a_read_only_sandbox_and_returns_its_last_message(tmp_path):
    run = Recorder("hello from codex")
    assert ai.run("Say hello", provider="codex", runner=run, cwd=tmp_path) == "hello from codex"
    [command] = run.calls
    assert command[:2] == ["codex", "exec"] and command[command.index("-s") + 1] == "read-only"
    assert command[-1] == "Say hello"


def test_provider_comes_from_settings_then_whichever_cli_is_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path))
    monkeypatch.setattr(ai.shutil, "which", lambda name: "/bin/" + name if name == "codex" else None)
    assert ai.providers() == ["codex"] and ai.provider() == "codex"
    ai.save_provider("claude")
    assert json.loads((tmp_path / "settings.json").read_text())["ai"] == "claude" and ai.provider() == "claude"
    with pytest.raises(ValueError):
        ai.save_provider("gemini")


def test_json_replies_are_parsed_even_inside_a_code_fence():
    run = Recorder('Here you go:\n```json\n{"email": "alex@example.com"}\n```')
    assert ai.run_json("x", provider="claude", runner=run) == {"email": "alex@example.com"}
    with pytest.raises(ai.AIError):
        ai.run_json("x", provider="claude", runner=Recorder("no json here"))
