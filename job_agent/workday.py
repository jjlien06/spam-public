"""Workday entry flow verified against a live Workday tenant.

No credentials are entered and no account mutation is performed here.
"""
import re
from urllib.parse import urlsplit, urljoin
from playwright.sync_api import TimeoutError as BrowserTimeout
from .queue import platform
from .accounts import Vault

TRANSIENT_ERROR = re.compile(r"something went wrong[\s\S]{0,200}refresh the page", re.IGNORECASE)
# The draft either renders real form fields or fails; wait for one of the two.
# Header menus and the honeypot input render first, so count only field wrappers.
SETTLED_OR_FAILED = ("() => /something went wrong[\\s\\S]{0,200}refresh the page/i.test(document.body.innerText)"
                     " || document.querySelectorAll('[data-automation-id^=\"formField\"]').length > 0"
                     " || [...document.querySelectorAll('button,[role=button]')].some(b => /^(submit|submit application)$/i.test(b.textContent.trim()))")


def account_button(page, automation_id, accessible_name):
    """Workday may expose a visible role-button over an aria-hidden submit."""
    # The overlay sits beside the real button; its label varies by tenant
    # ("Sign In" on Red Maple, "Submit" on QTX), so find it by position first.
    beside = [o for o in page.locator(f'[data-automation-id="{automation_id}"]').locator("xpath=..")
              .locator('[data-automation-id="click_filter"]').all() if o.is_visible()]
    if len(beside) == 1:
        return beside[0]
    click_filter = page.locator('[data-automation-id="click_filter"]').and_(page.get_by_role("button", name=accessible_name, exact=True))
    if click_filter.count() == 1:
        return click_filter
    overlay = page.get_by_role("button", name=accessible_name, exact=True)
    if overlay.count() == 1:
        return overlay
    return page.locator(f'[data-automation-id="{automation_id}"]')


def create_account(page, email, vault=None):
    """Create only on the observed target tenant, once per stored credential.

    Stored-before-click prevents losing a generated password on timeout. A
    pending credential requires reconciliation rather than another signup.
    This function is not invoked by prepare: caller must select account creation.
    """
    parsed = urlsplit(page.url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".myworkdayjobs.com"):
        raise ValueError("Account creation is only implemented for Workday tenants")
    origin = f"https://{parsed.netloc}"
    if not email or "@" not in email or any(c.isspace() for c in email):
        raise ValueError("A valid application email is required")
    vault = vault or Vault()
    if vault.get(origin, email):
        return {"status": "account_reconciliation_required", "note": "Existing or pending credential found; no signup repeated"}
    fields = [page.locator(f'input[data-automation-id="{name}"]') for name in ("email", "password", "verifyPassword")]
    button = account_button(page, "createAccountSubmitButton", "Create Account")
    if any(f.count() != 1 or not f.is_visible() for f in fields) or button.count() != 1:
        return {"status": "needs_input", "note": "Expected account creation form not present"}
    password = vault.generate()
    for field, value in zip(fields, (email, password, password)):
        field.fill(value)
    if any(not f.evaluate("e=>e.checkValidity()") for f in fields):
        return {"status": "needs_input", "note": "Account field validation failed; creation not attempted"}
    vault.set(origin, email, password)
    try:
        button.click(timeout=5000)
    except BrowserTimeout:
        return {"status": "account_reconciliation_required", "note": "Creation outcome uncertain; credential retained; do not repeat signup"}
    # Clicking is not proof of account creation. Verification/login follows separately.
    return {"status": "account_reconciliation_required",
            "note": "Creation requested; account outcome must be checked before retrying; email verification may or may not be required"}


# Require application content and navigation together: sidebar step names alone
# are also present at the account gate and do not establish authentication.
ENTRY_STATE = """() => {
    const visible = e => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
    const all = selector => [...document.querySelectorAll(selector)].filter(visible);
    // Mid-navigation the document can have no body yet (RBC); report no state.
    if (!document.body) return null;
    if (all('input[type="password"], [data-automation-id="createAccountSubmitButton"], [data-automation-id="signInSubmitButton"], [data-automation-id="signInLink"], [data-automation-id="forgotPasswordLink"], [data-automation-id="SignInWithEmailButton"]').length) return 'account';
    // Closed or removed postings (one tenant: "The page you are looking for doesn't exist").
    if (/page you are looking for (?:doesn['’]t|does not) exist|posting is no longer available|no longer accepting applications|page que vous recherchez n['’]existe pas/i.test(document.body.innerText)) return 'closed';
    // A signed-in candidate who already applied sees this instead of the form.
    if (/you(?:['’]ve already applied for this job| applied for this job on )/i.test(document.body.innerText)) return 'applied';
    const steps = new Set(['My Information', 'My Experience', 'Application Questions', 'Voluntary Disclosures', 'Review']);
    const heading = all('h1,h2,h3,[role="heading"],[data-automation-id="pageHeader"]')
        .some(e => steps.has(e.textContent.trim()));
    const navigation = all('button,input[type="submit"],[role="button"]')
        .some(e => /^(next|continue|save and continue|submit|submit application)$/i.test(
            (e.getAttribute('aria-label') || e.value || e.textContent).trim()));
    if (heading && navigation) return 'application';
    // The application chooser can leave the public Apply link visible behind
    // its overlay. Prefer the visible chooser action and allow any element
    // type, since the tenant may render it as a div or span.
    if (all('*').some(e => e.textContent.trim() === 'Apply Manually' &&
        ![...e.children].some(child => visible(child)))) return 'manual';
    // A signed-in candidate with a draft sees Continue Application instead of Apply.
    if (all('[data-automation-id="adventureButton"],[data-automation-id="continueButton"]').length) return 'entry';
    return false;
}"""
ENTRY_LINK = '[data-automation-id="adventureButton"]:visible, [data-automation-id="continueButton"]:visible'


def enter_application(page):
    """Return an account/error report, or None to prepare a recognized form.

    Uses the existing browser session only; never signs in or retries signup.
    """
    text = page.locator('body').inner_text()
    if re.search(r'please complete a security check|verify you are human', text, re.I) and page.locator('iframe[src*="challenges.cloudflare.com"]').count():
        return {"status": "captcha_required", "missing": ["Complete the visible security check, then Run / Resume"],
                "note": "Application form has not been reached"}
    if platform(page.url) != "workday":
        # Some embedded application forms remain hidden behind a posting CTA.
        # Only reveal when there are no visible applicant fields; never treat
        # a form's final Apply/Submit control as an entry action.
        # A site-search box in the page header (Intuit's careers "Location" type-ahead) is not an applicant field.
        fields = page.locator('input:not([type=hidden]):not([type=search]),textarea,select').filter(visible=True)
        if fields.evaluate_all("els => els.filter(e => !e.closest('[role=search]')).length"):
            return None
        # Posting pages often repeat the same application link above and below
        # the description. Deduplicate destinations, not DOM element counts.
        destinations = {}
        current = urlsplit(page.url)
        for link in page.get_by_role('link', name=re.compile(r'^Apply(?: Now)?$', re.I)).all():
            if not link.is_visible():
                continue
            destination = urljoin(page.url, link.get_attribute('href') or '')
            parsed = urlsplit(destination)
            if (parsed.scheme == 'https' and parsed.netloc == current.netloc
                    and parsed.path.rstrip('/') == current.path.rstrip('/') + '/apply'):
                destinations[destination] = link
            # Cross-origin ATS handoffs require an explicitly selected target URL.
            # An arbitrary Apply link is not authority to leave the posting origin.
        if len(destinations) == 1:
            next(iter(destinations.values())).click(timeout=5000)
            page.wait_for_load_state('domcontentloaded')
            return None
        candidates = page.get_by_role('button', name=re.compile(r'^Apply for this position$', re.I)).filter(visible=True).all()
        leaves = [b for b in candidates if not b.locator('button,[role=button]').count()]
        if len(leaves) == 1:
            button = leaves[0]
            if button.evaluate("e=>!!e.form && e.type==='submit'"):
                return {"status": "needs_input", "missing": ["Application entry could submit a form"]}
            button.click(timeout=5000)
            try:
                page.locator('input:not([type=hidden]),textarea,select').filter(visible=True).first.wait_for(timeout=10000)
            except BrowserTimeout:
                return {"status": "needs_input", "missing": ["Application entry did not reveal a form"]}
        return None
    history = []
    try:
        for _ in range(3):
            state = page.wait_for_function(ENTRY_STATE, timeout=45000).json_value()
            if platform(page.url) != "workday":
                break
            if state == "application":
                return None
            if state == "closed":
                return {"status": "posting_closed", "missing": [], "history": history,
                        "note": "Posting is closed or removed; nothing entered"}
            if state == "applied":
                return {"status": "already_applied", "missing": [], "history": history,
                        "note": "Site reports this job was already applied to; nothing entered"}
            if state == "account":
                return {"status": "account_required", "missing": ["Workday account sign-in or creation required"],
                        "history": history, "note": "No credentials entered; no account created or application submitted"}
            if state == "entry":
                entry = page.locator(ENTRY_LINK).first
                destination = urljoin(page.url, entry.get_attribute("href") or "")
                if (urlsplit(destination).scheme != "https" or
                        urlsplit(destination).netloc != urlsplit(page.url).netloc or
                        not urlsplit(destination).path.endswith("/apply")):
                    return {"status": "needs_input", "missing": ["Unrecognized Workday application link"], "history": history}
                entry.click(timeout=5000)
                history.append({"action": "opened_application"})
            elif state == "manual":
                page.get_by_text("Apply Manually", exact=True).filter(visible=True).click(timeout=5000)
            page.wait_for_function("previous => { const state = (" + ENTRY_STATE + ")(); return state && state !== previous; }",
                                   arg=state, timeout=45000)
        return {"status": "needs_input", "missing": ["Workday entry made no recognized progress"], "history": history}
    except BrowserTimeout:
        return {"status": "needs_input", "missing": ["Workday entry flow changed or timed out"], "history": history,
                "note": "Account actions and submission not attempted"}


def in_application(page):
    """True when the current page already shows an application form step."""
    try:
        return platform(page.url) == "workday" and page.evaluate(ENTRY_STATE) == "application"
    except Exception:
        return False


def english(url):
    """Pin a Workday link to en-US: tenants remember the last locale (a fr-CA link
    turns later QTX pages French), and SPAM's controls and messages are English."""
    if platform(url) != "workday":
        return url
    parts = urlsplit(url)
    segments = parts.path.lstrip("/").split("/")
    # Any case: some lists link "en-us", which must not become "/en-US/en-us/..." (an invalid URL).
    if segments and re.fullmatch(r"[a-z]{2}-[a-z]{2}", segments[0], re.I):
        segments = segments[1:]
    return parts._replace(path="/en-US/" + "/".join(segments)).geturl()


POSTING = {"text": ""}  # the posting's description, for ordering skills by its keywords (one job per process)


def remember_posting(page):
    try:
        description = page.locator("[data-automation-id=jobPostingDescription]")
        description.first.wait_for(timeout=8000)
        POSTING["text"] = description.first.inner_text()[:30000]
    except Exception:
        try:
            POSTING["text"] = page.locator("body").inner_text()[:30000]
        except Exception:
            POSTING["text"] = ""


def form_url(url):
    """The application form of a Lever or Ashby posting (its /apply or /application page); the posting only
    links to it."""
    parts = urlsplit(url)
    page = {"jobs.lever.co": "/apply", "jobs.ashbyhq.com": "/application"}.get(parts.hostname)
    if page and re.fullmatch(r"/[^/]+/[0-9a-f-]{36}/?", parts.path):
        return parts._replace(path=parts.path.rstrip("/") + page, query="", fragment="").geturl()
    return url


def greenhouse_embed_url(url):
    """Greenhouse's embeddable form for a hosted board job, which boards that redirect
    their job pages to a company careers site (Coinbase, Epic) still serve; else None."""
    parts = urlsplit(url)
    match = re.fullmatch(r"/([^/]+)/jobs/(\d+)/?", parts.path)
    if not (parts.hostname or "").endswith("greenhouse.io") or not match:
        return None
    return f"https://job-boards.greenhouse.io/embed/job_app?for={match.group(1)}&token={match.group(2)}"


def open_application(page, url, store=None, should_stop=lambda: False, on_event=lambda text: None):
    """Open a posting, pass the entry chooser and account gate, and land on the form.

    Returns a status report when the application cannot be reached, or None when
    the page shows the application form. Signing in from the manual-apply chooser
    while a draft already exists trips Workday's "Something went wrong" page; the
    authenticated session is then re-entered from the posting, which loads the draft.
    """
    if platform(url) == "oracle":
        from .oracle import apply_url, gmail_code, sign_in
        email = store.credentials(url)[1].get("email") if store is not None else None
        if not email:
            return {"status": "needs_input", "history": [], "missing": ["Oracle sign-in needs the default email in accounts.json"]}
        page.goto(apply_url(url), wait_until="domcontentloaded", timeout=45000)
        page.locator("input[name=primary-email], #pin-code-1, input[name=firstName]").first.wait_for(timeout=30000)
        from .runner import dismiss_cookie_overlays
        dismiss_cookie_overlays(page, on_event)  # American Express's cookie modal covers the Next button
        gate = sign_in(page, email, gmail_code(email), on_event)
        return gate if gate is None else {"history": [], **gate}
    if platform(url) == "icims":
        from .icims import open_application as open_icims
        email = store.credentials(url)[1].get("email") if store is not None else None
        return open_icims(page, url, email, on_event)
    url = form_url(english(url))
    from .ashby import install as queue_ashby_saves
    queue_ashby_saves(page)  # before the page loads: Ashby answer saves must not overlap
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    embed = greenhouse_embed_url(url)
    if embed and not (urlsplit(page.url).hostname or "").endswith("greenhouse.io"):
        on_event("Greenhouse board redirected to the company site; opening its embedded form")
        page.goto(embed, wait_until="domcontentloaded", timeout=45000)
    remember_posting(page)
    gate = enter_application(page)
    if gate and gate["status"] == "account_required" and store is not None:
        from .auth import authenticate
        gate = authenticate(page, store, should_stop, on_event)
        if gate is None and platform(url) == "workday":
            # Never continue from the manual-apply page after signing in.
            on_event("Signed in; re-entering the application from the job posting")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            gate = enter_application(page)
    if gate is not None or platform(url) != "workday":
        return gate
    try:
        page.wait_for_function(SETTLED_OR_FAILED, timeout=45000)
    except BrowserTimeout:
        return {"status": "needs_input", "history": [], "missing": ["Workday draft did not load its form fields"],
                "note": "Delete the unsubmitted draft from Candidate Home to start over"}
    if TRANSIENT_ERROR.search(page.locator("body").inner_text()):
        return {"status": "needs_input", "history": [], "missing": ["Workday failed to open the draft"],
                "note": "Delete the unsubmitted draft from Candidate Home to start over"}
    return None
