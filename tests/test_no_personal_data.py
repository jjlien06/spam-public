"""Shared files carry no personal data: every tracked file is scanned for the identifiers in me/denylist.txt."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Each person lists their own identifiers (name, email, phone digits, street, school...) in me/denylist.txt,
# one regex per line (git-ignored), so the list itself never enters the shared repo.
IDENTIFIERS = []


# The maintainer's design history (specs, plans) names them by design and is not exported to the shared repo.
NOT_SHARED = ("docs/superpowers/",)


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    return [ROOT / f for f in out if f and not f.startswith(NOT_SHARED) and (ROOT / f).is_file()]


def profile_terms(profile):
    """Regexes for the values in this person's own profile that must never appear in shared files: GPAs (next to
    the word GPA), job titles, phone digits, street, ZIP, email and name."""
    import json
    try:
        data = json.loads(profile.read_text())
    except (OSError, ValueError):
        return []
    facts, core = data.get("facts") or {}, data.get("core") or {}
    terms = []
    gpas = {str(e.get("gpa")) for e in facts.get("education") or [] if isinstance(e, dict) and e.get("gpa")}
    gpas |= {str(core["gpa"])} if core.get("gpa") else set()
    terms += [r"gpa\b[^\n]{0,60}" + re.escape(g) + r"(?!\d)" for g in gpas] + [re.escape(g) + r"[^\n]{0,40}\bgpa" for g in gpas]
    terms += [re.escape(e["title"]) for e in facts.get("experience") or [] if isinstance(e, dict) and len(e.get("title") or "") > 8
              and e["title"].lower() not in ("software engineering intern", "software engineer intern")]
    phone = re.sub(r"\D", "", str(facts.get("phone") or core.get("phone") or ""))
    if len(phone) >= 7:
        terms.append(r"\D?".join(phone[-7:]))
    address = facts.get("address") or {}
    for value in (address.get("street"), address.get("address1"), core.get("street"), facts.get("email"), core.get("email"),
                  facts.get("name")):
        if isinstance(value, str) and len(value) > 4:
            terms.append(re.escape(value))
    for value in (address.get("zip"), core.get("zip")):
        if isinstance(value, str) and re.fullmatch(r"\d{5}", value):
            terms.append(r"\b" + value + r"\b")
    return terms


def test_no_personal_identifiers_in_tracked_files():
    extra = ROOT / "me" / "denylist.txt"
    terms = IDENTIFIERS + ([t.strip() for t in extra.read_text().splitlines() if t.strip() and not t.startswith("#")]
                           if extra.exists() else []) + profile_terms(ROOT / "me" / "profile.json")
    if not terms:
        return  # nothing to look for until the user lists their identifiers
    pattern = re.compile("|".join(terms), re.I)
    hits = []
    for path in tracked():
        if path.name == "test_no_personal_data.py":
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
    assert not hits, f"{len(hits)} personal-data hits in tracked files:\n" + "\n".join(hits[:80])


def test_the_persons_own_gpa_and_job_titles_are_caught(tmp_path):
    import json
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"facts": {"education": [{"gpa": 3.9}], "phone": "(555) 010-2233",
                                             "experience": [{"title": "Robotics Lab Assistant"}]}}))
    pattern = re.compile("|".join(profile_terms(profile)), re.I)
    for leaked in ('"text_patterns": [{"match": "\\\\bgpa\\\\b", "value": "3.9"}]', '"GPA": "3.9"',
                   "<div>Robotics Lab Assistant</div>", "call 555-010-2233"):
        assert pattern.search(leaked), leaked
    for fine in ("GPA 3.5 or higher", "Software Engineering Intern", "3.95 kg"):
        assert not pattern.search(fine), fine
