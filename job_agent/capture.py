"""Save a page SPAM could not handle, so it can become a fixture and a fix."""
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import paths

INVENTORY = """() => [...document.querySelectorAll('input,select,textarea,button,[role=combobox],[role=listbox],[role=radiogroup],[data-automation-id]')]
 .filter(e => e.offsetWidth || e.offsetHeight || e.getClientRects().length)
 .map(e => ({tag: e.tagName, type: e.type || null, role: e.getAttribute('role'), aid: e.getAttribute('data-automation-id'),
   id: e.id || null, name: e.name || null, label: e.getAttribute('aria-label'),
   required: e.required || e.getAttribute('aria-required'), text: (e.innerText || '').trim().slice(0, 80)}))"""


def capture(page, reason, directory=None):
    """Write <time>-<host>.{html,json,png} under directory; return the html path or None.

    Captures are private local diagnostics. HTML and screenshots can contain
    applicant details; never treat them as sanitized public fixtures.
    """
    directory = Path(directory) if directory else paths.state() / "bugs"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stem = directory / (time.strftime("%Y%m%d-%H%M%S") + "-" + (urlsplit(page.url).hostname or "page"))
        html = re.sub(r'\sclass="[^"]*"|\sstyle="[^"]*"', "", page.content())
        stem.with_suffix(".html").write_text(re.sub(r"<svg.*?</svg>", "<svg/>", html, flags=re.S))
        stem.with_suffix(".json").write_text(json.dumps({"reason": reason, "url": page.url,
                                                         "inventory": page.evaluate(INVENTORY)}, indent=1))
        page.screenshot(path=str(stem.with_suffix(".png")))
        for suffix in (".html", ".json", ".png"):
            stem.with_suffix(suffix).chmod(0o600)
        return stem.with_suffix(".html")
    except Exception:
        return None  # capturing must never turn a clean stop into a crash
