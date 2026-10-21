from job_agent import facts


def test_every_core_fact_has_a_type_group_and_question():
    assert len(facts.CORE) >= 35
    for key, spec in facts.CORE.items():
        assert spec["type"] in facts.TYPES, key
        assert spec["group"] in facts.GROUPS, key
        assert spec["ask"].strip(), key


def test_validate_reports_wrong_types_unknown_keys_and_bad_choices():
    problems = facts.validate({"work_authorized": "yes", "favorite_color": "blue", "gender": "Robot", "gpa": 3.8,
                               "graduation_date": "May 2029"})
    assert any("work_authorized" in p for p in problems)
    assert any("favorite_color" in p for p in problems)
    assert any("gender" in p for p in problems)
    assert any("graduation_date" in p for p in problems)
    assert not any("gpa" in p for p in problems)
    assert facts.validate({"gender": "decline", "veteran": "decline", "graduation_date": "2029-05"}) == []


def test_only_confirmed_facts_are_used():
    profile = {"core": {"work_authorized": True, "needs_sponsorship": False}, "confirmed_facts": ["work_authorized"]}
    assert facts.confirmed(profile) == {"work_authorized": True}
    assert facts.confirmed({}) == {}


def test_core_facts_fill_the_profile_facts_the_fill_code_reads():
    core = {"legal_first_name": "Alex", "legal_last_name": "Doe", "email": "alex@example.com", "phone": "555-0100",
            "school": "Example University", "degree": "Bachelor of Science", "major": "Computer Science",
            "gpa": 3.5, "graduation_date": "2029-05", "school_domain": "example.edu", "city": "Springfield",
            "state": "Illinois", "zip": "62701", "country": "United States", "willing_to_relocate": True}
    shaped = facts.to_facts(core)
    assert shaped["name"] == "Alex Doe" and shaped["email"] == "alex@example.com"
    assert shaped["education"][0]["school"] == "Example University"
    assert shaped["education"][0]["graduation_date"] == "2029-05" and shaped["education"][0]["school_domain"] == "example.edu"
    assert shaped["address"]["city"] == "Springfield" and shaped["willing_to_relocate"] is True


def test_default_option_logic_follows_the_fact_type():
    yes_no = facts.default_choose("work_authorized")
    assert {"when": True, "prefer": ["^yes\\b"]} in yes_no and {"when": False, "prefer": ["^no\\b"]} in yes_no
    gender = facts.default_choose("gender")
    assert gender[0]["when"] == "decline" and gender[-1] == {"when": "*", "prefer": ["^{value}$", "^{value}\\b"]}
