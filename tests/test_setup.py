import json
import stat

import pytest

from job_agent import setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path / "me"))
    return tmp_path / "me"


def test_start_creates_me_once_and_never_overwrites(home):
    setup.start()
    profile = json.loads((home / "profile.json").read_text())
    assert profile["core"] == {} and profile["confirmed_facts"] == [] and profile["answers"] == {}
    assert (home / "state").is_dir() and (home / "extensions").is_dir()
    assert stat.S_IMODE((home / "profile.json").stat().st_mode) == 0o600
    (home / "profile.json").write_text(json.dumps({**profile, "answers": {"Why us?": "mine"}}))
    setup.start()
    assert json.loads((home / "profile.json").read_text())["answers"] == {"Why us?": "mine"}


def test_resume_draft_keeps_only_known_well_typed_facts_and_saves_nothing(home, tmp_path):
    setup.start()
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    reply = {"legal_first_name": "Alex", "gpa": "3.5", "graduation_date": "2029-05", "favorite_color": "blue",
             "work_authorized": "probably"}
    draft = setup.draft_from_resume(resume, ask=lambda prompt, **kw: reply)
    assert draft == {"legal_first_name": "Alex", "gpa": 3.5, "graduation_date": "2029-05"}
    assert json.loads((home / "profile.json").read_text())["core"] == {}
    assert json.loads((home / "profile.json").read_text())["documents"]["resume"] == str(resume)


def test_confirming_a_group_saves_only_those_facts_and_keeps_everything_else(home):
    setup.start()
    data = json.loads((home / "profile.json").read_text())
    data["answers"] = {"Why us?": "mine"}
    (home / "profile.json").write_text(json.dumps(data))
    result = setup.confirm({"legal_first_name": "Alex", "legal_last_name": "Doe", "email": "alex@example.com",
                            "work_authorized": True})
    assert result["saved"] == ["email", "legal_first_name", "legal_last_name", "work_authorized"]
    data = json.loads((home / "profile.json").read_text())
    assert data["answers"] == {"Why us?": "mine"} and data["facts"]["name"] == "Alex Doe"
    assert sorted(data["confirmed_facts"]) == result["saved"]
    with pytest.raises(ValueError):
        setup.confirm({"gender": "Robot"})
    assert "gender" not in json.loads((home / "profile.json").read_text())["core"]


def test_status_lists_each_group_with_what_is_still_unconfirmed(home):
    setup.start()
    setup.confirm({"work_authorized": True})
    status = setup.status()
    work = next(g for g in status["groups"] if g["group"] == "work")
    assert {f["key"]: f["confirmed"] for f in work["facts"]}["work_authorized"] is True
    assert "needs_sponsorship" in status["missing"] and "work_authorized" not in status["missing"]


def test_accounts_default_is_saved_private_and_keeps_site_overrides(home):
    setup.start()
    (home / "accounts.json").write_text(json.dumps({"accounts": {"https://x.example": {"email": "a@b.c", "password": "p"}}}))
    setup.save_account("alex@example.com", "s3cret-Pass!")
    saved = json.loads((home / "accounts.json").read_text())
    assert saved["default"] == {"email": "alex@example.com", "password": "s3cret-Pass!"}
    assert "https://x.example" in saved["accounts"]
    assert stat.S_IMODE((home / "accounts.json").stat().st_mode) == 0o600


def test_targeting_is_validated_and_saved(home):
    setup.start()
    setup.save_targeting({"categories": ["Software Engineering"], "us_only": True, "company_cap": 4,
                          "exclude_companies": ["Acme"]})
    targeting = json.loads((home / "profile.json").read_text())["targeting"]
    assert targeting["categories"] == ["Software Engineering"] and targeting["exclude_companies"] == ["Acme"]
    with pytest.raises(ValueError):
        setup.save_targeting({"categories": ["Basket Weaving"]})


def test_a_state_abbreviation_in_the_resume_is_drafted_as_its_full_name(home, tmp_path):
    setup.start()
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    assert setup.draft_from_resume(resume, ask=lambda prompt, **kw: {"state": "IL"}) == {"state": "Illinois"}
