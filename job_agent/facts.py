"""Core facts: the fixed questions every applicant answers once (in `me/profile.json` under `core`).

A shared library rule (library/*.json) maps a question's wording to one of these keys; the answer comes from the
user's own value. Setup asks for them (drafting from the resume where it can) and records which ones the user has
confirmed in `confirmed_facts`: an unconfirmed fact is never used. Declinable facts accept "decline".
"""
import re

TYPES = {"bool", "str", "number", "date", "list", "enum"}
GROUPS = {"contact": "Name and contact", "school": "School", "work": "Work authorization",
          "preferences": "Preferences", "history": "History and conflicts", "demographics": "Voluntary self-identification",
          "links": "Links and extras"}
DECLINE = "decline"


def _fact(type_, group, ask, choices=None, declinable=False):
    return {"type": type_, "group": group, "ask": ask, "choices": choices or [], "declinable": declinable}


CORE = {
    # contact
    "legal_first_name": _fact("str", "contact", "Legal first name"),
    "legal_last_name": _fact("str", "contact", "Legal last name"),
    "preferred_name": _fact("str", "contact", "Preferred first name (blank if the same)"),
    "email": _fact("str", "contact", "Email for applications"),
    "phone": _fact("str", "contact", "Phone number"),
    "street": _fact("str", "contact", "Street address"),
    "city": _fact("str", "contact", "City"),
    "state": _fact("str", "contact", "State (full name)"),
    "zip": _fact("str", "contact", "ZIP code"),
    "country": _fact("str", "contact", "Country"),
    # school
    "school": _fact("str", "school", "School (full official name)"),
    "school_domain": _fact("str", "school", "School email domain (e.g. example.edu)"),
    "degree": _fact("str", "school", "Degree (e.g. Bachelor of Science)"),
    "major": _fact("str", "school", "Major"),
    "gpa": _fact("number", "school", "Cumulative GPA on a 4.0 scale"),
    "graduation_date": _fact("date", "school", "Expected graduation (YYYY-MM)"),
    "enrolled": _fact("bool", "school", "Are you currently enrolled as a student?"),
    "start_date": _fact("date", "school", "Earliest internship start date (YYYY-MM-DD)"),
    "age_18_plus": _fact("bool", "school", "Are you 18 or older?"),
    # work authorization
    "us_citizen": _fact("bool", "work", "Are you a U.S. citizen?"),
    "work_authorized": _fact("bool", "work", "Are you authorized to work in the U.S. for any employer?"),
    "needs_sponsorship": _fact("bool", "work", "Will you now or in the future need visa sponsorship?"),
    "clearance_eligible": _fact("bool", "work", "Could you obtain a U.S. security clearance?"),
    "has_clearance": _fact("bool", "work", "Do you hold an active security clearance?"),
    # preferences
    "willing_to_relocate": _fact("bool", "preferences", "Willing to relocate for an internship?"),
    "willing_onsite": _fact("bool", "preferences", "Willing to work on-site full time?"),
    "travel_percent": _fact("number", "preferences", "Most travel you'd accept (percent)"),
    "pay_expectation": _fact("str", "preferences", "Pay expectation (e.g. 'flexible' or '$25/hr')"),
    "referral_source": _fact("str", "preferences", "How you usually find postings (e.g. LinkedIn)"),
    # history and conflicts
    "worked_for_government": _fact("bool", "history", "Have you been employed by the U.S. government or military?"),
    "military_service": _fact("bool", "history", "Have you served in the military?"),
    "non_compete": _fact("bool", "history", "Are you bound by a non-compete or similar agreement?"),
    "conflicts_of_interest": _fact("bool", "history", "Any family ties or business conflicts with employers?"),
    "criminal_conviction": _fact("bool", "history", "Have you been convicted of a crime? (answered only by you)"),
    # demographics (all declinable)
    "gender": _fact("enum", "demographics", "Gender", ["Male", "Female", "Non-binary"], True),
    "pronouns": _fact("str", "demographics", "Pronouns", declinable=True),
    "hispanic_latino": _fact("bool", "demographics", "Are you Hispanic or Latino?", declinable=True),
    "race": _fact("enum", "demographics", "Race", ["Asian", "Black or African American", "White",
                                                   "American Indian or Alaska Native",
                                                   "Native Hawaiian or Other Pacific Islander", "Two or More Races"], True),
    "veteran": _fact("bool", "demographics", "Are you a protected veteran?", declinable=True),
    "disability": _fact("bool", "demographics", "Do you have a disability?", declinable=True),
    "lgbtq": _fact("bool", "demographics", "Do you identify as LGBTQ+?", declinable=True),
    # links and extras
    "linkedin": _fact("str", "links", "LinkedIn URL"),
    "github": _fact("str", "links", "GitHub URL"),
    "website": _fact("str", "links", "Personal website or portfolio URL"),
    "languages": _fact("list", "links", "Languages you speak"),
    "skills": _fact("list", "links", "Technical skills"),
}


def validate(core):
    """Problems with a `core` block: unknown keys, wrong types, values outside the choices."""
    problems = []
    for key, value in (core or {}).items():
        spec = CORE.get(key)
        if spec is None:
            problems.append(f"{key}: not a core fact")
            continue
        if value is None or (spec["declinable"] and value == DECLINE):
            continue
        kind = spec["type"]
        ok = {"bool": isinstance(value, bool), "str": isinstance(value, str),
              "number": isinstance(value, (int, float)) and not isinstance(value, bool),
              "date": isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value)),
              "list": isinstance(value, list) and all(isinstance(v, str) for v in value),
              "enum": value in spec["choices"]}[kind]
        if not ok:
            problems.append(f"{key}: expected {kind}" + (f" in {spec['choices']}" if kind == "enum" else "") + f", got {value!r}")
    return problems


def confirmed(profile_data):
    """The core facts the user has confirmed: the only ones SPAM may use."""
    core = profile_data.get("core") or {}
    return {key: core[key] for key in profile_data.get("confirmed_facts") or [] if key in core and core[key] is not None}


def default_choose(key):
    """Option logic for a question mapped to this fact: data, never an answer."""
    spec = CORE[key]
    decline = [{"when": DECLINE, "prefer": ["decline", "prefer not", "not wish", "don.t wish", "choose not", "not to (?:say|answer|disclose)"]}]
    if spec["type"] == "bool":
        return (decline if spec["declinable"] else []) + [{"when": True, "prefer": ["^yes\\b"]}, {"when": False, "prefer": ["^no\\b"]}]
    return (decline if spec["declinable"] else []) + [{"when": "*", "prefer": ["^{value}$", "^{value}\\b"]}]


def to_facts(core):
    """The `facts` shape the fill code already reads (name, education records, address...)."""
    name = " ".join(v for v in (core.get("legal_first_name"), core.get("legal_last_name")) if v)
    shaped = {key: core[key] for key in ("email", "phone", "willing_to_relocate", "languages", "skills") if key in core}
    if name:
        shaped["name"] = name
    school = {k: core[k] for k in ("school", "degree", "major", "gpa", "school_domain") if k in core}
    if "graduation_date" in core:
        school["graduation_date"] = school["end_date"] = core["graduation_date"]
    if school:
        shaped["education"] = [school]
    address = {k: core[k] for k in ("street", "city", "state", "zip", "country") if k in core}
    if address:
        shaped["address"] = address
    links = {k: core[k] for k in ("linkedin", "github", "website") if core.get(k)}
    if links:
        shaped["links"] = links
    return shaped
