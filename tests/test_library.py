import json

import pytest

from job_agent import library
from job_agent.custom_controls import option_match
from job_agent.profile import Profile
from job_agent.review_check import check_review

CORE = {"work_authorized": True, "needs_sponsorship": False, "us_citizen": True, "age_18_plus": True,
        "willing_to_relocate": True, "gender": "Female", "veteran": "decline", "hispanic_latino": False,
        "gpa": 3.5, "criminal_conviction": False}


def person(confirm=tuple(CORE), **extra):
    return {"core": dict(CORE), "confirmed_facts": list(confirm), **extra}


def pick(profile_data, question, options):
    value = Profile(profile_data).lookup(question, choice=True)
    if value is None:
        return None
    choices = [{"label": o, "value": o} for o in options]
    for preference in value if isinstance(value, list) else [value]:
        chosen = option_match(choices, preference)
        if chosen is not None:
            return chosen
    return "unmatched"


@pytest.mark.parametrize("question,options,expected", [
    ("Are you legally authorized to work in the United States?", ["Yes", "No"], "Yes"),
    ("Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?",
     ["Yes", "No"], "No"),
    ("Do you now or in the future require sponsorship for employment visa status to work in the United States?",
     ["No, I do not require sponsorship now or in the future", "No, I do not require sponsorship now, but may require it in the future",
      "Yes, I would require new sponsorship"], "No, I do not require sponsorship now or in the future"),
    ("Are you at least 18 years of age?", ["Yes", "No"], "Yes"),
    ("Are you willing to relocate?", ["Yes", "No"], "Yes"),
    ("What is your gender?", ["Male", "Female", "Decline to self-identify"], "Female"),
    ("Protected veteran status", ["I am a protected veteran", "I am not a protected veteran", "I don't wish to answer"],
     "I don't wish to answer"),
    ("Are you Hispanic or Latino?", ["Yes", "No", "Decline To Self Identify"], "No"),
    ("Have you ever been convicted of a felony?", ["Yes", "No"], "No"),
])
def test_library_answers_common_questions_from_confirmed_facts(question, options, expected):
    assert pick(person(), question, options) == expected


def test_an_unconfirmed_fact_never_answers():
    assert pick(person(confirm=["gender"]), "Are you legally authorized to work in the United States?", ["Yes", "No"]) is None


def test_a_private_answer_beats_the_library():
    data = person(answers={"Are you willing to relocate?": "No"})
    assert pick(data, "Are you willing to relocate?", ["Yes", "No"]) == "No"


def test_a_profile_without_core_facts_resolves_as_before():
    assert Profile({"answers": {}}).lookup("Are you legally authorized to work in the United States?", choice=True) is None


def test_free_text_takes_only_plain_facts_from_the_library():
    data = person()
    assert Profile(data).lookup("GPA", choice=False) == "3.5"
    assert Profile(data).lookup("Why do you want to work here?", choice=False) is None


def test_library_rules_guard_the_review_page():
    text = "Are you legally authorized to work in the United States?\nNo\n"
    assert check_review(text, person())
    assert check_review(text.replace("\nNo\n", "\nYes\n"), person()) == []


def test_shared_library_holds_no_answers():
    for rule in library.shared_rules():
        assert set(rule) <= library.RULE_KEYS, rule
        for choice in rule.get("choose", []):
            assert choice["when"] in (True, False, "*", "decline"), rule["id"]


def test_mapping_a_new_wording_to_a_fact_is_a_private_shareable_rule(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(person()))
    question = "Do you possess unrestricted permission to be employed stateside?"
    assert pick(json.loads(path.read_text()), question, ["Yes", "No"]) is None
    library.learn(path, question, "work_authorized")
    data = json.loads(path.read_text())
    [rule] = data["library_rules"]
    assert rule["shareable"] and rule["fact"] == "work_authorized"
    assert pick(data, question, ["Yes", "No"]) == "Yes"
    with pytest.raises(ValueError):
        library.learn(path, question, "favorite_color")


def test_share_export_has_rules_only_and_never_a_fact_value(tmp_path):
    data = person(answers={"Why us?": "Because Example Corp builds rockets."})
    data["core"]["school"] = "Example University"
    data["confirmed_facts"].append("school")
    data["library_rules"] = [
        {"id": "a", "match": "^permission to be employed stateside", "fact": "work_authorized", "kind": "choice", "shareable": True},
        {"id": "b", "match": "^which school", "fact": "school", "kind": "text", "shareable": False},
        {"id": "c", "match": "example university alumni", "fact": "school", "kind": "choice", "shareable": True},
        {"id": "d", "match": "^are you an? example\\ university student$", "fact": "enrolled", "kind": "choice", "shareable": True},
        {"id": "e", "match": "^can you say yes to working here$", "fact": "work_authorized", "kind": "choice", "shareable": True},
    ]
    exported = library.export(data)
    assert [r["id"] for r in exported["rules"]] == ["a", "e"]  # "yes" is not personal
    assert [r["id"] for r in exported["refused"]] == ["c", "d"]  # their regexes contain a fact value, even escaped
    text = json.dumps(exported["rules"])
    assert "Example" not in text and "rockets" not in text and "shareable" not in text
    assert all(set(r) <= library.RULE_KEYS for r in exported["rules"])


@pytest.mark.parametrize("section,payload,personal", [
    ("facts", {"contact": {"emails": ["nested.person@example.invalid"]}}, "nested.person@example.invalid"),
    ("facts", {"address": {"street": "123 Example Lane"}}, "123 Example Lane"),
    ("facts", {"identity": [{"legal_name": "Alex Example"}]}, "Alex Example"),
    ("facts", {"contact": {"phone": "+1 (555) 010-1234"}}, "+1 (555) 010-1234"),
    ("core", {"contact": [{"email": "core.person@example.invalid"}]}, "core.person@example.invalid"),
    ("answers", {"Contact": {"value": {"emails": ["answer.person@example.invalid"]}}}, "answer.person@example.invalid"),
    ("preferences", {"contacts": [{"email": "preference.person@example.invalid"}]}, "preference.person@example.invalid"),
    ("documents", {"Resume": {"path": "/private/example-person/resume.pdf"}}, "/private/example-person/resume.pdf"),
    ("mailbox", {"accounts": [{"email": "mailbox.person@example.invalid"}]}, "mailbox.person@example.invalid"),
])
def test_share_export_refuses_nested_personal_values_but_preserves_generic_rules(section, payload, personal, tmp_path):
    data = person(**{section: payload})
    data["facts"] = {**data.get("facts", {}), "flags": [True, False, "Yes", "No", "US", "", "n/a", "none", "true", "false", "decline"]}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    library.learn(path, f"Can {personal.upper()} work here?", "work_authorized")
    library.learn(path, "Can you work here? Yes or no (US); true/false, n/a, none, decline.", "work_authorized")

    exported = library.export(json.loads(path.read_text()))

    assert len(exported["rules"]) == 1
    assert exported["rules"][0]["id"].startswith("can-you-work-here")
    assert len(exported["refused"]) == 1
    assert personal.lower() not in json.dumps(exported["rules"]).lower()


@pytest.mark.parametrize("field,value", [
    ("id", "contact-known.person@example.invalid"),
    ("match", r"^known\.person@example\.invalid$"),
    ("fact", "known.person@example.invalid"),
    ("kind", "known.person@example.invalid"),
    ("guard", "known.person@example.invalid"),
    ("unless", [r"known\.person@example\.invalid"]),
    ("choose", [{"when": True, "prefer": [r"known\.person@example\.invalid"]}]),
    ("choose", [{"when": True, "prefer": ["^yes$"], "note": {"contact": ["known.person@example.invalid"]}}]),
])
def test_share_export_scans_every_rule_string_leaf_including_id(field, value):
    data = person()
    data["core"]["email"] = "known.person@example.invalid"
    rule = {"id": "safe", "match": "^are you authorized$", "fact": "work_authorized", "kind": "choice", "shareable": True}
    rule[field] = value
    data["library_rules"] = [rule]

    exported = library.export(data)

    assert exported["rules"] == []
    assert exported["refused"] == [{key: val for key, val in rule.items() if key in library.RULE_KEYS}]


def test_merging_a_friends_export_adds_only_valid_new_rules(tmp_path):
    target = tmp_path / "community.json"
    incoming = {"version": 1, "rules": [
        {"id": "a", "match": "^permission to be employed stateside$", "fact": "work_authorized", "kind": "choice"},
        {"id": "b", "match": "^(unclosed", "fact": "work_authorized", "kind": "choice"},
        {"id": "c", "match": "^favorite color$", "fact": "favorite_color", "kind": "choice"},
        {"id": "d", "match": "^x$", "fact": "gender", "kind": "choice", "choose": [{"when": "Female", "prefer": ["^f"]}]},
        {"id": "e", "match": "^y$", "fact": "gender", "kind": "choice", "value": "Female"},
    ]}
    report = library.merge(incoming, target)
    assert report["added"] == ["a"] and sorted(report["rejected"]) == ["b", "c", "d", "e"]
    assert library.merge(incoming, target)["added"] == []  # the same wording twice is one rule
    assert [r["id"] for r in json.loads(target.read_text())["rules"]] == ["a"]


@pytest.mark.parametrize("question,choice,expected", [
    ("First Name", False, "Alex"), ("Legal last name", False, "Doe"), ("Email Address", False, "alex@example.com"),
    ("Phone", False, "555-0100"), ("City", False, "Springfield"), ("Zip/Postal Code", False, "62701"),
    ("School", False, "Example University"),
])
def test_identity_fields_come_from_confirmed_facts(question, choice, expected):
    data = {"core": {"legal_first_name": "Alex", "legal_last_name": "Doe", "email": "alex@example.com", "phone": "555-0100",
                     "city": "Springfield", "zip": "62701", "school": "Example University", "state": "Illinois"},
            "confirmed_facts": ["legal_first_name", "legal_last_name", "email", "phone", "city", "zip", "school", "state"]}
    assert Profile(data).lookup(question, choice=choice) == expected


def test_a_state_field_is_typed_or_picked_from_its_list():
    data = {"core": {"state": "Illinois"}, "confirmed_facts": ["state"]}
    assert Profile(data).lookup("State", choice=False) == "Illinois"
    assert pick(data, "State", ["Idaho", "Illinois", "Indiana"]) == "Illinois"


def test_a_wording_that_asks_the_opposite_can_be_learned_inverted(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(person()))
    question = "Can you work here without a visa sponsor?"
    library.learn(path, question, "needs_sponsorship", invert=True)
    data = json.loads(path.read_text())
    assert pick(data, question, ["Yes", "No"]) == "Yes"  # needs_sponsorship is False -> can work without one
    assert library.export(data)["rules"][0]["choose"]  # the inverted logic travels with the shared rule
    with pytest.raises(ValueError):
        library.learn(path, "What is your gender?", "gender", invert=True)  # only yes/no facts invert


def test_a_question_that_only_mentions_linkedin_is_not_the_linkedin_url():
    data = {"core": {"linkedin": "https://linkedin.com/in/alex"}, "confirmed_facts": ["linkedin"]}
    assert Profile(data).lookup("From which channel did you know about our job post (our website, LinkedIn, X, etc.)?") is None
    assert Profile(data).lookup("LinkedIn Profile URL") == "https://linkedin.com/in/alex"
    assert Profile(data).lookup("Add your LinkedIn Profile url. If you don't post your url here, we will not be able to review your application.") \
        == "https://linkedin.com/in/alex"


def test_optional_in_a_work_authorization_question_is_not_opt():
    assert pick(person(), "Are you legally authorized to work in the United States? (optional)", ["Yes", "No"]) == "Yes"
    assert pick(person(), "Are you authorized to work in the U.S. on OPT?", ["Yes", "No"]) is None


def test_contribute_opens_a_pull_request_with_only_the_shareable_wordings(tmp_path):
    import subprocess
    git = lambda *a, cwd: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
    remote, clone = tmp_path / "remote.git", tmp_path / "clone"
    git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    git("clone", "-q", str(remote), str(clone), cwd=tmp_path)
    (clone / "library").mkdir()
    (clone / "library" / "core.json").write_text(json.dumps({"version": 1, "rules": []}))
    git("add", "-A", cwd=clone)
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init", cwd=clone)
    git("push", "-q", "origin", "main", cwd=clone)
    data = person(answers={"Why us?": "secret essay"})
    data["library_rules"] = [{"id": "a", "match": "^permission to be employed stateside$", "fact": "work_authorized",
                              "kind": "choice", "shareable": True}]
    opened = []
    result = library.contribute(data, clone, gh=lambda args, cwd: opened.append(args) or "https://github.com/x/pull/1",
                                author=("Friend", "friend@example.com"))
    assert result["pr"] == "https://github.com/x/pull/1" and result["rules"] == 1
    branch = result["branch"]
    shown = git("show", f"{branch}:library/community.json", cwd=remote)
    assert "stateside" in shown and "secret essay" not in shown and "Example" not in shown
    assert opened and opened[0][:2] == ["pr", "create"]
    body = opened[0][opened[0].index("--body") + 1]
    assert "best-effort" in body.lower()
    assert "human review" in body.lower() and "preview" in body.lower()
    assert "no answers, essays, companies or dates" not in body.lower()
    assert git("status", "--porcelain", cwd=clone) == ""  # the friend's checkout is untouched
