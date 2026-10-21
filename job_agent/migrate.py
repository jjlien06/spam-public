"""Moving a copy made before me/ existed (root profile.json, accounts.json, state/) into me/, safely.

`check` replays the questions on recent Review snapshots against the old profile and the new one and lists every
answer that would change; `run` copies (never moves) into me/ after a backup, and only when the check is clean.
"""
import json
import shutil
import time
from pathlib import Path

from .profile import Profile
from .review_check import looks_like_question


def _questions(root, sample):
    runs = sorted((Path(root) / "state" / "runs").glob("*.review.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    seen = []
    for path in runs[:sample]:
        for line in path.read_text(errors="ignore").splitlines():
            line = line.strip()
            if looks_like_question(line) and 3 < len(line) < 600 and line not in seen:
                seen.append(line)
    return seen


def _answer(profile, question):
    try:
        return profile.lookup(question, choice=True), profile.lookup(question, choice=False)
    except (ValueError, TypeError):
        return ("error", "error")


def check(root, new_profile, sample=40):
    """Every question from the last `sample` Review pages whose answer differs between the old and new profile."""
    old = Profile(json.loads((Path(root) / "profile.json").read_text()))
    new = Profile(new_profile)
    changed, newly = [], []
    questions = _questions(root, sample)
    for question in questions:
        before, after = _answer(old, question), _answer(new, question)
        if before == after:
            continue
        entry = {"question": question, "before": before, "after": after}
        (newly if before == (None, None) else changed).append(entry)
    return {"questions": len(questions), "changed": changed, "newly_answered": newly}


def core_from_facts(old):
    """Core facts that the old profile states without ambiguity (the rest are asked in setup)."""
    core = {}
    name = str(old.get("name") or "").split()
    if len(name) >= 2:
        core["legal_first_name"], core["legal_last_name"] = name[0], name[-1]
    for key in ("email", "phone"):
        if isinstance(old.get(key), str):
            core[key] = old[key]
    school = (old.get("education") or [{}])[0]
    for key, target in (("school", "school"), ("major", "major"), ("degree", "degree"), ("school_domain", "school_domain")):
        if isinstance(school.get(key), str):
            core[target] = school[key]
    if isinstance(school.get("gpa"), (int, float)):
        core["gpa"] = school["gpa"]
    if isinstance(school.get("graduation_date"), str) and len(school["graduation_date"]) >= 7:
        core["graduation_date"] = school["graduation_date"][:7]
    if isinstance(old.get("willing_to_relocate"), bool):
        core["willing_to_relocate"] = old["willing_to_relocate"]
    return core


def run(root, sample=40, dest=None):
    """Copy root's profile.json, accounts.json and state/ into dest/me (dest defaults to root) after backing them up,
    along with private extras in root/me (extensions/, denylist.txt). Refuses when dest/me/profile.json exists or
    when the check finds any changed answer."""
    root = Path(root)
    me = Path(dest or root) / "me"
    if (me / "profile.json").exists():
        raise FileExistsError(f"{me / 'profile.json'} already exists; nothing copied")
    profile = json.loads((root / "profile.json").read_text())
    report = check(root, profile, sample)
    if report["changed"]:
        raise RuntimeError(f"{len(report['changed'])} answer(s) would change; nothing copied: {report['changed'][:3]}")
    backup = root / f"backup-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.mkdir()
    copied = []
    for name in ("profile.json", "accounts.json"):
        if (root / name).exists():
            shutil.copy2(root / name, backup / name)
    me.mkdir(exist_ok=True)
    for name in ("profile.json", "accounts.json"):
        if (root / name).exists():
            shutil.copy2(root / name, me / name)
            (me / name).chmod(0o600)
            copied.append(name)
    if (root / "state").is_dir() and not (me / "state").exists():
        # The browser profile inside state/ must not be in use while it is copied.
        shutil.copytree(root / "state", me / "state", symlinks=True)
        copied.append("state/")
    extras = root / "me"
    if extras.resolve() != me.resolve():
        if (extras / "extensions").is_dir() and not (me / "extensions").exists():
            shutil.copytree(extras / "extensions", me / "extensions")
            copied.append("extensions/")
        if (extras / "denylist.txt").exists() and not (me / "denylist.txt").exists():
            shutil.copy2(extras / "denylist.txt", me / "denylist.txt")
            copied.append("denylist.txt")
    return {"copied": copied, "backup": str(backup), "checked_questions": report["questions"], "into": str(me)}
