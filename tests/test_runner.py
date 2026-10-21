import pytest
from playwright.sync_api import sync_playwright

from job_agent.runner import prepare, required_transcript


def _page(tmp_path, html):
    fixture = tmp_path / "fixture.html"
    fixture.write_text(html)
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(fixture.as_uri())
    return playwright, browser, page


def test_prepare_multistep_fills_alias_select_checkbox_file_and_never_submits(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"fixture resume")
    html = """<!doctype html><form onsubmit="localStorage.setItem('submitted','yes');return false">
      <label>Given name *<input name="first" required></label>
      <label>Country<select name="country" required><option>Choose</option><option>Canada</option></select></label>
      <label>Consent<input name="consent" type="checkbox" required></label>
      <label>Resume<input name="resume" type="file" required></label>
      <button type='button' id='next' onclick='document.body.innerHTML="<button type=button>Submit application</button>"'>Continue</button>
    </form>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {
            "answers": {
                "First name": {"value": "Alex", "aliases": ["Given name"]},
                "Country": "Canada", "Consent": True,
            },
            "documents": {"Resume": str(resume)},
        })
        assert report["status"] == "ready_for_review"
        assert len(report["history"]) == 2
        assert report["missing"] == []
        assert page.evaluate("Object.assign({}, localStorage)") == {}
        assert page.locator("input[type=file]").count() == 0
    finally:
        browser.close()
        playwright.stop()


def test_prepare_stops_on_unknown_required_checkbox(tmp_path):
    playwright, browser, page = _page(tmp_path, """<label>Terms<input type=checkbox required></label>
        <button type=button>Continue</button>""")
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "needs_input"
        assert "Terms" in report["missing"]
    finally:
        browser.close()
        playwright.stop()


def test_prepare_rejects_select_answer_not_in_options(tmp_path):
    playwright, browser, page = _page(tmp_path, """<label>Country<select required><option>Canada</option></select></label>
        <button type=button>Continue</button>""")
    try:
        report = prepare(page, {"answers": {"Country": "United States"}})
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Country (answer not an available option)"]
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.parametrize("html", [
    '<label>Official transcript *<input type=file required></label>',
    '<div data-automation-id="formField-transcript"><h3>Official Transcript</h3><input type=file required></div>',
])
def test_required_transcript_is_detected_from_its_label_or_heading(tmp_path, html):
    playwright, browser, page = _page(tmp_path, html)
    try:
        assert required_transcript(page) is True
    finally:
        browser.close()
        playwright.stop()


def test_optional_transcript_upload_is_not_required(tmp_path):
    playwright, browser, page = _page(tmp_path, '<label>Transcript<input type=file></label>')
    try:
        assert required_transcript(page) is False
    finally:
        browser.close()
        playwright.stop()


def test_optional_transcript_elsewhere_does_not_make_required_resume_a_transcript(tmp_path):
    html = '''<section><label>Resume *<input type=file required></label></section>
      <p>Transcript upload is optional.</p><label>Transcript<input type=file></label>'''
    playwright, browser, page = _page(tmp_path, html)
    try:
        assert required_transcript(page) is False
    finally:
        browser.close()
        playwright.stop()


def test_skip_required_transcript_returns_before_filling_application_fields(tmp_path):
    html = '''<label>First name<input name=first></label>
      <label>Official transcript *<input type=file required></label>
      <button type=button>Continue</button>'''
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {
            "answers": {"First name": "Alex"},
            "targeting": {"skip_transcript_required": True},
        })
        assert report["status"] == "skipped_transcript"
        assert page.locator("input[name=first]").input_value() == ""
    finally:
        browser.close()
        playwright.stop()


def test_prepare_reports_no_progress_and_does_not_click_submit(tmp_path):
    playwright, browser, page = _page(tmp_path, """<button type=button onclick="">Continue</button>
        <button type=button onclick="localStorage.setItem('submitted','yes')">Submit application</button>""")
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "ready_for_review"
        assert page.evaluate("Object.assign({}, localStorage)") == {}
    finally:
        browser.close()
        playwright.stop()


def test_posting_apply_link_is_not_treated_as_application_submit(tmp_path):
    html = '<a href="/posting/apply" onclick="window.followed=true">Apply</a>'
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}}, submit=True)
        assert report["status"] == "needs_input"
        assert page.url.endswith("fixture.html")
        assert page.evaluate("window.followed") is None
    finally:
        browser.close()
        playwright.stop()


def test_prepare_enforces_step_limit_on_repeating_unknown_platform_fixture(tmp_path):
    html = """<div id=state>step 0</div>
        <button type=button onclick="state.textContent='step '+(++window.n)">Continue</button>
        <script>window.n=0</script>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}}, max_steps=2)
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Step limit reached"]
        assert len(report["history"]) == 2
    finally:
        browser.close()
        playwright.stop()


def test_prepare_pause_callback_stops_before_filling_or_navigation(tmp_path):
    playwright, browser, page = _page(tmp_path, """<label>First name<input name=first></label>
        <button type=button>Continue</button>""")
    calls = []
    try:
        report = prepare(page, {"answers": {"First name": "Alex"}}, should_stop=lambda: calls.append(True) or True)
        assert report["status"] == "paused"
        assert page.locator("input").input_value() == ""
        assert calls
    finally:
        browser.close()
        playwright.stop()


def test_prepare_emits_events_for_fill_and_advance(tmp_path):
    playwright, browser, page = _page(tmp_path, """<label>First name<input name=first></label>
        <button type=button onclick="document.body.innerHTML='<button type=button>Submit application</button>'">Continue</button>""")
    events = []
    try:
        report = prepare(page, {"answers": {"First name": "Alex"}}, on_event=events.append)
        assert report["status"] == "ready_for_review"
        assert events == ["Step 1: processed 1 saved answers", "Advancing to the next page",
                          "Step 2: processed 0 saved answers"]
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.parametrize("origin", [
    "https://jobs.ashbyhq.com/example/123",
    "https://boards.greenhouse.io/example/jobs/123",
    "https://jobs.unknown.example/example/123",
])
def test_prepare_native_form_is_platform_agnostic(origin):
    html = """<label>First name<input name=first></label>
      <button type=button onclick="document.body.innerHTML='<button type=button>Submit application</button>'">Continue</button>"""
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(origin)
    try:
        report = prepare(page, {"answers": {"First name": "Alex"}})
        assert report["status"] == "ready_for_review"
    finally:
        browser.close()
        playwright.stop()


def test_prepare_waits_for_progressively_rendered_form_before_navigating(tmp_path):
    # Workday shows the step heading and footer first and streams fields in afterwards.
    html = """<!doctype html><h2>My Information</h2><div id=form></div>
      <button type=button id=next onclick="
        const first = document.querySelector('[name=first]')?.value || '';
        const city = document.querySelector('[name=city]')?.value || '';
        document.body.innerHTML = '<h2>Review</h2><p id=saved>' + first + '|' + city + '</p><button type=button>Submit</button>';
      ">Save and Continue</button>
      <script>
        setTimeout(() => document.getElementById('form').insertAdjacentHTML('beforeend', '<label>First Name *<input name=first aria-required=true></label>'), 400);
        setTimeout(() => document.getElementById('form').insertAdjacentHTML('beforeend', '<label>City<input name=city></label>'), 900);
      </script>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {"First Name": "Alex", "City": "Springfield"}})
        assert report["status"] == "ready_for_review"
        assert page.locator("#saved").inner_text() == "Alex|Springfield"
    finally:
        browser.close()
        playwright.stop()


def test_prepare_survives_fields_inserted_mid_pass(tmp_path):
    # Checking a box reveals a conditional field ahead of the remaining controls.
    html = """<!doctype html><label>Consent<input name=consent type=checkbox required
        onchange="this.closest('label').insertAdjacentHTML('afterend', '<label>Extra<input name=extra></label>')"></label>
      <label>First Name *<input name=first required></label>
      <label>Last Name *<input name=last required></label>
      <button type=button onclick="document.body.innerHTML='<h2>Review</h2><button type=button>Submit</button>'">Continue</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {"Consent": True, "First Name": "Alex", "Last Name": "Lee"}}, max_steps=1)
        assert report["history"][0]["filled"] == 3, "every original control must be visited"
        assert report["missing"] == ["Step limit reached"]
    finally:
        browser.close()
        playwright.stop()


DROP_ZONE = """<h4>Resume/CV</h4>
  <div data-automation-id="formField-"><label id="label32"><span>Upload a file (5MB max)<abbr aria-hidden="true">*</abbr></span></label>
    <div aria-labelledby="label32" data-automation-id="attachments-FileUpload"><div data-automation-id="file-upload-drop-zone">
      <div>Drop files here</div><button data-automation-id="select-files" type="button">Select files</button></div>
      <input data-automation-id="file-upload-input-ref" multiple type="file" style="display:none"
        onchange="this.closest('[data-automation-id=attachments-FileUpload]').insertAdjacentHTML('beforeend', '<div data-automation-id=file-upload-item>' + this.files[0].name + ' Successfully Uploaded</div>')">
    </div></div>"""


def test_hidden_dropzone_file_input_receives_resume_by_section_heading(tmp_path):
    resume = tmp_path / "Alex_Rivera_Resume.pdf"
    resume.write_bytes(b"fixture resume")
    playwright, browser, page = _page(tmp_path, "<!doctype html>" + DROP_ZONE)
    try:
        report = prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}}, max_steps=1)
        assert report["missing"] == ["No unique supported navigation control"]
        assert report["history"][0]["filled"] == 1
        assert page.locator("input[type=file]").evaluate("e => e.files.length") == 1
        assert page.locator("[data-automation-id=file-upload-item]").count() == 1
        # A second pass recognizes the uploaded file and does not attach it again.
        report = prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}}, max_steps=1)
        assert report["history"][0]["filled"] == 0
        assert page.locator("[data-automation-id=file-upload-item]").count() == 1
    finally:
        browser.close()
        playwright.stop()


def test_required_hidden_file_input_without_document_is_reported(tmp_path):
    playwright, browser, page = _page(tmp_path, "<!doctype html>" + DROP_ZONE + "<button type=button>Save and Continue</button>")
    try:
        report = prepare(page, {"answers": {}, "documents": {}})
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Upload a file (5MB max) (no saved document for Resume/CV)"]
    finally:
        browser.close()
        playwright.stop()


def test_validation_errors_shown_after_navigation_are_reported(tmp_path):
    html = """<!doctype html><h2>My Experience</h2><div id=alerts></div>
      <button type=button onclick="document.getElementById('alerts').innerHTML =
        '<p data-automation-id=inputAlert>Error: The field Upload a file (5MB max) is required and must have a value.</p>'">Save and Continue</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Site error: The field Upload a file (5MB max) is required and must have a value."]
        assert len(report["history"]) == 1
    finally:
        browser.close()
        playwright.stop()


def test_prepare_reloads_transient_site_error_page_once(tmp_path):
    served = []
    form = """<h2>My Information</h2><label>First Name *<input name=first required></label>
      <button type=button onclick="document.body.innerHTML='<h2>Review</h2><button type=button>Submit</button>'">Save and Continue</button>"""
    error = "<h3>Something went wrong</h3><p>Please refresh the page and then try again.</p><p>Error Code: VPS|1234</p>"
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.route("**/*", lambda route: (served.append(1), route.fulfill(status=200, content_type="text/html",
                                                                      body=error if len(served) == 1 else form)))
    page.goto("https://fixture.example/apply")
    try:
        report = prepare(page, {"answers": {"First Name": "Alex"}})
        assert report["status"] == "ready_for_review"
        assert len(served) == 2
        assert report["history"][0].get("reloaded") is True
    finally:
        browser.close()
        playwright.stop()


def test_prepare_waits_while_loading_placeholders_are_present(tmp_path):
    # Skeleton screens keep a visually hidden "Loading" leaf for screen readers.
    html = """<!doctype html><h2>My Information</h2><div id=form><div role=group><div style="display:none">Loading</div></div></div>
      <button type=button onclick="
        const first = document.querySelector('[name=first]')?.value || '';
        document.body.innerHTML = '<h2>Review</h2><p id=saved>' + first + '</p><button type=button>Submit</button>';
      ">Save and Continue</button>
      <script>setTimeout(() => document.getElementById('form').innerHTML = '<label>First Name *<input name=first aria-required=true></label>', 1800);</script>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {"First Name": "Alex"}})
        assert report["status"] == "ready_for_review"
        assert page.locator("#saved").inner_text() == "Alex"
    finally:
        browser.close()
        playwright.stop()


def test_success_live_region_is_not_a_site_error(tmp_path):
    # First click only surfaces the upload's success live region; the page stays put.
    html = """<!doctype html><h2>My Experience</h2><div role=alert id=live></div>
      <button type=button onclick="
        if (!document.getElementById('live').textContent) { document.getElementById('live').textContent = 'resume.pdf successfully uploaded'; return; }
        document.body.innerHTML = '<h2>Review</h2><button type=button>Submit</button>'">Save and Continue</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "ready_for_review"
        assert len(report["history"]) == 3  # alert step, navigation step, review observation
    finally:
        browser.close()
        playwright.stop()


def test_upload_waits_for_site_confirmation_before_navigating(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"fixture resume")
    html = """<!doctype html><h2>My Experience</h2>
      <label>Resume<input name=resume type=file required
        onchange="setTimeout(() => this.insertAdjacentHTML('afterend', '<div data-automation-id=file-upload-item>' + this.files[0].name + ' Successfully Uploaded</div>'), 900)"></label>
      <button type=button onclick="
        const done = !!document.querySelector('[data-automation-id=file-upload-item]');
        document.body.innerHTML = '<h2>Review</h2><p id=saved>' + done + '</p><button type=button>Submit</button>'">Save and Continue</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}})
        assert report["status"] == "ready_for_review"
        assert page.locator("#saved").inner_text() == "true"
    finally:
        browser.close()
        playwright.stop()


def test_prepare_waits_for_an_empty_page_to_render_its_fields(tmp_path):
    # After a navigation the page can be quiet but empty for a few seconds with no
    # loading markers at all; that is not a page to act on yet.
    html = """<!doctype html><h2>My Information</h2><div id=form></div>
      <script>setTimeout(() => document.getElementById('form').innerHTML =
        '<label>First Name *<input name=first aria-required=true></label>' +
        '<button type=button onclick="document.body.innerHTML=\\'<h2>Review</h2><p id=saved>\\' + document.querySelector(\\'[name=first]\\').value + \\'</p><button type=button>Submit</button>\\'">Save and Continue</button>', 3000);</script>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {"First Name": "Alex"}})
        assert report["status"] == "ready_for_review"
        assert page.locator("#saved").inner_text() == "Alex"
    finally:
        browser.close()
        playwright.stop()


def test_resume_is_not_attached_to_a_cover_letter_drop_zone(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"fixture resume")
    cover = DROP_ZONE.replace("Resume/CV", "Cover Letter").replace("label32", "label33").replace("formField-\"", "formField-cover\"")
    playwright, browser, page = _page(tmp_path, "<!doctype html>" + DROP_ZONE + cover)
    try:
        report = prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}}, max_steps=1)
        assert report["history"][0]["filled"] == 1
        assert "Upload a file (5MB max) (no saved document for Cover Letter)" in report["missing"]
        counts = page.locator("input[type=file]").evaluate_all("es => es.map(e => e.files.length)")
        assert counts == [1, 0]
    finally:
        browser.close()
        playwright.stop()


def test_fill_that_rerenders_later_controls_does_not_crash_or_skip_them(tmp_path):
    # Choosing a country re-renders the address block; the old handles detach.
    html = """<!doctype html><label>Country<select name=country required><option value="">Choose</option><option>Canada</option></select></label>
      <div id=addr><label>City<input name=city required></label><label>Postal Code<input name=postal required></label></div>
      <script>document.querySelector('select').onchange = () => { const a = document.getElementById('addr'); a.innerHTML = a.innerHTML; };</script>
      <button type=button onclick="document.body.innerHTML='<h2>Review</h2><p id=saved>' + document.querySelector('[name=city]').value + '|' + document.querySelector('[name=postal]').value + '</p><button type=button>Submit</button>'">Continue</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {"Country": "Canada", "City": "Springfield", "Postal Code": "62701"}})
        assert report["status"] == "ready_for_review"
        assert page.locator("#saved").inner_text() == "Springfield|62701"
    finally:
        browser.close()
        playwright.stop()


def test_pattern_answers_never_fill_free_text_controls(tmp_path):
    html = """<!doctype html><label>If previously employed, enter your employee ID<input name=eid></label>
      <label>Were you previously employed here?<select name=prev required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>
      <button type=button>Continue</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        prepare(page, {"answers": {}, "patterns": [{"match": "previously employed", "value": "No"}]}, max_steps=1)
        assert page.locator("[name=eid]").input_value() == ""
        assert page.locator("[name=prev]").input_value() == "No"
    finally:
        browser.close()
        playwright.stop()


def test_settle_gives_up_on_a_permanent_hidden_loading_node(tmp_path):
    import time
    from job_agent.runner import settle
    html = """<!doctype html><div style="display:none"><span>Loading</span></div><label>First Name<input name=first></label>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        started = time.monotonic()
        settle(page)
        assert time.monotonic() - started < 25, "a permanent loading marker must not hold settle for the full timeout"
    finally:
        browser.close()
        playwright.stop()


REVIEW_PAGE = """<!doctype html><h2>Review</h2><p>Check your answers</p>
  <button type=button id=submit onclick="localStorage.setItem('submitted','yes');
    document.body.innerHTML='<h2>Thank you</h2><p>Your application has been submitted.</p>'">Submit</button>"""


def test_prepare_submits_only_when_explicitly_enabled(tmp_path):
    playwright, browser, page = _page(tmp_path, REVIEW_PAGE)
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "ready_for_review"
        assert page.evaluate("Object.assign({}, localStorage)") == {}
        report = prepare(page, {"answers": {}}, submit=True)
        assert report["status"] == "submitted"
        assert "submitted" in report["confirmation"].lower()
        assert page.evaluate("localStorage.getItem('submitted')") == "yes"
    finally:
        browser.close()
        playwright.stop()


def test_submit_is_reported_uncertain_when_the_site_does_not_acknowledge(tmp_path):
    html = """<!doctype html><h2>Review</h2><button type=button onclick="localStorage.setItem('submitted','yes')">Submit</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}}, submit=True)
        assert report["status"] == "submit_uncertain"
        assert page.evaluate("localStorage.getItem('submitted')") == "yes"
    finally:
        browser.close()
        playwright.stop()


def test_submit_never_fires_with_missing_answers_or_site_errors(tmp_path):
    html = """<!doctype html><h2>Review</h2><label>Terms *<input type=checkbox required></label>
      <button type=button onclick="localStorage.setItem('submitted','yes')">Submit</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}}, submit=True)
        assert report["status"] == "needs_input"
        assert page.evaluate("Object.assign({}, localStorage)") == {}
    finally:
        browser.close()
        playwright.stop()


def test_generic_upload_slot_naming_another_document_never_gets_the_resume(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"fixture resume")
    # The slot's own label names a transcript; no heading precedes it.
    html = """<!doctype html><div data-automation-id="formField-t"><label id=l1><span>Upload a file (Unofficial Transcript)<abbr>*</abbr></span></label>
      <div aria-labelledby="l1" data-automation-id="attachments-FileUpload"><input type=file style="display:none"></div></div>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}}, max_steps=1)
        assert page.locator("input[type=file]").evaluate("e => e.files.length") == 0
        assert any("no saved document" in item for item in report["missing"])
    finally:
        browser.close()
        playwright.stop()


def test_combined_resume_cover_letter_slot_gets_the_resume(tmp_path):
    resume = tmp_path / "Alex_Rivera_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    playwright, browser, page = _page(tmp_path, """<h3>Resume/Cover Letter</h3>
        <div data-automation-id=formField-resume><label for=f>Upload a file (5MB max)<abbr>*</abbr></label>
        <input type=file id=f aria-required=true style="display:none"></div>
        <button type=button onclick="document.body.innerHTML='<button type=button>Submit</button>'">Continue</button>""")
    try:
        report = prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_generic_upload_slot_under_a_note_heading_gets_the_resume(tmp_path):
    # Aerospace Corp: the only heading is a file-name warning, not a document type.
    resume = tmp_path / "Alex_Rivera_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    playwright, browser, page = _page(tmp_path, """<h4>Please note special characters on your file name will cause the system to error out.</h4>
        <div data-automation-id=formField-file><label for=f>Upload a file (5MB max)<abbr>*</abbr></label>
        <input type=file id=f aria-required=true style="display:none"></div>
        <button type=button onclick="document.body.innerHTML='<button type=button>Submit</button>'">Continue</button>""")
    try:
        report = prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_open_popup_over_the_next_button_is_closed_before_navigating(tmp_path):
    playwright, browser, page = _page(tmp_path, """<h2>Questions</h2>
        <div id=pop data-behavior-click-outside-close=topmost style="position:fixed;bottom:0;left:0;right:0;height:200px;background:#fff">picker</div>
        <script>document.addEventListener('mousedown', e => { const p = document.getElementById('pop'); if (p && !p.contains(e.target)) p.remove(); }, true);</script>
        <div style="position:fixed;bottom:20px;left:20px"><button type=button onclick="document.body.innerHTML='<button type=button>Submit</button>'">Save and Continue</button></div>""")
    try:
        page.set_default_timeout(6000)
        assert prepare(page, {"answers": {}})["status"] == "ready_for_review"
    finally:
        browser.close()
        playwright.stop()


def test_search_results_dialog_with_done_button_is_closed(tmp_path):
    # One tenant: the Skills prompt is a role=dialog with a Done button and no click-outside marker.
    playwright, browser, page = _page(tmp_path, """<h2>My Experience</h2>
        <div id=dlg role=dialog style="position:fixed;inset:0;background:#fff">
          <p data-automation-id=promptOption>Python Frameworks</p><button type=button onclick="dlg.remove()">Done</button></div>
        <button type=button onclick="document.body.innerHTML='<button type=button>Submit</button>'">Save and Continue</button>""")
    try:
        page.set_default_timeout(6000)
        assert prepare(page, {"answers": {}})["status"] == "ready_for_review"
    finally:
        browser.close()
        playwright.stop()


def test_guest_apply_redirect_with_an_application_id_counts_as_submitted(tmp_path):
    # Synthetic guest flow: Submit shows no message, only a new URL.
    html = """<h2>Review</h2><button type=button onclick="history.pushState({}, '', '?Job_Application_ID=00000000000000000000000000000001');
      document.body.innerHTML = '<h2>Search Jobs</h2>'">Submit</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}}, submit=True)
        assert report["status"] == "submitted", report
        assert report["confirmation"] == "Workday Job_Application_ID=00000000000000000000000000000001"
    finally:
        browser.close()
        playwright.stop()


def test_custom_greenhouse_confirmation_page_counts_as_submitted(tmp_path):
    # Huddlo: /jobs/N/confirmation with the company's own copy and no "thank you".
    (tmp_path / "confirmation.html").write_text(
        "<p>And the crowd goes wild! We can't wait to look over your application.</p><p>Track your application</p>")
    html = """<h2>Review</h2><button type=button onclick="location.href = 'confirmation.html'">Submit</button>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}}, submit=True)
        assert report["status"] == "submitted", report
    finally:
        browser.close()
        playwright.stop()


def test_apply_button_on_a_posting_page_is_not_a_review_page(tmp_path):
    # ByteDance: the job posting's "Apply" button is not the form's submit.
    playwright, browser, page = _page(tmp_path, "<h1>PM Intern</h1><p>About the role</p><button type=button>Apply</button>")
    try:
        report = prepare(page, {"answers": {}}, max_steps=1)
        assert report["status"] != "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_a_signature_date_box_gets_today_in_its_own_format():
    import datetime
    from job_agent.runner import signature_date
    day = datetime.date(2026, 9, 27)
    assert signature_date("Date", "MM/DD/YYYY", today=day) == "09/27/2026"
    assert signature_date("Date", "", "date", today=day) == "2026-09-27"
    assert signature_date("Start Date", "MM/DD/YYYY", today=day) is None


def test_workday_save_and_continue_disabled_while_saving_is_waited_for(tmp_path):
    # NVIDIA Workday (2026-10-12): after "Save and Continue" on My Experience the step stays
    # on screen for seconds with the footer button disabled while Workday saves; the next
    # step then renders its own footer button hidden and disabled before enabling it.
    # A stale page with a disabled forward button is a save in flight, not a page without
    # navigation. Fictional values only.
    html = """<!doctype html><div id=progress>current step 2 of 3 My Experience</div>
      <main id=form><h2>My Experience</h2><label>Portfolio<input name=site value="https://example.test"></label></main>
      <footer><button type=button data-automation-id=pageFooterNextButton id=next>Save and Continue</button></footer>
      <script>
      document.getElementById('next').onclick = function () {
        this.disabled = true;
        document.getElementById('progress').textContent = 'current step 2 of 3 My Experience (saving)';
        setTimeout(() => {
          document.getElementById('progress').textContent = 'current step 3 of 3 Review';
          document.getElementById('form').innerHTML = '<h2>Review</h2><p>Portfolio https://example.test</p>';
          const b = document.getElementById('next');
          b.style.display = 'none';
          setTimeout(() => {
            b.replaceWith(Object.assign(document.createElement('button'),
              {type: 'button', textContent: 'Submit', onclick: () => localStorage.setItem('submitted', 'yes')}));
          }, 800);
        }, 6000);
      };
      </script>"""
    playwright, browser, page = _page(tmp_path, html)
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "ready_for_review", report
        assert page.evaluate("Object.assign({}, localStorage)") == {}
    finally:
        browser.close()
        playwright.stop()
