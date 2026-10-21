"""First-time setup for one person: me/ with a profile, confirmed core facts, targeting and a hiring-site login.

Everything here writes only what the user confirmed. The AI (Claude Code or Codex, via ai.py) may draft facts from
the resume; the draft is shown and saved only through confirm(). Used by `job-agent setup` (the Setup page) and by
the user's own AI agent through the `job-agent facts` commands (docs/agent/SETUP.md).
"""
import json
import os
import sys
from pathlib import Path

from . import facts, paths

CATEGORIES = ["Software Engineering", "Data Science, AI & Machine Learning", "Hardware Engineering",
              "Quantitative Finance", "Product Management", "Other"]
SKELETON = {"core": {}, "confirmed_facts": [], "facts": {}, "answers": {}, "patterns": [], "text_patterns": [],
            "review_checks": [], "library_rules": [], "documents": {},
            "targeting": {"categories": ["Software Engineering"], "us_only": True, "company_cap": 4,
                          "exclude_companies": []}}


def _write(path, data):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _load():
    return json.loads(paths.profile().read_text())


def start():
    """Create me/ (profile, state, extensions) if missing; never touch an existing profile."""
    home = paths.home()
    for folder in (home, paths.state(), home / "extensions"):
        folder.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    if not paths.profile().exists():
        _write(paths.profile(), SKELETON)
    return {"home": str(home), "profile": str(paths.profile())}


def checks():
    """What this Mac still needs: each {name, ok, fix}."""
    from . import ai
    python_ok = sys.version_info >= (3, 11)
    chromium = Path.home() / "Library" / "Caches" / "ms-playwright"
    return [
        {"name": "Python 3.11+", "ok": python_ok, "fix": "brew install python@3.11"},
        {"name": "Browser (Playwright Chromium)", "ok": chromium.exists() and any(chromium.glob("chromium-*")),
         "fix": ".venv/bin/playwright install chromium"},
        {"name": "AI (Claude Code or Codex)", "ok": bool(ai.providers()),
         "fix": "Install Claude Code (https://claude.com/claude-code) or Codex (npm i -g @openai/codex) and sign in"},
        {"name": "Your profile (me/profile.json)", "ok": paths.profile().exists(), "fix": "job-agent setup"},
    ]


RESUME_PROMPT = """Read the resume at {path}. Reply with ONE JSON object and nothing else, using only these keys,
and only for facts the resume states outright (leave a key out when the resume does not say it; never guess):
{keys}
Types: {types}. Dates as YYYY-MM (graduation) or YYYY-MM-DD. GPA as a number. Lists as arrays of strings.
The name at the top of the resume is legal_first_name + legal_last_name (the user confirms it); use preferred_name
only when the resume shows a different name in use (e.g. in quotes or parentheses)."""


def draft_from_resume(resume, ask=None):
    """Facts the AI read from the resume, filtered to known, well-typed keys. Nothing is saved but the resume's path."""
    from . import ai
    resume = Path(resume).expanduser().resolve()
    if not resume.exists():
        raise FileNotFoundError(resume)
    data = _load()
    data.setdefault("documents", {})["resume"] = str(resume)
    _write(paths.profile(), data)
    readable = [k for k, s in facts.CORE.items() if s["group"] in ("contact", "school", "links")]
    prompt = RESUME_PROMPT.format(path=resume, keys=", ".join(readable),
                                  types="; ".join(f"{k}: {facts.CORE[k]['type']}" for k in readable))
    reply = (ask or ai.run_json)(prompt, cwd=resume.parent)
    draft = {}
    for key, value in reply.items():
        if key not in readable:
            continue
        if facts.CORE[key]["type"] == "number" and isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                continue
        if key == "state" and isinstance(value, str):
            from .custom_controls import US_STATES
            value = US_STATES.get(value.strip().upper(), value)  # forms list states by full name
        if not facts.validate({key: value}):
            draft[key] = value
    return draft


def confirm(values):
    """Save facts the user confirmed (all or nothing when any is invalid) and refresh the derived `facts`."""
    problems = facts.validate(values)
    if problems:
        raise ValueError("; ".join(problems))
    data = _load()
    data.setdefault("core", {}).update(values)
    data["confirmed_facts"] = sorted(set(data.get("confirmed_facts") or []) | set(values))
    data["facts"] = {**data.get("facts", {}), **facts.to_facts(facts.confirmed(data))}
    _write(paths.profile(), data)
    return {"saved": sorted(values)}


def status():
    """Each fact group with its facts, values and whether confirmed; the keys still missing."""
    data = _load() if paths.profile().exists() else {}
    core, done = data.get("core") or {}, set(data.get("confirmed_facts") or [])
    groups = []
    for group, title in facts.GROUPS.items():
        groups.append({"group": group, "title": title, "facts": [
            {"key": key, "ask": spec["ask"], "type": spec["type"], "choices": spec["choices"],
             "declinable": spec["declinable"], "value": core.get(key), "confirmed": key in done}
            for key, spec in facts.CORE.items() if spec["group"] == group]})
    return {"groups": groups, "missing": [k for k in facts.CORE if k not in done],
            "targeting": data.get("targeting") or {}, "categories": CATEGORIES,
            "resume": (data.get("documents") or {}).get("resume"), "has_account": paths.accounts().exists()}


def save_targeting(targeting):
    unknown = [c for c in targeting.get("categories", []) if c not in CATEGORIES]
    if unknown:
        raise ValueError(f"unknown categories {unknown}; choose from {CATEGORIES}")
    cap = targeting.get("company_cap", 4)
    if not isinstance(cap, int) or cap < 1:
        raise ValueError("company_cap must be a whole number of at least 1")
    data = _load()
    data["targeting"] = {**data.get("targeting", {}), **{k: v for k, v in targeting.items()
                                                         if k in ("categories", "us_only", "company_cap", "exclude_companies")}}
    _write(paths.profile(), data)
    return {"saved": "targeting"}


def save_account(email, password):
    """The default hiring-site login (Workday and others): me/accounts.json, readable only by the user."""
    if "@" not in email or len(password) < 8:
        raise ValueError("an email address and a password of at least 8 characters are required")
    path = paths.accounts()
    current = json.loads(path.read_text()) if path.exists() else {}
    current["default"] = {"email": email, "password": password}
    current.setdefault("accounts", {})
    _write(path, current)
    return {"saved": "accounts"}
