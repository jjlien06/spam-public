"""Oracle Recruiting Cloud ("Candidate Experience"): banks and industrials host their careers sites here
(<tenant>.fa.<region>.oraclecloud.com/hcmUI/CandidateExperience/...). An application starts at
.../job/<id>/apply/email: the applicant's email, a terms checkbox whose label opens an AGREE dialog (the
user agreed to these candidate terms for Oracle sites, 2026-09-30), then a six-digit PIN emailed to that
address. The page also carries a hidden "honey-pot" box, which a person never sees and SPAM never fills."""
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as BrowserTimeout

CANDIDATE_PATH = re.compile(r"^(/hcmUI/CandidateExperience/[^/]+/sites/[^/]+/job/\d+)")
# Goldman Sachs lists roles on higher.gs.com; its student roles apply on this Oracle site.
GOLDMAN = "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LateralHiring/job/{}/apply/email"
PIN = re.compile(r"(?:passcode|verification code|one-time code|pin)\D{0,40}?\b(\d{6})\b", re.I)


def is_oracle(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    if host == "higher.gs.com":
        return bool(re.match(r"^/roles/\d+", parts.path))
    return host.endswith(".oraclecloud.com") and bool(CANDIDATE_PATH.match(parts.path))


def apply_url(url):
    """Where an application for this posting starts: the email step."""
    parts = urlsplit(url)
    if parts.hostname == "higher.gs.com":
        return GOLDMAN.format(re.match(r"^/roles/(\d+)", parts.path).group(1))
    return f"https://{parts.netloc}{CANDIDATE_PATH.match(parts.path).group(1)}/apply/email"


def pin_from(text):
    match = PIN.search(text or "")
    return match.group(1) if match else None


def _button(page, text):
    """The one visible button whose own text is exactly `text` (never a look-alike such as Submit)."""
    buttons = page.locator("button:visible").filter(has_text=re.compile(rf"^\s*{text}\s*$", re.I))
    return buttons if buttons.count() == 1 else None


def sign_in(page, email, get_code, on_event=lambda text: None, wait=90):
    """Pass the email and PIN steps. get_code(since) returns the emailed PIN (or None) for a code sent
    after `since` (epoch seconds). Returns None once the application's sections are showing."""
    box = page.locator("input[name=primary-email]:visible")
    if box.count():
        box.fill(email)
        terms = page.locator("#legal-disclaimer-checkbox")
        if terms.count() and not terms.is_checked():
            # The row's visual checkbox toggles acceptance on most sites; others accept only through
            # the "terms and conditions" link's dialog and its AGREE button.
            button = page.locator(".apply-flow-input-checkbox__button").first
            if button.count():
                button.dispatch_event("click")  # the hidden input sits on top of it and takes mouse clicks
            if not terms.is_checked():
                page.locator("#legal-disclaimer-link").first.click()
                agree = page.locator("#__oj_zorder_container button:visible").filter(has_text=re.compile(r"^\s*agree\s*$", re.I))
                try:
                    agree.first.wait_for(timeout=5000)
                    agree.first.click()
                except BrowserTimeout:
                    pass
            if not terms.is_checked():
                return {"status": "needs_input", "missing": ["Oracle terms and conditions checkbox did not stick"]}
        from .runner import dismiss_cookie_overlays
        dismiss_cookie_overlays(page, on_event)  # American Express's cookie modal renders late, over Next
        sent = time.time() - 5
        next_button = _button(page, "next")
        if next_button is None:
            return {"status": "needs_input", "missing": ["Oracle email step has no unique Next button"]}
        next_button.click()
        on_event("Email entered; waiting for the emailed verification code")
        try:
            page.locator("#pin-code-1, input[name=firstName], h2").first.wait_for(timeout=20000)
        except BrowserTimeout:
            return {"status": "needs_input", "missing": ["Oracle did not show the verification-code step"]}
    else:
        sent = time.time() - 60
    if page.locator("#pin-code-1:visible").count():
        deadline = time.time() + wait
        code = None
        while code is None and time.time() < deadline:
            code = get_code(sent)
            if code is None:
                time.sleep(5)
        if code is None:
            return {"status": "email_code_required", "missing": ["Oracle emailed a six-digit code that was not found in Gmail"],
                    "note": "Enter the code from the email, or run the job again for a new one"}
        for index, digit in enumerate(code, 1):
            page.locator(f"#pin-code-{index}").fill(digit)
        verify = _button(page, "verify")
        if verify is None:
            return {"status": "needs_input", "missing": ["Oracle code step has no unique Verify button"]}
        verify.click()
        on_event("Verification code entered")
        try:
            page.locator("#pin-code-1").first.wait_for(state="detached", timeout=20000)
        except BrowserTimeout:
            return {"status": "needs_input", "missing": ["Oracle did not accept the verification code"]}
    return None


def gmail_code(email, search=None):
    """get_code for sign_in: the newest six-digit code emailed to `email` after `since`."""
    from datetime import datetime
    from . import gmail
    search = search or gmail.search

    def get(since):
        for message in search(f"to:{email} (passcode OR \"verification code\") newer_than:1h", limit=5):
            try:
                at = datetime.fromisoformat(message.get("date", "")).timestamp()
            except ValueError:
                continue
            if at >= since:
                code = pin_from(message.get("body") or message.get("snippet"))
                if code:
                    return code
        return None
    return get
