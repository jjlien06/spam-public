"""Where one person's data lives: me/ (git-ignored), or SPAM_HOME if set. A copy made before me/ existed keeps
its root-level profile.json, accounts.json and state/ until it is migrated."""
import os
from pathlib import Path


def home():
    if os.environ.get("SPAM_HOME"):
        return Path(os.environ["SPAM_HOME"])
    cwd = Path.cwd()
    if (cwd / "profile.json").exists() and not (cwd / "me" / "profile.json").exists():
        return cwd  # legacy layout (me/ may already hold private extras)
    return cwd / "me"


def profile():
    return home() / "profile.json"


def accounts():
    return home() / "accounts.json"


def state():
    return home() / "state"


def extensions():
    """Private add-ons: always under me/ (or SPAM_HOME), even in the legacy layout."""
    if os.environ.get("SPAM_HOME"):
        return Path(os.environ["SPAM_HOME"]) / "extensions"
    return Path.cwd() / "me" / "extensions"
