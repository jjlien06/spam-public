"""Oracle Recruiting Cloud (Candidate Experience) sign-in, copied from live markup (an Oracle-hosted
bank careers site, 2026-09-30): an email box with a hidden honeypot, a terms checkbox whose label opens
an AGREE dialog, then a six-box emailed PIN and VERIFY before the application's sections."""
from playwright.sync_api import sync_playwright

from job_agent.oracle import apply_url, pin_from, sign_in
from job_agent.queue import platform

BASE = "https://abcd.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26010970"

EMAIL_PAGE = """<h1>Job application form</h1><p>Please provide email address</p>
<div class="input-row"><label for="primary-email-0">Email Address </label>
  <input type="email" name="primary-email" id="primary-email-0" class="input-row__control"></div>
<div class="input-row" style="position:absolute;left:-9999px"><label for="honey-pot-1">honeypot</label>
  <input type="text" name="honey-pot" id="honey-pot-1" class="input-row__control"></div>
<label class="input-row legal-disclaimer-container" for="legal-disclaimer-checkbox">
  <input type="checkbox" class="input-row__hidden-control" id="legal-disclaimer-checkbox" required style="opacity:0;position:absolute" onclick="return false">
  <span class="apply-flow-input-checkbox" id="legal-disclaimer-checkbox-label">
    <span class="apply-flow-input-checkbox__button" style="display:inline-block;width:12px;height:12px;border:1px solid"
      onclick="event.preventDefault(); if (window.buttonTicks) document.getElementById('legal-disclaimer-checkbox').checked = !document.getElementById('legal-disclaimer-checkbox').checked">&#9744;</span>
    <span class="apply-flow-input-checkbox__label">I agree with the <a id="legal-disclaimer-link" href="#"
      onclick="event.preventDefault(); event.stopPropagation(); document.getElementById('__oj_zorder_container').style.display='block'">terms and conditions</a></span>
  </span></label>
<div id="__oj_zorder_container" role="presentation" style="display:none"><h1 class="heading">Terms and Conditions</h1>
  <p>For information about the Careers Privacy Notice, please click here.</p>
  <button type="button" onclick="document.getElementById('legal-disclaimer-checkbox').checked = true;
    document.getElementById('__oj_zorder_container').style.display='none'">AGREE</button></div>
<button type="button" class="button apply-flow-pagination__button">Cancel</button>
<button type="submit" class="button apply-flow-pagination__button" onclick="
  if (!document.getElementById('legal-disclaimer-checkbox').checked || !document.getElementById('primary-email-0').value) return;
  window.honeypot = document.getElementById('honey-pot-1').value;
  document.body.innerHTML = PIN_PAGE">Next</button>
<script>const PIN_PAGE = `<h1>Confirm Your Identity</h1><p>The verification code was sent to this email address.</p>
  ${[1,2,3,4,5,6].map(i => `<input type="text" id="pin-code-${i}" aria-label="Enter verification code digit ${i} of six.">`).join('')}
  <button type="submit" onclick="window.pin = [1,2,3,4,5,6].map(i => document.getElementById('pin-code-' + i).value).join('');
    history.pushState({}, '', location.pathname + '#/apply/section/1'); document.body.innerHTML = '<h2>Resume and Cover Letter</h2><button>Next</button>'">VERIFY</button>
  <button type="button">Send New Code</button>`;</script>"""


def test_oracle_candidate_experience_urls_are_one_platform():
    assert platform(BASE) == "oracle"
    assert platform(BASE + "/apply/email") == "oracle"
    assert platform("https://higher.gs.com/roles/171548?type=students") == "oracle"
    assert platform("https://example.oraclecloud.com/analytics") == "generic"
    assert apply_url(BASE + "?utm_source=x") == BASE + "/apply/email"
    assert apply_url(BASE + "/apply/section/2") == BASE + "/apply/email"
    # Goldman's student roles apply on its Oracle "LateralHiring" site.
    assert apply_url("https://higher.gs.com/roles/171548?type=students") == \
        "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LateralHiring/job/171548/apply/email"


def test_pin_is_read_from_the_verification_email():
    assert pin_from("Please confirm your identity using the one-time passcode: 101332 This code will expire in 10 minutes.") == "101332"
    assert pin_from("Your verification code is 482913.") == "482913"
    assert pin_from("Call us at 800-555-1234 or reference job 171548") is None


import pytest


@pytest.mark.parametrize("button_ticks", [True, False], ids=["checkbox-button", "agree-dialog"])
def test_sign_in_agrees_to_terms_enters_the_emailed_pin_and_never_fills_the_honeypot(tmp_path, button_ticks):
    fixture = tmp_path / "email.html"
    fixture.write_text(f"<!doctype html><html><body><script>window.buttonTicks = {str(button_ticks).lower()}</script>" + EMAIL_PAGE + "</body></html>")
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_default_timeout(8000)
    asked = []
    try:
        page.goto(fixture.as_uri())
        report = sign_in(page, "alex@example.com", lambda since: asked.append(since) or "482913")
        assert report is None, report
        assert asked, "the code is fetched only after it was sent"
        assert page.evaluate("window.pin") == "482913"
        assert page.evaluate("window.honeypot") == ""
        assert page.locator("h2").inner_text() == "Resume and Cover Letter"
    finally:
        b.close(); p.stop()


def test_sign_in_without_a_code_stops_for_the_user(tmp_path):
    fixture = tmp_path / "email.html"
    fixture.write_text("<!doctype html><html><body>" + EMAIL_PAGE + "</body></html>")
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_default_timeout(8000)
    try:
        page.goto(fixture.as_uri())
        report = sign_in(page, "alex@example.com", lambda since: None, wait=1)
        assert report["status"] == "email_code_required"
    finally:
        b.close(); p.stop()


def test_oracle_cookie_modal_is_rejected_not_accepted(tmp_path):
    """American Express's Oracle site covers the email step with "Accept All" / "Reject All"."""
    from job_agent.runner import dismiss_cookie_overlays
    body = """<div class="cookie-consent-modal"><p>Our Use of Cookies. We use cookies on this Jobs website (strictly necessary
      cookies) and would also like functional and analytics cookies.</p>
      <div class="cookie-consent__actions"><button onclick="window.choice='accept'; this.closest('.cookie-consent-modal').remove()">Accept All</button>
      <button onclick="window.choice='reject'; this.closest('.cookie-consent-modal').remove()">Reject All</button></div></div>
      <button>Reject All</button><p>An unrelated Reject All outside any cookie notice stays untouched.</p>"""
    fixture = tmp_path / "cookie.html"
    fixture.write_text("<!doctype html><html><body>" + body + "</body></html>")
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    try:
        page.goto(fixture.as_uri())
        assert dismiss_cookie_overlays(page) == 1
        assert page.evaluate("window.choice") == "reject"
    finally:
        b.close(); p.stop()


def test_cookie_modal_that_appears_late_is_rejected_before_next(tmp_path):
    late = """<script>setTimeout(() => document.body.insertAdjacentHTML('beforeend', `<div class="cookie-consent-modal"
      style="position:fixed;inset:0;background:#fff"><p>Our Use of Cookies: strictly necessary cookies and optional cookies.</p>
      <div><div><div><div><div class="cookie-consent__actions"><button onclick="this.closest('.cookie-consent-modal').remove()">Accept All</button>
      <button onclick="window.cookies='rejected'; this.closest('.cookie-consent-modal').remove()">Reject All</button></div></div></div></div></div></div>`), 300)</script>"""
    fixture = tmp_path / "email.html"
    fixture.write_text("<!doctype html><html><body><script>window.buttonTicks = true</script>" + EMAIL_PAGE + late + "</body></html>")
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_default_timeout(8000)
    try:
        page.goto(fixture.as_uri())
        page.wait_for_timeout(600)
        assert sign_in(page, "alex@example.com", lambda since: "482913") is None
        assert page.evaluate("window.cookies") == "rejected"
    finally:
        b.close(); p.stop()


CX_SELECT = """<div class="input-row"><label class="input-row__label" for="country-36"><span>Country <span>*</span></span></label>
  <div class="cx-select-container"><input name="country" id="country-36" type="text" role="combobox" aria-autocomplete="list"
    aria-haspopup="grid" aria-controls="country-36-listbox" aria-expanded="false" aria-required="true" class="cx-select-input"></div>
  <div role="grid" id="country-36-listbox" aria-label="Country" class="cx-select__list" style="display:none"></div></div>
<div class="input-row"><label class="input-row__label" for="country-codes-dropdownphoneNumber" id="phoneNumberGroupLabel">
  <span>Phone Number</span></label><div role="group" aria-labelledby="phoneNumberGroupLabel">
  <span id="inputFieldLabel-country-codes-dropdownphoneNumber">Country code</span>
  <input name="phoneNumber" id="country-codes-dropdownphoneNumber" type="text" role="combobox" aria-haspopup="grid"
    aria-controls="country-codes-dropdownphoneNumber-listbox" aria-labelledby="labelText-undefined inputFieldLabel-country-codes-dropdownphoneNumber" class="cx-select-input" value="">
  <div role="grid" id="country-codes-dropdownphoneNumber-listbox" style="display:none"></div>
  <input type="tel" aria-label="Phone Number"></div></div>
<script>
const lists = {"country-36": ["United States", "United States Minor Outlying Islands", "Canada"],
               "country-codes-dropdownphoneNumber": ["+1 (United States)", "+1 (Canada)", "+44 (United Kingdom)"]};
for (const [id, options] of Object.entries(lists)) {
  const box = document.getElementById(id), grid = document.getElementById(id + "-listbox");
  const show = () => { grid.innerHTML = options.filter(o => o.toLowerCase().includes(box.value.toLowerCase().replace(/^\\+1 ?/, '')))
      .map((o, i) => `<div role="row"><div role="gridcell" id="${id}-listitem-${i}" class="cx-select__list-item">${o}</div></div>`).join('');
    grid.style.display = 'block'; box.setAttribute('aria-expanded', 'true');
    grid.querySelectorAll('[role=gridcell]').forEach(cell => cell.onclick = () => {
      box.value = id === 'country-36' ? cell.textContent : cell.textContent.split(' ')[0]; grid.style.display = 'none'; }); };
  box.addEventListener('click', show); box.addEventListener('input', show);
}
</script>"""


def test_oracle_cx_select_grid_options_and_phone_country_code(tmp_path):
    """Oracle's cx-select opens a role=grid popup of role=gridcell options. The phone country-code box is
    labelled "Phone Number" by its row, so it must not take the phone number as its answer."""
    from job_agent.custom_controls import fill_comboboxes
    from job_agent.profile import Profile
    fixture = tmp_path / "cx.html"
    fixture.write_text("<!doctype html><html><body>" + CX_SELECT + "</body></html>")
    profile = Profile({"answers": {
        "Country": ["United States", {"search": "United States", "matches": "^united states$"}],
        "Phone Number": "(555) 010-0000",
        "Phone country code": [{"search": "United States", "matches": r"^\+1 \(united states\)$"}]}})
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_default_timeout(8000)
    try:
        page.goto(fixture.as_uri())
        filled, missing = fill_comboboxes(page.main_frame, profile, lambda: False, lambda t: None)
        assert missing == [], missing
        assert page.locator("#country-36").input_value() == "United States"
        assert page.locator("#country-codes-dropdownphoneNumber").input_value() == "+1"
    finally:
        b.close(); p.stop()
