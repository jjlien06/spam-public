"""Automatic Workday login/signup with local credentials and bounded retries."""
import hashlib
import re
from playwright.sync_api import TimeoutError as BrowserTimeout
from .account_store import origin_of
from .queue import platform
from .workday import account_button, ENTRY_STATE


def _authenticate_once(page, store, should_stop=lambda: False, on_event=lambda text: None):
    def report(status, note):
        return {"status": status, "history": [], "missing": [note], "note": "Application not submitted"}
    if platform(page.url) != "workday":
        return report("account_unsupported", "Account form adapter not implemented for this platform")
    origin, credential, existing = store.credentials(page.url)
    email, password = credential["email"], credential["password"]
    # Missing registry entry is not proof of a missing remote account. A pending
    # signup is reconciled by login, never by repeatedly pressing Create Account.
    operation = "login" if existing or store.has_attempt(origin, email, "create") else "create"
    if operation == "create" and not credential.get("create_if_missing", False):
        return report("account_required", "Automatic account creation disabled")
    if should_stop():
        return report("paused", "Paused before account action")
    # Some tenants list Google/LinkedIn/email options before any form.
    email_option = page.locator('[data-automation-id="SignInWithEmailButton"]:visible')
    if email_option.count() == 1:
        email_option.click(timeout=5000)
        try:
            page.locator('input[data-automation-id="email"]').first.wait_for(state="visible", timeout=15000)
        except BrowserTimeout:
            return report("account_required", "Email sign-in form did not appear")
    switch_id = "signInLink" if operation == "login" else "createAccountLink"
    form_name = "Sign In" if operation == "login" else "Create Account"
    submit_id = "signInSubmitButton" if operation == "login" else "createAccountSubmitButton"
    switch = page.locator(f'[data-automation-id="{switch_id}"]:visible')
    if switch.count() == 1:
        switch.click(timeout=5000)
        # Workday swaps the form asynchronously; wait for the target form's own button.
        try:
            page.locator(f'[data-automation-id="{submit_id}"]').first.wait_for(state="attached", timeout=15000)
        except BrowserTimeout:
            return report("account_required", "Account form did not switch to " + form_name)
    if origin_of(page.url) != origin:
        return report("account_required", "Account origin changed")
    # Account terms are accepted only with the explicit accept_account_terms
    # opt-in, and the exact consent text is recorded; challenges are never solved.
    terms = None
    consent_box = page.locator('input[type="checkbox"][data-automation-id="createAccountCheckbox"]:visible')
    if operation == "create" and (consent_box.count() or page.get_by_text("terms", exact=False).filter(visible=True).count()):
        if not credential.get("accept_account_terms", False):
            return report("terms_review_required", "Account terms require review")
        consent = page.locator('input[type="checkbox"][data-automation-id="createAccountCheckbox"]')
        if consent.count() > 1 or page.locator('input[type="checkbox"]:visible').count() > consent.count():
            return report("terms_review_required", "Unrecognized account consent controls")
        content = page.locator('[data-automation-id="signInContent"]')
        terms = (content.first if content.count() else page.locator("body")).inner_text()
    entries = [("email", email), ("password", password)]
    if operation == "create":
        entries.append(("verifyPassword", password))
    for name, value in entries:
        field = page.locator(f'input[data-automation-id="{name}"]')
        if field.count() != 1 or not field.is_visible():
            return report("account_required", "Expected credential fields not present")
        field.fill(value)
        if not field.evaluate("e=>e.checkValidity()"):
            return report("account_validation_failed", "Credential does not satisfy form requirements")
    if should_stop():
        return report("paused", "Paused before account action")
    if terms is not None:
        consent = page.locator('input[type="checkbox"][data-automation-id="createAccountCheckbox"]')
        if consent.count() == 1:
            consent.check(timeout=5000)
        store.record_terms(origin, terms)
        on_event("Accepted account terms (opt-in); consent text saved")
    # Wrong passwords can lock a Workday account: once this exact password was
    # rejected here, stop until accounts.json holds a different one.
    fingerprint = hashlib.sha256(password.encode()).hexdigest()[:12]
    if operation == "login" and store.was_rejected(origin, email, fingerprint):
        return report("account_rejected", "Sign-in was already rejected with this password; not retried. "
                      "Update this site's login in accounts.json (or reset the password) first")
    if not store.claim(origin, email, operation):
        return report("account_reconciliation_required", "Previous account attempt needs reconciliation; no duplicate sent")
    on_event("Signing in with saved credentials…" if operation == "login" else "Creating account with default credentials…")
    progressed = ("() => ['application', 'applied'].includes((" + ENTRY_STATE + ")()) || [...document.querySelectorAll('[role=alert]')].some(e=>e.innerText.trim())"
                  " || /verify your email|check your email|verification email|activate your account/i.test(document.body.innerText)")
    # After registering, some tenants (QTX) return to their sign-in page.
    signin_after_create = ("() => !document.querySelector('[data-automation-id=\"createAccountSubmitButton\"]') && "
                           "!!document.querySelector('[data-automation-id=\"signInSubmitButton\"], [data-automation-id=\"SignInWithEmailButton\"]')")
    if operation == "create":
        progressed = "() => (" + progressed + ")() || (" + signin_after_create + ")()"
    # A freshly swapped-in Workday form can reset its inputs or ignore the first
    # click until it hydrates. Sign-in is idempotent, so re-enter and retry. Account
    # creation is retried only while no request has reached the site, so at most
    # one registration is ever sent; once one is sent it gets the full wait.
    sent = []
    watch = lambda request: sent.append(request.url) if request.method == "POST" and origin_of(request.url) == origin else None
    page.on("request", watch)
    responded = False
    try:
        for attempt in range(3):
            for name, value in entries:
                field = page.locator(f'input[data-automation-id="{name}"]')
                if field.count() == 1 and field.input_value() != value:
                    field.fill(value)
            before = len(sent)
            if operation == "create":
                # Workday's create form ignores a click that arrives right after the
                # fields and consent box change (P&G, Mastercard): leave the last
                # field and give the form a moment, longer on each retry.
                page.keyboard.press("Tab")
                page.wait_for_timeout(1500 + 2500 * attempt)
            account_button(page, submit_id, form_name).click(timeout=5000)
            last = attempt == 2
            try:
                if operation == "create":
                    page.wait_for_timeout(1500)
                    last = last or len(sent) > before
                page.wait_for_function(progressed, timeout=45000 if last else 8000)
                responded = True
                break
            except BrowserTimeout:
                if last:
                    break
                if page.evaluate(ENTRY_STATE) != "account" or (operation == "create" and sent):
                    # The form is gone (still loading) or the registration went
                    # out while waiting: wait it out, never click again.
                    try:
                        page.wait_for_function(progressed, timeout=45000)
                        responded = True
                    except BrowserTimeout:
                        pass
                    break
    except BrowserTimeout:
        pass
    finally:
        page.remove_listener("request", watch)
    if operation == "create" and not sent and page.evaluate(ENTRY_STATE) == "account":
        # The site rejected the form before sending anything, so no account
        # exists: release the claim so a fixed form can be created later.
        store.release(origin, email, "create")
        errors = page.locator('[role=alert]:visible, [data-automation-id=errorMessage]:visible').all_inner_texts()
        return report("account_validation_failed", "Account form rejected before sending: " + ("; ".join(e.strip() for e in errors if e.strip()) or "no message"))
    if responded and operation == "create" and page.evaluate(signin_after_create):
        store.finish(origin, email, "create", "created")
        on_event("Account created; signing in")
        return _authenticate_once(page, store, should_stop, on_event)
    if not responded:
        return report("account_reconciliation_required", "Account response uncertain; browser remains open")
    if page.evaluate(ENTRY_STATE) in ("application", "applied"):
        store.finish(origin, email, operation, "authenticated")
        on_event("Account authenticated; continuing application.")
        return None
    body = page.locator("body").inner_text().lower()
    if any(text in body for text in ("verify your email", "check your email", "verification email", "activate your account")):
        store.finish(origin, email, operation, "verification_required")
        return report("email_verification_required", "Account email verification is required")
    if operation == "login" and store.status(origin, email, "create") == "created":
        # Right after SPAM created the account this is an unverified email (7 of 7 on
        # 2026-09-23), not a wrong password: keep the password usable and park the job
        # for the email-verification step.
        store.finish(origin, email, operation, "verification_required")
        return report("email_verification_required", "Sign-in rejected right after Create Account: "
                      "click the verification link emailed for the new account, then rerun")
    store.finish(origin, email, operation, "rejected:" + fingerprint if operation == "login" else "rejected")
    if operation == "login":
        store.reject(origin, email, fingerprint)
    return report("account_rejected", "Site rejected the account operation; inspect the visible error")


def authenticate(page, store, should_stop=lambda: False, on_event=lambda text: None):
    """Sign in or create the account; after a rejected password, try each saved
    fallback password once (fresh page each time) and remember the one that works."""
    result = _authenticate_once(page, store, should_stop, on_event)
    if result is None or platform(page.url) != "workday":
        return result
    if result.get("status") == "account_validation_failed" and re.search(r"password", " ".join(result.get("missing", [])), re.I):
        # The site's password rules (e.g. 12+ characters) refused the default for a
        # new account: create it with a saved fallback that satisfies them.
        origin, credential, _ = store.credentials(page.url)
        for password in store.fallback_passwords():
            if password == credential["password"] or should_stop():
                continue
            on_event("Site password rules refused the default; creating the account with a fallback password")
            store.use_password(origin, password, exists=False)
            page.reload(wait_until="domcontentloaded")
            try:
                page.wait_for_function("() => (" + ENTRY_STATE + ")() === 'account'", timeout=15000)
            except BrowserTimeout:
                pass
            result = _authenticate_once(page, store, should_stop, on_event)
            if result is None:
                store.remember(origin, credential["email"], password)
                return None
            if result.get("status") != "account_validation_failed":
                break
        return result
    if result.get("status") != "account_rejected":
        return result
    origin, credential, _ = store.credentials(page.url)
    email = credential["email"]
    for password in store.fallback_passwords():
        fingerprint = hashlib.sha256(password.encode()).hexdigest()[:12]
        if password == credential["password"] or store.was_rejected(origin, email, fingerprint):
            continue
        if should_stop():
            break
        on_event("Trying a saved fallback password for this site")
        store.use_password(origin, password)
        page.reload(wait_until="domcontentloaded")
        try:
            page.wait_for_function("() => (" + ENTRY_STATE + ")() === 'account'", timeout=15000)
        except BrowserTimeout:
            pass
        result = _authenticate_once(page, store, should_stop, on_event)
        if result is None:
            store.remember(origin, email, password)
            on_event("Signed in with the fallback password; saved it for this site")
            return None
        if result.get("status") != "account_rejected":
            return result
    return result
