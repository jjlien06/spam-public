import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from job_agent.account_store import AccountStore, origin_of
from job_agent.auth import authenticate


ORIGIN = "https://fixture.wd5.myworkdayjobs.com"
TARGET = ORIGIN + "/External/job/R10001/apply"


def _store(tmp_path, payload):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(payload))
    return AccountStore(path, tmp_path / "state")


def _page(tmp_path, html):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(TARGET)
    return playwright, browser, page


def test_account_store_uses_origin_override_and_claims_once(tmp_path):
    store = _store(tmp_path, {
        "default": {"email": "default@example.com", "password": "default-secret", "create_if_missing": True},
        "accounts": {ORIGIN: {"email": "target@example.com", "password": "target-secret", "exists": True}},
    })
    try:
        origin, credentials, existing = store.credentials(TARGET)
        assert origin == ORIGIN
        assert credentials["email"] == "target@example.com"
        assert existing is True
        assert store.claim(origin, credentials["email"], "login") is True
        assert store.claim(origin, credentials["email"], "create") is True
        assert store.claim(origin, credentials["email"], "create") is False
    finally:
        store.db.close()


def test_login_claim_is_retryable_after_uncertain_or_rejected_outcome(tmp_path):
    # Sign-in is idempotent: a timed-out or rejected attempt must not lock the site out.
    store = _store(tmp_path, {"default": {"email": "default@example.com", "password": "default-secret"}})
    try:
        assert store.claim(ORIGIN, "default@example.com", "login") is True
        assert store.claim(ORIGIN, "default@example.com", "login") is True, "uncertain login must be retryable"
        store.finish(ORIGIN, "default@example.com", "login", "rejected")
        assert store.claim(ORIGIN, "default@example.com", "login") is True, "rejected login must be retryable"
        # Account creation stays single-shot until reconciled.
        assert store.claim(ORIGIN, "default@example.com", "create") is True
        assert store.claim(ORIGIN, "default@example.com", "create") is False
    finally:
        store.db.close()


def test_authenticates_existing_account_through_visible_signin_overlay(tmp_path):
    html = """<input data-automation-id=email type=email required>
      <input data-automation-id=password type=password required>
      <input data-automation-id=signInSubmitButton type=submit aria-hidden=true>
      <div role=button data-automation-id=click_filter aria-label='Sign In'
        onclick="document.body.innerHTML='<h2>My Information</h2><button>Next</button>'">Sign In</div>"""
    store = _store(tmp_path, {
        "default": {"email": "target@example.com", "password": "target-secret", "create_if_missing": True},
        "accounts": {ORIGIN: {"exists": True}},
    })
    playwright, browser, page = _page(tmp_path, html)
    events = []
    try:
        assert authenticate(page, store, on_event=events.append) is None
        assert events == ["Signing in with saved credentials…", "Account authenticated; continuing application."]
        row = store.db.execute("SELECT status FROM attempts").fetchone()
        assert row[0] == "authenticated"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_auth_create_stops_for_terms_without_claiming_or_clicking(tmp_path):
    html = """<p>Review our account terms before continuing.</p>
      <input data-automation-id=email type=email><input data-automation-id=password type=password>
      <input data-automation-id=verifyPassword type=password>
      <button data-automation-id=createAccountSubmitButton>Create Account</button>"""
    store = _store(tmp_path, {"default": {"email": "new@example.com", "password": "new-secret", "create_if_missing": True}, "accounts": {}})
    playwright, browser, page = _page(tmp_path, html)
    try:
        result = authenticate(page, store)
        assert result["status"] == "terms_review_required"
        assert store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_pending_create_reconciles_by_login_and_never_repeats_create(tmp_path):
    html = """<input data-automation-id=email type=email><input data-automation-id=password type=password>
      <input data-automation-id=signInSubmitButton type=submit aria-hidden=true>
      <div role=button aria-label='Sign In' onclick="document.body.innerHTML='<h2>My Information</h2><button>Next</button>'">Sign In</div>"""
    store = _store(tmp_path, {"default": {"email": "pending@example.com", "password": "pending-secret", "create_if_missing": True}, "accounts": {}})
    origin, credentials, _ = store.credentials(TARGET)
    assert store.claim(origin, credentials["email"], "create") is True
    playwright, browser, page = _page(tmp_path, html)
    try:
        assert authenticate(page, store) is None
        assert store.db.execute("SELECT operation,status FROM attempts ORDER BY operation").fetchall() == [("create", "uncertain"), ("login", "authenticated")]
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_origin_validation_rejects_http_and_embedded_credentials():
    with pytest.raises(ValueError):
        origin_of("http://fixture.wd5.myworkdayjobs.com/apply")
    with pytest.raises(ValueError):
        origin_of("https://user:password@fixture.wd5.myworkdayjobs.com/apply")


def test_signin_waits_for_asynchronously_swapped_form(tmp_path):
    # Workday replaces the Create Account form with the Sign In form after a short
    # delay; the old email field is still present at click time and briefly absent after.
    html = """<div id=create>
        <input data-automation-id=email type=email><input data-automation-id=password type=password>
        <input data-automation-id=verifyPassword type=password>
        <button data-automation-id=createAccountSubmitButton type=button>Create Account</button>
        <button data-automation-id=signInLink type=button>Sign In</button></div>
      <script>
        document.querySelector('[data-automation-id=signInLink]').onclick = () => {
          document.getElementById('create').remove();
          setTimeout(() => {
            document.body.innerHTML = '<input data-automation-id=email type=email required>' +
              '<input data-automation-id=password type=password required>' +
              '<button data-automation-id=signInSubmitButton type=button>Sign In</button>';
            document.querySelector('[data-automation-id=signInSubmitButton]').onclick = () => {
              document.body.innerHTML = '<h2>My Information</h2><button>Next</button>';
            };
          }, 400);
        };
      </script>"""
    store = _store(tmp_path, {
        "default": {"email": "target@example.com", "password": "target-secret"},
        "accounts": {ORIGIN: {"exists": True}},
    })
    playwright, browser, page = _page(tmp_path, html)
    try:
        assert authenticate(page, store) is None
        assert store.db.execute("SELECT status FROM attempts").fetchone()[0] == "authenticated"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_signin_survives_late_form_hydration(tmp_path):
    # Workday's swapped-in form can reset controlled inputs and attach its click
    # handler a few hundred milliseconds after the elements appear.
    html = """<button data-automation-id=signInLink type=button>Sign In</button>
      <script>
        document.querySelector('[data-automation-id=signInLink]').onclick = () => {
          document.body.innerHTML = '<input data-automation-id=email type=email required>' +
            '<input data-automation-id=password type=password required>' +
            '<button data-automation-id=signInSubmitButton type=button>Sign In</button>';
          const button = document.querySelector('[data-automation-id=signInSubmitButton]');
          setTimeout(() => document.querySelectorAll('input').forEach(i => i.value = ''), 300);
          setTimeout(() => { button.onclick = () => {
            const ok = document.querySelector('[data-automation-id=email]').value === 'target@example.com' &&
              document.querySelector('[data-automation-id=password]').value === 'target-secret';
            document.body.innerHTML = ok ? '<h2>My Information</h2><button>Next</button>' : '<div role=alert>Bad credentials</div>';
          }; }, 600);
        };
      </script>"""
    store = _store(tmp_path, {
        "default": {"email": "target@example.com", "password": "target-secret"},
        "accounts": {ORIGIN: {"exists": True}},
    })
    playwright, browser, page = _page(tmp_path, html)
    try:
        assert authenticate(page, store) is None
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


# QTX (qtxhr) shows sign-in options first; the email form appears on click.
SIGN_IN_OPTIONS = """<div data-automation-id=signInContent><h2>Sign In</h2>
  <p>1. I have read and agree to the Terms of Use and the Job Applicant Privacy Notice of QTX.</p>
  <button data-automation-id=GoogleSignInButton type=button>Sign in with Google</button>
  <button data-automation-id=SignInWithEmailButton type=submit onclick="event.preventDefault(); showSignIn()">Sign in with email</button></div>
  <script>
  function showSignIn() { document.querySelector('[data-automation-id=signInContent]').innerHTML = `
    <h2>Sign In</h2><input data-automation-id=email type=text><input data-automation-id=password type=password>
    <button data-automation-id=signInSubmitButton type=button onclick="window.signedIn=true; document.body.innerHTML='<h2>My Information</h2><button>Next</button>'">Sign In</button>
    <button data-automation-id=createAccountLink type=button onclick="showCreate()">Create Account</button>`; }
  function showCreate() { document.querySelector('[data-automation-id=signInContent]').innerHTML = `
    <h2>Create Account</h2><input data-automation-id=email type=text><input data-automation-id=password type=password>
    <input data-automation-id=verifyPassword type=password>
    <p>1. I have read and agree to the Terms of Use and the Job Applicant Privacy Notice of QTX.</p>
    <label><input data-automation-id=createAccountCheckbox type=checkbox>Yes, I have read and consent to the terms and conditions</label>
    <button data-automation-id=createAccountSubmitButton type=button onclick="window.created=document.querySelector('[data-automation-id=createAccountCheckbox]').checked; document.body.innerHTML='<h2>My Information</h2><button>Next</button>'">Create Account</button>`; }
  </script>"""


def test_workday_sign_in_options_page_is_an_account_gate(tmp_path):
    from job_agent.workday import ENTRY_STATE
    playwright, browser, page = _page(tmp_path, SIGN_IN_OPTIONS)
    try:
        assert page.evaluate(ENTRY_STATE) == "account"
    finally:
        browser.close()
        playwright.stop()


def test_existing_account_signs_in_through_email_option(tmp_path):
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123"}, "accounts": {ORIGIN: {"exists": True}}})
    playwright, browser, page = _page(tmp_path, SIGN_IN_OPTIONS)
    try:
        assert authenticate(page, store) is None
        assert page.evaluate("window.signedIn") is True
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_create_ticks_consent_only_when_opted_in_and_records_the_terms(tmp_path):
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123", "create_if_missing": True,
                                          "accept_account_terms": True}, "accounts": {}})
    playwright, browser, page = _page(tmp_path, SIGN_IN_OPTIONS)
    events = []
    try:
        assert authenticate(page, store, on_event=events.append) is None
        assert page.evaluate("window.created") is True
        origin, text = store.db.execute("SELECT origin, text FROM terms").fetchone()
        assert origin == ORIGIN and "Terms of Use" in text and "consent to the terms" in text
        assert any("terms" in event.lower() for event in events)
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_create_without_opt_in_still_stops_at_terms(tmp_path):
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123", "create_if_missing": True}, "accounts": {}})
    playwright, browser, page = _page(tmp_path, SIGN_IN_OPTIONS)
    try:
        assert authenticate(page, store)["status"] == "terms_review_required"
        assert page.evaluate("window.created") is None
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def _create_page_with_options(tmp_path, ignore_first_click):
    """Create form whose button ignores clicks until hydrated, then POSTs
    /register and redirects to the email sign-in page (QTX live behaviour)."""
    html = f"""<div data-automation-id=signInContent><h2>Create Account</h2>
      <input data-automation-id=email type=text><input data-automation-id=password type=password>
      <input data-automation-id=verifyPassword type=password>
      <p>I have read and agree to the Terms of Use.</p>
      <label><input data-automation-id=createAccountCheckbox type=checkbox>Yes, I consent to the terms and conditions</label>
      <button data-automation-id=createAccountSubmitButton type=button>Create Account</button></div>
      <script>
      let ignore = {str(ignore_first_click).lower()};
      document.querySelector('[data-automation-id=createAccountSubmitButton]').onclick = () => {{
        if (ignore) {{ ignore = false; return; }}
        fetch('/register', {{method: 'POST'}}).then(() => {{ document.body.innerHTML = `<h2>Sign In</h2>
          <input data-automation-id=email type=text><input data-automation-id=password type=password>
          <button data-automation-id=signInSubmitButton type=button onclick="window.signedIn=true; document.body.innerHTML='<h2>My Information</h2><button>Next</button>'">Sign In</button>`; }});
      }};
      </script>"""
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    posts = []
    def handle(route):
        if route.request.method == "POST":
            posts.append(route.request.url)
            return route.fulfill(status=200, body="{}")
        route.fulfill(status=200, content_type="text/html", body=html)
    page.route("**/*", handle)
    page.goto(TARGET)
    return playwright, browser, page, posts


@pytest.mark.parametrize("ignore_first_click", [False, True])
def test_create_retries_only_unsent_clicks_then_signs_in_after_redirect(tmp_path, ignore_first_click):
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123", "create_if_missing": True,
                                          "accept_account_terms": True}, "accounts": {}})
    playwright, browser, page, posts = _create_page_with_options(tmp_path, ignore_first_click)
    try:
        assert authenticate(page, store) is None
        assert len(posts) == 1, "exactly one registration request"
        assert page.evaluate("window.signedIn") is True
        rows = dict(store.db.execute("SELECT operation, status FROM attempts").fetchall())
        assert rows == {"create": "created", "login": "authenticated"}
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_create_never_clicks_again_after_a_late_registration_request(tmp_path):
    # The POST goes out 3s after the click; the page never confirms. One request only.
    html = """<input data-automation-id=email type=text><input data-automation-id=password type=password>
      <input data-automation-id=verifyPassword type=password>
      <button data-automation-id=createAccountSubmitButton type=button
        onclick="setTimeout(() => fetch('/register', {method: 'POST'}), 3000)">Create Account</button>"""
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123", "create_if_missing": True}, "accounts": {}})
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    posts = []
    page.route("**/*", lambda route: (posts.append(1), route.fulfill(status=200, body="{}")) if route.request.method == "POST"
               else route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(TARGET)
    try:
        page.set_default_timeout(60000)
        assert authenticate(page, store)["status"] == "account_reconciliation_required"
        assert len(posts) == 1
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_signin_keeps_waiting_through_a_slow_loading_page(tmp_path):
    # QTX: after Sign In the form is replaced by "Loading" for longer than the retry window.
    html = """<input data-automation-id=email type=text><input data-automation-id=password type=password>
      <button data-automation-id=signInSubmitButton type=button onclick="window.clicks=(window.clicks||0)+1;
        document.body.innerHTML='<p>Loading</p>';
        setTimeout(() => document.body.innerHTML='<h2>My Information</h2><button>Next</button>', 10000)">Sign In</button>"""
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123"}, "accounts": {ORIGIN: {"exists": True}}})
    playwright, browser, page = _page(tmp_path, html)
    try:
        assert authenticate(page, store) is None
        assert page.locator("h2").inner_text() == "My Information"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


CREATE_WITH_CHECKBOX = """<div data-automation-id=signInContent><h2>Create Account</h2>
  <input data-automation-id=email type=text><input data-automation-id=password type=password>
  <input data-automation-id=verifyPassword type=password>
  <p>By activating your account in Workday, you acknowledge and agree that you have read and understand Cloudforce's Candidate Privacy Notice.</p>
  <label><input data-automation-id=createAccountCheckbox type=checkbox>I acknowledge and agree to Cloudforce's Candidate Privacy Notice</label>
  <button data-automation-id=createAccountSubmitButton type=button onclick="
    if (!document.querySelector('[data-automation-id=createAccountCheckbox]').checked) {
      document.body.insertAdjacentHTML('beforeend', '<div role=alert>Error: Please check the box to continue</div>'); return; }
    window.created = true; document.body.innerHTML = '<h2>My Information</h2><button>Next</button>'">Create Account</button></div>"""


def test_privacy_notice_consent_box_counts_as_account_terms(tmp_path):
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123", "create_if_missing": True,
                                          "accept_account_terms": True}, "accounts": {}})
    playwright, browser, page = _page(tmp_path, CREATE_WITH_CHECKBOX)
    try:
        assert authenticate(page, store) is None
        assert page.evaluate("window.created") is True
        assert "Candidate Privacy Notice" in store.db.execute("SELECT text FROM terms").fetchone()[0]
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_privacy_notice_consent_without_opt_in_stops(tmp_path):
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123", "create_if_missing": True}, "accounts": {}})
    playwright, browser, page = _page(tmp_path, CREATE_WITH_CHECKBOX)
    try:
        assert authenticate(page, store)["status"] == "terms_review_required"
        assert page.evaluate("window.created") is None
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_create_rejected_before_any_request_releases_the_attempt(tmp_path):
    # Form-side validation error, nothing sent: creation may be retried later.
    html = """<input data-automation-id=email type=text><input data-automation-id=password type=password>
      <input data-automation-id=verifyPassword type=password>
      <button data-automation-id=createAccountSubmitButton type=button
        onclick="document.body.insertAdjacentHTML('beforeend', '<div role=alert>Error: Please fix the form</div>')">Create Account</button>"""
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123", "create_if_missing": True}, "accounts": {}})
    playwright, browser, page = _page(tmp_path, html)
    try:
        result = authenticate(page, store)
        assert result["status"] == "account_validation_failed"
        assert "Please fix the form" in result["missing"][0]
        assert store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_signin_clicks_the_overlay_on_the_submit_button_whatever_its_label(tmp_path):
    # QTX: the click_filter over the aria-hidden Sign In button is labelled "Submit".
    html = """<input data-automation-id=email type=text><input data-automation-id=password type=password>
      <div style="position:relative;width:200px;height:40px">
        <div data-automation-id=click_filter role=button aria-label=Submit tabindex=0 style="position:absolute;inset:0;z-index:2"
             onclick="window.signedIn=true; document.body.innerHTML='<h2>My Information</h2><button>Next</button>'"></div>
        <button data-automation-id=signInSubmitButton type=submit aria-hidden=true tabindex=-2 style="width:200px;height:40px">Sign In</button>
      </div>"""
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123"}, "accounts": {ORIGIN: {"exists": True}}})
    playwright, browser, page = _page(tmp_path, html)
    try:
        page.set_default_timeout(6000)
        assert authenticate(page, store) is None
        assert page.evaluate("window.signedIn") is True
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


REJECTING_SIGNIN = """<input data-automation-id=email type=text><input data-automation-id=password type=password>
  <button data-automation-id=signInSubmitButton type=button onclick="window.tries=(window.tries||0)+1;
    document.body.insertAdjacentHTML('beforeend','<div role=alert>You may have entered the wrong email address or password or your account might be locked.</div>')">Sign In</button>"""


def test_rejected_login_is_not_retried_until_the_password_changes(tmp_path):
    payload = {"default": {"email": "a@example.com", "password": "Old-123"}, "accounts": {ORIGIN: {"exists": True}}}
    store = _store(tmp_path, payload)
    playwright, browser, page = _page(tmp_path, REJECTING_SIGNIN)
    try:
        assert authenticate(page, store)["status"] == "account_rejected"
        page.reload()
        again = authenticate(page, store)
        assert again["status"] == "account_rejected" and "not retried" in again["missing"][0]
        assert page.evaluate("window.tries") is None, "no second wrong-password attempt"
        store.db.close()
        payload["default"]["password"] = "New-456"
        store = _store(tmp_path, payload)
        page.reload()
        assert authenticate(page, store)["status"] == "account_rejected"
        assert page.evaluate("window.tries") == 1, "a changed password is tried"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_rejected_login_right_after_creating_waits_for_email_verification(tmp_path):
    # Every such case on 2026-09-23 was an unverified new account, not a wrong password.
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Secret-123"}, "accounts": {}})
    origin, cred, _ = store.credentials(TARGET)
    store.claim(origin, cred["email"], "create"); store.finish(origin, cred["email"], "create", "created")
    playwright, browser, page = _page(tmp_path, REJECTING_SIGNIN)
    try:
        report = authenticate(page, store)
        assert report["status"] == "email_verification_required"
        assert "verification link" in report["missing"][0]
        # The password stays usable for the next try after verifying.
        import hashlib
        assert not store.was_rejected(origin, cred["email"], hashlib.sha256(b"Secret-123").hexdigest()[:12])
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


FALLBACK_ONLY_SIGNIN = """<input data-automation-id=email type=text><input data-automation-id=password type=password>
  <button data-automation-id=signInSubmitButton type=button onclick="window.tries=(window.tries||0)+1;
    if (document.querySelector('[data-automation-id=password]').value === 'Fallback-9!') { document.body.innerHTML='<h2>My Information</h2><button>Next</button>'; return; }
    document.querySelectorAll('[role=alert]').forEach(e => e.remove());
    document.body.insertAdjacentHTML('beforeend','<div role=alert>You may have entered the wrong email address or password or your account might be locked.</div>')">Sign In</button>"""


def test_rejected_login_tries_each_fallback_password_once_and_remembers_the_winner(tmp_path):
    payload = {"default": {"email": "a@example.com", "password": "Primary-1!", "fallback_passwords": ["Other-2!", "Fallback-9!"]},
               "accounts": {ORIGIN: {"exists": True}}}
    store = _store(tmp_path, payload)
    playwright, browser, page = _page(tmp_path, FALLBACK_ONLY_SIGNIN)
    try:
        assert authenticate(page, store) is None
        assert page.evaluate("window.tries") == 1, "each page load gets exactly one sign-in click"
        saved = json.loads((tmp_path / "accounts.json").read_text())
        assert saved["accounts"][ORIGIN]["password"] == "Fallback-9!", "the working password is remembered for this site"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_every_password_rejected_stops_without_repeating_any(tmp_path):
    payload = {"default": {"email": "a@example.com", "password": "Primary-1!", "fallback_passwords": ["Other-2!"]},
               "accounts": {ORIGIN: {"exists": True}}}
    store = _store(tmp_path, payload)
    playwright, browser, page = _page(tmp_path, FALLBACK_ONLY_SIGNIN)
    try:
        assert authenticate(page, store)["status"] == "account_rejected"
        page.reload()
        again = authenticate(page, store)
        assert again["status"] == "account_rejected" and "not retried" in again["missing"][0]
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_create_rejected_for_password_rules_retries_with_a_longer_fallback(tmp_path):
    # Vistara / P&G: passwords need 12+ characters; the default is shorter.
    html = """<input data-automation-id=email type=text><input data-automation-id=password type=password>
      <input data-automation-id=verifyPassword type=password>
      <button data-automation-id=createAccountSubmitButton type=button onclick="
        const pw = document.querySelector('[data-automation-id=password]').value;
        if (pw.length < 12) { document.querySelectorAll('[role=alert]').forEach(e => e.remove());
          document.body.insertAdjacentHTML('beforeend', '<div role=alert>Error: Password must include a minimum of 12 characters</div>'); return; }
        fetch('/register', {method: 'POST'}).then(() => { document.body.innerHTML = '<h2>My Information</h2><button>Next</button>'; });">Create Account</button>"""
    store = _store(tmp_path, {"default": {"email": "a@example.com", "password": "Short-1!", "create_if_missing": True,
                                          "fallback_passwords": ["Much-Longer-Pass-9!"]}, "accounts": {}})
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", lambda route: route.fulfill(status=200, body="{}") if route.request.method == "POST"
               else route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(TARGET)
    try:
        assert authenticate(page, store) is None
        saved = json.loads((tmp_path / "accounts.json").read_text())
        assert saved["accounts"][ORIGIN]["password"] == "Much-Longer-Pass-9!"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()
