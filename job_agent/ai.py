"""One way to ask the user's own AI (Claude Code or ChatGPT/Codex) a question, headless and read-only.

Used for drafting (facts from a resume, essay drafts from the user's verified material) and never for filling a
form: every draft is shown to the user and saved only after they confirm it. The provider is chosen in setup and
saved in me/settings.json; either CLI runs with the user's own login.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import paths

PROVIDERS = ("claude", "codex")


class AIError(RuntimeError):
    pass


def _settings_path():
    return paths.home() / "settings.json"


def settings():
    try:
        return json.loads(_settings_path().read_text())
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**settings(), key: value}, indent=2))


def providers():
    """The AI CLIs installed on this Mac."""
    return [name for name in PROVIDERS if shutil.which(name)]


def provider():
    chosen = settings().get("ai")
    return chosen if chosen in PROVIDERS else next(iter(providers()), None)


def save_provider(name):
    if name not in PROVIDERS:
        raise ValueError(f"unknown AI provider {name!r}; use one of {PROVIDERS}")
    save_setting("ai", name)


def _subprocess(command, cwd, timeout):
    # stdin closed: codex exec otherwise waits to read more prompt from an inherited stdin.
    out = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise AIError((out.stderr or out.stdout).strip()[-600:] or f"{command[0]} exited {out.returncode}")
    return out.stdout


def run(prompt, provider=None, cwd=None, timeout=600, runner=_subprocess):
    """The AI's reply as text. It may read files under cwd; it cannot edit anything or run commands."""
    name = provider or globals()["provider"]()
    if name is None:
        raise AIError("No AI found. Install Claude Code (claude) or Codex (codex) and sign in, then rerun setup.")
    cwd = str(cwd or Path.cwd())
    try:
        if name == "claude":
            return runner(["claude", "-p", prompt, "--output-format", "text", "--allowedTools", "Read", "Glob", "Grep"],
                          cwd, timeout).strip()
        with tempfile.TemporaryDirectory() as folder:
            last = Path(folder) / "last.txt"
            runner(["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-C", cwd, "-o", str(last), prompt],
                   cwd, timeout)
            return last.read_text().strip() if last.exists() else ""
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AIError(f"{name} failed: {error}") from error


def run_json(prompt, **kwargs):
    """The AI's reply parsed as one JSON object (the first {...} in it, code fences allowed)."""
    reply = run(prompt, **kwargs)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.S)
    candidate = fenced.group(1) if fenced else reply[reply.find("{"): reply.rfind("}") + 1]
    try:
        value = json.loads(candidate)
    except ValueError as error:
        raise AIError("The AI did not reply with JSON: " + reply[:300]) from error
    if not isinstance(value, dict):
        raise AIError("The AI replied with JSON that is not an object")
    return value
