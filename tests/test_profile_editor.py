import json
import os

import pytest

from job_agent.profile import Profile
from job_agent.profile_editor import save_answer


def _write(path, data):
    path.write_text(json.dumps(data))


def test_save_answer_preserves_unrelated_data_and_updates_existing_alias(tmp_path):
    path = tmp_path / "profile.json"
    original = {
        "facts": {"keep": "yes"},
        "answers": {"First name": {"value": "Old", "aliases": ["Given name"]}},
        "unresolved": {"keep": True},
    }
    _write(path, original)
    save_answer(path, "Given name", "New")
    data = json.loads(path.read_text())
    assert data["facts"] == original["facts"]
    assert data["unresolved"] == original["unresolved"]
    assert data["answers"]["First name"] == {"value": "New", "aliases": ["Given name"]}
    assert Profile(data).lookup("Given name") == "New"


def test_save_answer_accepts_false_and_writes_mode_0600(tmp_path):
    path = tmp_path / "profile.json"
    _write(path, {"answers": {"Consent": True}})
    path.chmod(0o644)
    save_answer(path, "Consent", False)
    assert json.loads(path.read_text())["answers"]["Consent"]["value"] is False
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_save_answer_rejects_conflicting_aliases_without_replacing_source(tmp_path):
    path = tmp_path / "profile.json"
    original = {"answers": {
        "First name": {"value": "Alex", "aliases": ["Given name"]},
        "Legal name": {"value": "Jordan", "aliases": ["Given name"]},
    }}
    _write(path, original)
    with pytest.raises(ValueError, match="Conflicting"):
        save_answer(path, "First name", "Taylor")
    assert json.loads(path.read_text()) == original
