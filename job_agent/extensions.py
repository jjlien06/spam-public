"""Private add-ons in me/extensions/*.py, loaded only when present (the shared repo ships none).

An extension defines a mode: MODE = "name", plus any of
  adjust_profile(profile, job) -> profile   the profile a job in this mode is filled and checked with
  claims(job) -> bool                       the job belongs to this mode (default: its note starts "<Name> mode")
A job filled in a mode is submitted in the same mode (the run records it; Approve passes SPAM_MODE).
"""
import importlib.util
import re
import sys

from . import paths


def load():
    folder = paths.extensions()
    found = []
    for path in sorted(folder.glob("*.py")) if folder.exists() else []:
        try:
            spec = importlib.util.spec_from_file_location(f"spam_ext_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            found.append(module)
        except Exception as error:  # a private add-on must never break a fill
            print(f"extension {path.name} skipped: {error}", file=sys.stderr)
    return found


class UnknownMode(ValueError):
    """A job was filled in a mode whose extension is not installed here."""


def _call(module, hook, *args, default=None):
    try:
        return getattr(module, hook)(*args)
    except Exception as error:  # a private add-on must never break a fill
        print(f"extension {module.MODE}.{hook} failed: {error!r}", file=sys.stderr)
        return default


def _modes():
    return [m for m in load() if getattr(m, "MODE", None)]


def modes():
    return [m.MODE for m in _modes()]


def _claims(module, job):
    for hook in ("claims", "pick_filter"):
        if hasattr(module, hook):
            return bool(_call(module, hook, job, default=False))
    return str(job.get("note") or "").lower().startswith(f"{module.MODE} mode")


def mode_tagged(job):
    """The mode that claims this job, or None."""
    return next((m.MODE for m in _modes() if _claims(m, job)), None)


MODE_NOTE = re.compile(r"^\s*\w+ mode\b", re.I)


def pick_filter(job, mode):
    """With a mode, only that mode's jobs; without one, only jobs no mode claims — including jobs noted
    "<Name> mode ..." whose extension is not installed here."""
    if mode is None and MODE_NOTE.match(str(job.get("note") or "")):
        return False
    return mode_tagged(job) == mode


def adjust_profile(profile, job, mode):
    if mode is None:
        return profile
    found = [m for m in _modes() if m.MODE == mode]
    if not found:
        raise UnknownMode(f"mode {mode!r} is not installed (no me/extensions module defines it)")
    for module in found:
        if hasattr(module, "adjust_profile"):
            adjusted = _call(module, "adjust_profile", profile, job)
            if adjusted is None:
                raise UnknownMode(f"mode {mode!r} failed to adjust the profile")
            profile = adjusted
    return profile
