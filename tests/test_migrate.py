import json

import pytest

from job_agent import migrate

PROFILE = {"answers": {"Are you willing to relocate?": "Yes", "GPA": "3.5"},
           "patterns": [{"match": "authori[sz]ed to work", "value": "Yes"}],
           "facts": {"name": "Alex Doe", "email": "alex@example.com", "willing_to_relocate": True,
                     "education": [{"school": "Example University", "gpa": 3.5, "graduation_date": "2029-05"}]}}
REVIEW = """Are you willing to relocate?
Yes
Are you legally authorized to work in the United States?
Yes
GPA:
3.5
Will you require sponsorship?
No
"""


@pytest.fixture
def legacy(tmp_path):
    (tmp_path / "profile.json").write_text(json.dumps(PROFILE))
    (tmp_path / "accounts.json").write_text(json.dumps({"default": {"email": "alex@example.com", "password": "pw123456"}}))
    runs = tmp_path / "state" / "runs"
    runs.mkdir(parents=True)
    (runs / ("a" * 20 + ".review.txt")).write_text(REVIEW)
    return tmp_path


def test_check_reports_no_change_for_the_same_profile(legacy):
    report = migrate.check(legacy, json.loads((legacy / "profile.json").read_text()))
    assert report["changed"] == [] and report["questions"] >= 3


def test_check_reports_every_answer_that_would_change(legacy):
    new = json.loads((legacy / "profile.json").read_text())
    new["answers"]["Are you willing to relocate?"] = "No"
    new["core"] = {"needs_sponsorship": False}
    new["confirmed_facts"] = ["needs_sponsorship"]
    report = migrate.check(legacy, new)
    assert [c["question"] for c in report["changed"]] == ["Are you willing to relocate?"]
    assert [c["question"] for c in report["newly_answered"]] == ["Will you require sponsorship?"]


def test_core_facts_are_derived_only_where_the_old_profile_is_unambiguous():
    core = migrate.core_from_facts(PROFILE["facts"])
    assert core == {"legal_first_name": "Alex", "legal_last_name": "Doe", "email": "alex@example.com",
                    "school": "Example University", "gpa": 3.5, "graduation_date": "2029-05", "willing_to_relocate": True}


def test_migrate_copies_into_me_after_a_backup_and_never_overwrites(legacy):
    result = migrate.run(legacy)
    me = legacy / "me"
    assert json.loads((me / "profile.json").read_text())["answers"] == PROFILE["answers"]
    assert (me / "accounts.json").exists() and (me / "state" / "runs").is_dir()
    assert (legacy / "profile.json").exists()  # copied, never moved
    assert any(p.name.startswith("backup-") for p in legacy.iterdir()) and result["copied"]
    with pytest.raises(FileExistsError):
        migrate.run(legacy)


def test_migrate_refuses_when_the_check_finds_changes(legacy, monkeypatch):
    monkeypatch.setattr(migrate, "check", lambda root, new, sample=40: {"changed": [{"question": "x"}], "questions": 1,
                                                                      "newly_answered": []})
    with pytest.raises(RuntimeError):
        migrate.run(legacy)
    assert not (legacy / "me" / "profile.json").exists()


def test_migrate_from_an_old_checkout_into_a_new_one_brings_private_extensions(legacy, tmp_path):
    (legacy / "me" / "extensions").mkdir(parents=True)
    (legacy / "me" / "extensions" / "private_mode.py").write_text("MODE = 'x'\n")
    (legacy / "me" / "denylist.txt").write_text("secret\n")
    new = tmp_path / "new-checkout"
    new.mkdir()
    result = migrate.run(legacy, dest=new)
    assert json.loads((new / "me" / "profile.json").read_text())["answers"] == PROFILE["answers"]
    assert (new / "me" / "state" / "runs").is_dir() and (new / "me" / "extensions" / "private_mode.py").exists()
    assert (new / "me" / "denylist.txt").exists() and not (legacy / "me" / "profile.json").exists()
    assert "extensions/" in result["copied"]
