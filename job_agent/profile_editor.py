"""Persist a new exact answer without changing unrelated profile facts."""
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from .profile import Profile, normalize


def with_answer(data, question, answer):
    """Preview the same exact-answer update used by the controller and Answer Deck."""
    data = deepcopy(data)
    question = question.strip()
    if not question or answer == "":
        raise ValueError("Question and answer are required")
    answers = data.setdefault("answers", {})
    key = next((k for k, value in answers.items() if normalize(question) in
                [normalize(k), *[normalize(a) for a in (value.get("aliases", []) if isinstance(value, dict) else [])]]), None)
    if key is None:
        key = next((k for k in answers if normalize(k) == normalize(question)), question)
    old = answers.get(key)
    answers[key] = {**old, "value": answer} if isinstance(old, dict) else {"value": answer, "aliases": []}
    Profile(data)  # Reject conflicting aliases before replacing the source.
    return data


def write_profile(path, data):
    """Validate and atomically replace a private profile."""
    path = Path(path)
    Profile(data)
    fd, temp = tempfile.mkstemp(prefix=".profile-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_answer(path, question, answer):
    path = Path(path)
    write_profile(path, with_answer(json.loads(path.read_text()), question, answer))
