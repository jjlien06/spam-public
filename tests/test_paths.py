import subprocess
import sys

from job_agent import paths


def test_me_folder_wins_and_legacy_root_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPAM_HOME", raising=False)
    (tmp_path / "profile.json").write_text("{}")  # a copy made before me/ existed
    assert paths.profile() == tmp_path / "profile.json" and paths.state() == tmp_path / "state"
    (tmp_path / "me").mkdir()
    (tmp_path / "me" / "profile.json").write_text("{}")
    assert paths.profile() == tmp_path / "me" / "profile.json" and paths.state() == tmp_path / "me" / "state"
    monkeypatch.setenv("SPAM_HOME", str(tmp_path / "other"))
    assert paths.home() == tmp_path / "other" and paths.accounts() == tmp_path / "other" / "accounts.json"


def test_fresh_clone_points_at_me(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPAM_HOME", raising=False)
    assert paths.profile() == tmp_path / "me" / "profile.json"
    assert paths.extensions() == tmp_path / "me" / "extensions"


def test_cli_without_a_profile_says_how_to_set_up(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "SPAM_HOME": str(tmp_path / "me"), "PYTHONPATH": str(paths.__file__).rsplit("/job_agent/", 1)[0]}
    out = subprocess.run([sys.executable, "-m", "job_agent.cli", "status"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert out.returncode == 2
    assert "job-agent setup" in out.stdout + out.stderr and "Traceback" not in out.stderr


def test_a_me_folder_without_a_profile_keeps_the_legacy_root(tmp_path, monkeypatch):
    # me/ may exist only for private extras (extensions, denylist) before the switch-over.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SPAM_HOME", raising=False)
    (tmp_path / "profile.json").write_text("{}")
    (tmp_path / "me" / "extensions").mkdir(parents=True)
    assert paths.profile() == tmp_path / "profile.json"
    assert paths.extensions() == tmp_path / "me" / "extensions"  # extensions always live in me/


def test_bug_captures_default_to_the_persons_state_folder(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from job_agent import capture
    monkeypatch.setenv("SPAM_HOME", str(tmp_path / "me"))
    page = SimpleNamespace(url="https://example.com/x", content=lambda: "<html></html>", evaluate=lambda *a: [],
                           screenshot=lambda **k: None)
    capture.capture(page, "test")
    assert list((tmp_path / "me" / "state" / "bugs").glob("*-example.html"))
