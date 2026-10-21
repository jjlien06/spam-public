import pytest
from job_agent.workday import account_button, create_account, enter_application
from job_agent.queue import platform
from playwright.sync_api import sync_playwright


WORKDAY_URL = "https://fixture.myworkdayjobs.com/External/job/R10001"


def _routed_page(initial):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()

    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=initial))
    page.goto(WORKDAY_URL, wait_until="domcontentloaded", timeout=10000)
    return playwright, browser, page


def test_workday_apply_manually_reaches_account_gate_without_credentials():
    initial = """<a data-automation-id="adventureButton" href="/job/R10001/apply">Apply</a>
      <script>
        document.querySelector('a').onclick = event => {
          event.preventDefault();
          document.body.innerHTML = '<button id="manual" type="button">Apply Manually</button>';
          document.getElementById('manual').onclick = () => {
            document.body.innerHTML = '<input data-automation-id="email"><input type="password">';
          };
        };
      </script>
    """
    playwright, browser, page = _routed_page(initial)
    try:
        result = enter_application(page)
        assert result["status"] == "account_required"
        assert result["history"] == [{"action": "opened_application"}]
        assert "no credentials entered" in result["note"].lower()
        assert page.locator("input[type=password]").count() == 1
    finally:
        browser.close()
        playwright.stop()


def test_workday_manual_modal_wins_over_visible_entry_link():
    initial = """<a data-automation-id="adventureButton" href="/job/R10001/apply">Apply</a>
      <div role="dialog"><div id="manual">Apply Manually</div></div>
      <script>
        document.getElementById('manual').onclick = () => {
          document.body.innerHTML = '<input data-automation-id="email"><input type="password">';
        };
      </script>
    """
    playwright, browser, page = _routed_page(initial)
    try:
        result = enter_application(page)
        assert result["status"] == "account_required"
        assert result["history"] == []
        assert page.locator("input[type=password]").count() == 1
    finally:
        browser.close()
        playwright.stop()


def test_workday_rejects_cross_origin_apply_link_without_clicking():
    initial = '<a data-automation-id="adventureButton" href="https://evil.example/apply">Apply</a>'
    playwright, browser, page = _routed_page(initial)
    try:
        result = enter_application(page)
        assert result["status"] == "needs_input"
        assert result["missing"] == ["Unrecognized Workday application link"]
        assert page.url == WORKDAY_URL
    finally:
        browser.close()
        playwright.stop()


def test_unknown_host_is_not_treated_as_workday():
    assert platform("https://jobs.unknown.example/R10001") == "generic"
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        page.goto("data:text/html,<button>Apply Manually</button>")
        assert enter_application(page) is None
    finally:
        browser.close()
        playwright.stop()


def test_embedded_apply_for_position_clicks_nested_leaf_and_reveals_form():
    playwright, browser, page = _routed_page("""<div id=wrapper role=button aria-label='Apply for this position'>
      <button id=apply type=button>Apply for this position</button></div>
      <script>
        wrapper.onclick = () => window.wrapperClicked = true;
        apply.onclick = event => { event.stopPropagation(); window.applyClicked = true;
          document.body.insertAdjacentHTML('beforeend', '<input aria-label=ApplicantName>'); };
      </script>""")
    try:
        page.goto("https://jobs.example.test/posting")
        assert enter_application(page) is None
        assert page.locator("input[aria-label=ApplicantName]").is_visible()
        assert page.evaluate("window.applyClicked") is True
        assert page.evaluate("window.wrapperClicked") is None
    finally:
        browser.close()
        playwright.stop()


def test_embedded_apply_submit_button_is_not_clicked():
    playwright, browser, page = _routed_page("""<form onsubmit='window.submitted=true;return false'>
      <button type=submit>Apply for this position</button></form>""")
    try:
        page.goto("https://jobs.example.test/posting")
        result = enter_application(page)
        assert result["status"] == "needs_input"
        assert result["missing"] == ["Application entry could submit a form"]
        assert page.evaluate("window.submitted") is None
    finally:
        browser.close()
        playwright.stop()


def test_generic_posting_follows_one_deduplicated_same_job_apply_destination():
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    def route(request):
        path = request.request.url.split("jobs.example.test", 1)[1]
        body = ('<label>Applicant name<input></label>' if path == '/posting/apply' else
                '<a href="/posting/apply">Apply</a><a href="/posting/apply">Apply</a>')
        request.fulfill(status=200, content_type="text/html", body=body)
    page.route("**/*", route)
    try:
        page.goto("https://jobs.example.test/posting")
        assert enter_application(page) is None
        assert page.url == "https://jobs.example.test/posting/apply"
        assert page.locator("input").count() == 1
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.parametrize("href", ["https://outside.example/apply", "/different-job/apply"])
def test_generic_posting_does_not_follow_cross_origin_or_other_job_apply_links(href):
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html",
        body=f'<a href="{href}">Apply</a>'))
    try:
        page.goto("https://jobs.example.test/posting")
        current = page.url
        assert enter_application(page) is None
        assert page.url == current
    finally:
        browser.close()
        playwright.stop()


def test_cloudflare_security_check_is_reported_without_entering_application():
    initial = """<p>Please complete a security check</p>
      <iframe src='https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b'></iframe>"""
    playwright, browser, page = _routed_page(initial)
    try:
        result = enter_application(page)
        assert result == {
            "status": "captcha_required",
            "missing": ["Complete the visible security check, then Run / Resume"],
            "note": "Application form has not been reached",
        }
    finally:
        browser.close()
        playwright.stop()


class FakeVault:
    def __init__(self, existing=None):
        self.values = {} if existing is None else dict(existing)
        self.events = []

    def get(self, origin, email):
        self.events.append(("get", origin, email))
        return self.values.get((origin, email))

    @staticmethod
    def generate():
        return "Generated-Test-Password-123!"

    def set(self, origin, email, password):
        self.events.append(("set", origin, email, password))
        self.values[(origin, email)] = password


def _account_page():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    html = """<form><input data-automation-id=email type=email required>
      <input data-automation-id=password type=password required minlength=8>
      <input data-automation-id=verifyPassword type=password required minlength=8>
      <button data-automation-id=createAccountSubmitButton type=button> Create </button></form>"""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto("https://fixture.wd5.myworkdayjobs.com/External/job/R10001/apply")
    return playwright, browser, page


def test_create_account_stores_before_single_create_click_and_reports_pending():
    playwright, browser, page = _account_page()
    vault = FakeVault()
    try:
        result = create_account(page, "candidate@example.com", vault)
        assert result["status"] == "account_reconciliation_required"
        assert "may or may not be required" in result["note"]
        assert vault.events[-1] == ("set", "https://fixture.wd5.myworkdayjobs.com", "candidate@example.com", "Generated-Test-Password-123!")
        assert page.locator('[data-automation-id="email"]').input_value() == "candidate@example.com"
        assert page.locator('[data-automation-id="password"]').input_value() == "Generated-Test-Password-123!"
    finally:
        browser.close()
        playwright.stop()


def test_create_account_does_not_repeat_existing_or_pending_secret():
    origin = "https://fixture.wd5.myworkdayjobs.com"
    vault = FakeVault({(origin, "candidate@example.com"): "already-stored"})
    playwright, browser, page = _account_page()
    try:
        result = create_account(page, "candidate@example.com", vault)
        assert result["status"] == "account_reconciliation_required"
        assert vault.events == [("get", origin, "candidate@example.com")]
    finally:
        browser.close()
        playwright.stop()


def test_create_account_rejects_wrong_origin_before_touching_vault():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<input data-automation-id=email>")
    vault = FakeVault()
    try:
        with pytest.raises(ValueError, match="only implemented"):
            create_account(page, "candidate@example.com", vault)
        assert vault.events == []
    finally:
        browser.close()
        playwright.stop()


def test_account_button_prefers_visible_semantic_overlay_over_hidden_submit():
    initial = """<input data-automation-id=createAccountSubmitButton type=submit aria-hidden=true>
      <div role=button data-automation-id=click_filter aria-label='Create Account'
        onclick='window.accountClicked=true'>Create Account</div>"""
    playwright, browser, page = _routed_page(initial)
    try:
        button = account_button(page, "createAccountSubmitButton", "Create Account")
        assert button.count() == 1
        assert button.get_attribute("data-automation-id") == "click_filter"
        button.click()
        assert page.evaluate("window.accountClicked === true") is True
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.parametrize("through_entry", [False, True])
def test_authenticated_workday_continues_to_review_without_submission(through_entry):
    from job_agent.runner import prepare
    form = """<h2>My Information</h2><label>First name<input required></label>
      <button type=button onclick="document.body.innerHTML='<h2>Review</h2><button onclick=window.submitted=true>Submit application</button>'">Save and continue</button>"""
    if through_entry:
        initial = "<a data-automation-id=adventureButton href=/job/R10001/apply>Apply</a>"
    else:
        initial = form
    playwright, browser, page = _routed_page(initial)
    try:
        if through_entry:
            page.locator('a').evaluate("(e, html) => e.onclick = event => { event.preventDefault(); setTimeout(() => document.body.innerHTML = html, 100); }", form)
        assert enter_application(page) is None
        report = prepare(page, {"answers": {"First name": "Alex"}})
        assert report["status"] == "ready_for_review"
        assert page.evaluate("window.submitted === true") is False
    finally:
        browser.close()
        playwright.stop()


def test_account_gate_takes_precedence_over_application_sidebar():
    initial = """<h2>My Information</h2><button>Continue</button>
      <input type=password><button onclick=window.created=true>Create Account</button>"""
    playwright, browser, page = _routed_page(initial)
    try:
        assert enter_application(page)["status"] == "account_required"
        assert page.evaluate("window.created === true") is False
    finally:
        browser.close()
        playwright.stop()


def test_authenticated_application_email_field_is_not_account_gate():
    initial = """<h2>My Information</h2><label>Email<input data-automation-id="email" type="email"></label>
      <button type="button">Continue</button>"""
    playwright, browser, page = _routed_page(initial)
    try:
        assert enter_application(page) is None
    finally:
        browser.close()
        playwright.stop()


def test_open_application_reenters_from_posting_after_signing_in(tmp_path):
    """Continuing from the manual-apply page after sign-in trips Workday's
    "Something went wrong" page when a draft exists; re-entering from the
    posting with the authenticated session loads the draft instead."""
    from job_agent.account_store import AccountStore
    from job_agent.workday import open_application
    state = {"logged_in": False, "hits": []}
    posting = '<a data-automation-id="adventureButton" href="/External/job/R10001/apply">Apply</a>'
    chooser = '<button id=manual type=button onclick="location.href=\'/External/job/R10001/apply/applyManually\'">Apply Manually</button>'
    account = """<input data-automation-id=email><input data-automation-id=password type=password>
      <button data-automation-id=signInSubmitButton type=button onclick="fetch('/login').then(() => location.href = '/External/job/R10001/apply/applyManually')">Sign In</button>"""
    # Live behaviour: heading, footer and header widgets render first; the error arrives ~2s later.
    conflict = """<h2>My Information</h2><button aria-haspopup=listbox>Settings</button><button aria-haspopup=true>me</button>
      <input name=website><input name=x><button type=button>Save and Continue</button>
      <script>setTimeout(() => document.body.innerHTML = '<h3>Something went wrong</h3><p>Please refresh the page and then try again.</p>', 2000)</script>"""
    draft = '<h2>My Information</h2><div data-automation-id="formField-first"><label>First Name *<input name=first></label></div><button type=button>Save and Continue</button>'
    def handle(route):
        path = route.request.url.split("myworkdayjobs.com")[1]
        state["hits"].append(path)
        if path == "/login":
            state["logged_in"] = True
            return route.fulfill(status=200, body="ok")
        if path.endswith("/apply/applyManually"):
            body = conflict if state["logged_in"] else account
        elif path.endswith("/apply"):
            body = draft if state["logged_in"] else chooser
        else:
            body = posting
        route.fulfill(status=200, content_type="text/html", body=body)
    (tmp_path / "accounts.json").write_text('{"default": {"email": "a@example.com", "password": "secret"}, "accounts": {"https://fixture.myworkdayjobs.com": {"exists": true}}}')
    store = AccountStore(tmp_path / "accounts.json", tmp_path / "state")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", handle)
    events = []
    try:
        assert open_application(page, WORKDAY_URL, store, on_event=events.append) is None
        assert page.locator("input[name=first]").count() == 1, "draft form loaded"
        assert state["hits"][-2:] == ["/en-US/External/job/R10001", "/External/job/R10001/apply"]
        assert any("re-enter" in event.lower() for event in events)
        assert "Something went wrong" not in page.locator("body").inner_text()
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_signed_in_posting_offers_continue_application_link():
    # With a draft in progress, the posting replaces Apply with Continue Application.
    initial = """<a data-automation-id="continueButton" href="/job/R10001/apply">Continue Application</a>
      <script>document.querySelector('a').onclick = event => { event.preventDefault();
        document.body.innerHTML = '<h2>My Information</h2><div data-automation-id="formField-first"><input></div><button type=button>Save and Continue</button>'; };
      </script>"""
    playwright, browser, page = _routed_page(initial)
    try:
        assert enter_application(page) is None
        assert page.locator("h2").inner_text() == "My Information"
    finally:
        browser.close()
        playwright.stop()


def test_open_application_accepts_a_draft_resumed_at_review(tmp_path):
    from job_agent.account_store import AccountStore
    from job_agent.workday import open_application
    posting = '<a data-automation-id="continueButton" href="/External/job/R10001/apply">Continue Application</a>'
    review = '<h2>Review</h2><p>Summary of your application</p><button type=button>Submit</button>'
    def handle(route):
        body = review if route.request.url.endswith("/apply") else posting
        route.fulfill(status=200, content_type="text/html", body=body)
    (tmp_path / "accounts.json").write_text('{"default": {"email": "a@example.com", "password": "secret"}}')
    store = AccountStore(tmp_path / "accounts.json", tmp_path / "state")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", handle)
    try:
        assert open_application(page, WORKDAY_URL, store) is None
        assert page.locator("h2").inner_text() == "Review"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


ALREADY_APPLIED = """<h2>Summer 2027 Intern: Architecture/Engineering</h2>
  <p>You've already applied for this job.</p><a href="/userHome">View My Applications</a>"""


def test_already_applied_posting_is_a_terminal_state():
    playwright, browser, page = _routed_page(ALREADY_APPLIED)
    try:
        report = enter_application(page)
        assert report["status"] == "already_applied"
    finally:
        browser.close()
        playwright.stop()


def test_sign_in_that_lands_on_already_applied_is_not_uncertain(tmp_path):
    """Red Maple R-000001: after sign-in Workday shows "You've already applied"
    instead of the form. That is a finished job, not an uncertain account response."""
    from job_agent.account_store import AccountStore
    from job_agent.workday import open_application
    state = {"logged_in": False}
    account = """<input data-automation-id=email><input data-automation-id=password type=password>
      <button data-automation-id=signInSubmitButton type=button onclick="fetch('/login').then(() => location.reload())">Sign In</button>"""
    posting = '<a data-automation-id="adventureButton" href="/External/job/R10001/apply">Apply</a>'
    def handle(route):
        path = route.request.url.split("myworkdayjobs.com")[1]
        if path == "/login":
            state["logged_in"] = True
            return route.fulfill(status=200, body="ok")
        if state["logged_in"]:
            body = ALREADY_APPLIED
        else:
            body = account if path.endswith("/apply") else posting
        route.fulfill(status=200, content_type="text/html", body=body)
    (tmp_path / "accounts.json").write_text('{"default": {"email": "a@example.com", "password": "secret"}, "accounts": {"https://fixture.myworkdayjobs.com": {"exists": true}}}')
    store = AccountStore(tmp_path / "accounts.json", tmp_path / "state")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", handle)
    try:
        report = open_application(page, WORKDAY_URL, store)
        assert report["status"] == "already_applied"
        origin = "https://fixture.myworkdayjobs.com"
        assert store.db.execute("SELECT status FROM attempts WHERE origin=? AND operation='login'", (origin,)).fetchone()[0] == "authenticated"
    finally:
        browser.close()
        playwright.stop()
        store.db.close()


def test_signed_in_posting_for_submitted_job_is_already_applied():
    # Red Maple R-000001 posting once signed in: no Apply link, only View Application.
    posting = """<h2 data-automation-id="jobPostingHeader">Summer 2027 Intern: Architecture/Engineering</h2>
      <p>You applied for this job on September 22, 2026.</p>
      <a data-automation-id="viewButton" href="/userHome">View Application</a>"""
    playwright, browser, page = _routed_page(posting)
    try:
        assert enter_application(page)["status"] == "already_applied"
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.parametrize("text", ["The page you are looking for doesn't exist.",
                                  "This job posting is no longer available",
                                  "We are no longer accepting applications for this position.",
                                  "La page que vous recherchez n'existe pas."])
def test_closed_posting_is_reported_immediately(text):
    playwright, browser, page = _routed_page(f"<h2>CAREERS</h2><p>{text}</p><a href=/jobs>Search for Jobs</a>")
    try:
        assert enter_application(page)["status"] == "posting_closed"
    finally:
        browser.close()
        playwright.stop()


def test_workday_links_are_opened_in_english():
    from job_agent.workday import english
    # One company's list links "en-us" in lower case: replaced, never doubled into "/en-US/en-us/".
    assert english("https://example.wd1.myworkdayjobs.com/en-us/external/job/X_JR1") == "https://example.wd1.myworkdayjobs.com/en-US/external/job/X_JR1"
    base = "https://qtxhr.wd5.myworkdayjobs.com"
    assert english(base + "/rec_qtx_ext_gateway/job/US-RI/Intern_0187") == base + "/en-US/rec_qtx_ext_gateway/job/US-RI/Intern_0187"
    assert english(base + "/fr-CA/Private_Posting_No_TMP/job/X_1") == base + "/en-US/Private_Posting_No_TMP/job/X_1"
    assert english(base + "/en-US/site/job/X_1?q=1") == base + "/en-US/site/job/X_1?q=1"
    assert english("https://boards.greenhouse.io/x/jobs/1") == "https://boards.greenhouse.io/x/jobs/1"


def test_greenhouse_board_that_redirects_to_company_site_opens_embed_form():
    """Coinbase and Epic send job-boards.greenhouse.io/{board}/jobs/{id} to their own
    careers sites (one behind a bot challenge); Greenhouse's embed form still serves it."""
    from job_agent.workday import greenhouse_embed_url, open_application
    assert greenhouse_embed_url("https://job-boards.greenhouse.io/acme/jobs/8168315") == \
        "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=8168315"
    assert greenhouse_embed_url("https://boards.greenhouse.io/acme/jobs/12?gh_src=x") == \
        "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=12"
    assert greenhouse_embed_url("https://jobs.lever.co/acme/1") is None
    import http.server, threading
    class Challenge(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<h1>Just a moment...</h1>")
        def log_message(self, *args):
            pass
    site = http.server.HTTPServer(("127.0.0.1", 0), Challenge)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    form = '<form><label>First Name<input id=first_name></label><button type=submit>Submit application</button></form>'
    def handle(route):
        url = route.request.url
        if url.startswith("https://job-boards.greenhouse.io/acme/jobs/"):
            # Redirected requests bypass page.route, so the company site is a real local server.
            return route.fulfill(status=302, headers={"Location": f"http://127.0.0.1:{site.server_port}/careers/8168315?gh_jid=8168315"}, body="")
        route.fulfill(status=200, content_type="text/html", body=form)
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("https://job-boards.greenhouse.io/**", handle)
    try:
        assert open_application(page, "https://job-boards.greenhouse.io/acme/jobs/8168315") is None
        assert "/embed/job_app?for=acme&token=8168315" in page.url
        assert page.locator("#first_name").count() == 1
    finally:
        browser.close()
        playwright.stop()
        site.shutdown()


WORKDAY_UPLOADED = """<div data-automation-id="formField-" data-fkit-id="resumeAttachments--attachments">
  <label id="label46"><span>Upload a file (5MB max)<abbr aria-hidden="true">*</abbr></span></label>
  <div data-automation-id="attachments-FileUpload" aria-labelledby="label46">
    <div data-automation-id="file-upload-drop-zone"><button type="button" data-automation-id="select-files">Select files</button></div>
    <input data-automation-id="file-upload-input-ref" type="file" multiple="">
    <div id=items><div data-automation-id="file-upload-item">
      <div data-automation-id="file-upload-item-name">Resume.pdf</div><div>%s</div>
      <div data-automation-id="file-upload-successful">Successfully Uploaded!</div>
      <button type="button" data-automation-id="delete-file" aria-label="Delete Resume.pdf" title="Delete"
        onclick="this.closest('[data-automation-id=file-upload-item]').remove()"></button>
    </div></div>
  </div></div>
<script>document.querySelector('input[type=file]').onchange = e => document.getElementById('items').innerHTML =
  '<div data-automation-id="file-upload-item"><div data-automation-id="file-upload-item-name">' + e.target.files[0].name +
  '</div><div>' + (e.target.files[0].size / 1024).toFixed(2) + ' KB</div></div>';</script>"""


def test_workday_replaces_an_older_upload_with_the_same_file_name(tmp_path):
    """A synthetic upload has the same name but a different size; replace the stale file."""
    from job_agent.runner import upload_document
    resume = tmp_path / "Resume.pdf"
    resume.write_bytes(b"%PDF" + b"x" * 32299)  # 31.55 KB
    for shown, replaced in (("55.57 KB", True), ("31.55 KB", False)):
        playwright, browser, page = _routed_page(WORKDAY_UPLOADED % shown)
        try:
            filled, missing = upload_document(page.locator("input[type=file]").element_handle(), {"resume": resume})
            assert (filled, missing) == ((1, None) if replaced else (0, None))
            assert page.locator("[data-automation-id=file-upload-item]").count() == 1
            assert "31.55 KB" in page.locator("#items").inner_text()
        finally:
            browser.close()
            playwright.stop()
