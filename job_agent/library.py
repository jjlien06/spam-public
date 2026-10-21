"""The shared question library (library/*.json) and the user's own library rules.

A rule maps a question's wording to one core fact (job_agent/facts.py) plus option logic:
    {"id", "match": regex, "fact": key, "kind": "choice"|"text"|"any" (a text box or a list), "choose": [{"when": v, "prefer": [regex]}],
     "unless": [regex], "guard": bool}
`when` is True, False, "decline" or "*" (any other value, which `prefer` may use as {value}). Rules hold no
answers: the answer is always the user's confirmed fact. Private rules live in the profile's `library_rules`
(checked first); the user may mark them `shareable` and export them with `job-agent share-library`.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from . import facts
from .profile import normalize

ROOT = Path(__file__).resolve().parent.parent / "library"
RULE_KEYS = {"id", "match", "fact", "kind", "choose", "unless", "guard"}


@lru_cache(maxsize=4)
def _shared(folder, stamp):
    rules = []
    for path in sorted(Path(folder).glob("*.json")):
        rules += json.loads(path.read_text()).get("rules", [])
    return tuple(json.dumps(r) for r in rules)


def shared_rules(folder=ROOT):
    folder = Path(folder)
    stamp = tuple((p.name, p.stat().st_mtime_ns) for p in sorted(folder.glob("*.json"))) if folder.exists() else ()
    return [json.loads(r) for r in _shared(str(folder), stamp)]


def rules(profile_data):
    """Private rules first, then the shared library."""
    return [r for r in profile_data.get("library_rules") or [] if r.get("match") and r.get("fact")] + shared_rules()


def _matches(rule, key):
    try:
        return re.search(rule["match"], key, re.I) and not any(re.search(u, key, re.I) for u in rule.get("unless", []))
    except re.error:
        return False


def _render(pattern, value):
    return pattern.replace("{value}", re.escape(str(value).lower()))


def _choose(rule, value):
    """The option regexes for this fact value, or None when the rule has no branch for it."""
    for branch in rule.get("choose") or facts.default_choose(rule["fact"]):
        when = branch["when"]
        if (when == value and type(when) is type(value)) or (when == "*" and value not in (True, False, facts.DECLINE)):
            return [_render(p, value) for p in branch["prefer"]]
    return None


def resolve(profile_data, label, choice):
    """An answer from the library for this question, in the profile's answer format, or None."""
    known = facts.confirmed(profile_data)
    if not known:
        return None
    key = normalize(label)
    for rule in rules(profile_data):
        if rule["fact"] not in facts.CORE or not _matches(rule, key):
            continue
        if rule["fact"] not in known:
            return None  # the question is recognized but the user has not confirmed that fact: stop and ask
        value = known[rule["fact"]]
        kind = rule.get("kind", "choice")
        if value == facts.DECLINE and kind != "choice":
            return None
        if not choice:
            if kind == "choice":
                return None
            return ", ".join(value) if isinstance(value, list) else str(value)
        if kind == "text":
            return None
        prefer = _choose(rule, value)
        return [{"matches": p} for p in prefer] if prefer else None
    return None


def review_checks(profile_data):
    """Guardrails for the Review page from `guard` rules: the shown answer must agree with the fact."""
    known = facts.confirmed(profile_data)
    checks = []
    for rule in rules(profile_data):
        if rule.get("guard") and rule.get("kind", "choice") == "choice" and rule["fact"] in known:
            prefer = _choose(rule, known[rule["fact"]])
            if prefer:
                checks.append({"match": rule["match"], "answer": "|".join(f"(?:{p})" for p in prefer),
                               "unless": rule.get("unless", [])})
    return checks


def learn(profile_path, question, fact, kind=None, shareable=True, invert=False):
    """Map a new wording of a question to a core fact: a private rule, marked shareable (wording only).
    invert=True for a yes/no wording that asks the opposite of the fact ("can you work WITHOUT sponsorship?")."""
    from .profile_editor import write_profile
    if fact not in facts.CORE:
        raise ValueError(f"{fact!r} is not a core fact; see job_agent/facts.py")
    if invert and facts.CORE[fact]["type"] != "bool":
        raise ValueError(f"only yes/no facts can be inverted; {fact!r} is {facts.CORE[fact]['type']}")
    kind = kind or ("choice" if facts.CORE[fact]["type"] in ("bool", "enum") else "text")
    data = json.loads(Path(profile_path).read_text())
    match = "^" + re.escape(normalize(question)) + "$"
    kept = [r for r in data.get("library_rules") or [] if r.get("match") != match]
    rule = {"id": re.sub(r"[^a-z0-9]+", "-", normalize(question))[:48].strip("-"), "match": match, "fact": fact,
            "kind": kind, "shareable": bool(shareable)}
    if invert:
        rule["choose"] = [{**c, "when": not c["when"]} if isinstance(c["when"], bool) else c for c in facts.default_choose(fact)]
    data["library_rules"] = [rule, *kept]
    write_profile(profile_path, data)
    return {"saved": "library_rules", "fact": fact, "match": match, "shareable": rule["shareable"], "inverted": bool(invert)}


def _string_values(value):
    """Yield string leaves in JSON-shaped values, not field names or other scalars."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _string_values(child)


def _private_values(profile_data):
    """Known personal strings for best-effort export screening, not a PII guarantee."""
    values = []
    for section in ("facts", "core", "preferences", "documents", "mailbox"):
        values.extend(_string_values(profile_data.get(section)))
    for entry in (profile_data.get("answers") or {}).values():
        # Labels and aliases describe questions, not the user's answer.
        value = entry.get("value") if isinstance(entry, dict) else entry
        values.extend(_string_values(value))
    common = {"yes", "no", "n/a", "none", "true", "false", facts.DECLINE}
    return {v.strip().lower() for v in values if len(v.strip()) >= 3 and v.strip().lower() not in common}


def export(profile_data):
    """The user's shareable rules, reduced to wording + fact + option logic, and those refused for holding
    something personal. Nothing else from the profile is included."""
    private = _private_values(profile_data)
    shared, refused = [], []
    for rule in profile_data.get("library_rules") or []:
        if not rule.get("shareable"):
            continue
        clean = {k: v for k, v in rule.items() if k in RULE_KEYS}
        # Inspect every exported string, including identifiers and nested option logic.
        # Also undo regex escapes ("example\\ university" reads as "example university").
        strings = list(_string_values(clean))
        texts = [text.lower() for text in strings] + [re.sub(r"\\(.)", r"\1", text).lower() for text in strings]
        bad_when = any(c.get("when") not in (True, False, "*", facts.DECLINE) for c in clean.get("choose", []))
        if bad_when or any(value in text for value in private for text in texts):
            refused.append(clean)
        else:
            shared.append(clean)
    return {"version": 1, "rules": shared, "refused": refused}


def _valid(rule):
    if set(rule) - RULE_KEYS or rule.get("fact") not in facts.CORE or rule.get("kind", "choice") not in ("choice", "text", "any"):
        return False
    try:
        re.compile(rule["match"])
        [re.compile(u) for u in rule.get("unless", [])]
    except (re.error, KeyError, TypeError):
        return False
    return all(c.get("when") in (True, False, "*", facts.DECLINE) and isinstance(c.get("prefer"), list)
               for c in rule.get("choose", []))


def merge(incoming, target=ROOT / "community.json"):
    """Add a friend's exported rules to a library file: only well-formed rules with no answers, each wording once."""
    target = Path(target)
    current = json.loads(target.read_text()) if target.exists() else {
        "version": 1, "about": "Question wordings shared by SPAM users (merged with job-agent merge-library).", "rules": []}
    seen = {r["match"] for r in shared_rules(target.parent) + current["rules"]}
    added, rejected = [], []
    for rule in incoming.get("rules", []):
        if not _valid(rule):
            rejected.append(rule.get("id") or rule.get("match"))
        elif rule["match"] not in seen:
            current["rules"].append(rule)
            seen.add(rule["match"])
            added.append(rule.get("id") or rule["match"])
    if added:
        target.write_text(json.dumps(current, indent=2) + "\n")
    return {"added": added, "rejected": rejected}


def contribute(profile_data, repo, gh=None, author=None):
    """Open a pull request that adds the user's shareable wordings to library/community.json.

    Works in a temporary git worktree cut from origin/main, so the user's checkout (and me/) is never touched.
    Only what export() allows goes in; merge() drops anything malformed or already in the shared library."""
    import subprocess
    import tempfile
    import time as _time
    repo = Path(repo)

    def git(*args, cwd=repo):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def run_gh(args, cwd):
        return subprocess.run(["gh", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    exported = export(profile_data)
    if not exported["rules"]:
        return {"rules": 0, "refused": len(exported["refused"]), "pr": None}
    git("fetch", "-q", "origin", "main")
    branch = f"library/{_time.strftime('%Y%m%d-%H%M%S')}"
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder) / "work"
        git("worktree", "add", "-q", "-b", branch, str(work), "origin/main")
        try:
            report = merge(exported, work / "library" / "community.json")
            if not report["added"]:
                return {"rules": 0, "refused": len(exported["refused"]), "pr": None, "branch": None}
            git("add", "library/community.json", cwd=work)
            who = ["-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}"] if author else []
            git(*who, "commit", "-qm", f"Library: {len(report['added'])} question wording(s) learned from applications", cwd=work)
            git("push", "-q", "origin", branch, cwd=work)
            body = ("Question wordings mapped to core facts, learned while applying. "
                    "job-agent share-library performs best-effort screening for known personal values; "
                    "it does not guarantee that all personal information is excluded. "
                    "Human review of the export preview is required before sharing.\n\n"
                    + "\n".join(f"- `{r}`" for r in report["added"]))
            pr = (gh or run_gh)(["pr", "create", "--base", "main", "--head", branch, "--title",
                                 f"Library: {len(report['added'])} learned question wording(s)", "--body", body], work)
        finally:
            git("worktree", "remove", "--force", str(work))
    return {"rules": len(report["added"]), "refused": len(exported["refused"]), "pr": pr, "branch": branch}
