"""iCIMS: markup modeled on a live tenant (email + privacy step in an iframe, hCaptcha, candidate profile)."""
import pytest
from playwright.sync_api import sync_playwright

from job_agent import icims
from job_agent.queue import platform


def test_icims_postings_are_recognized_on_icims_and_on_company_sites():
    assert platform("https://careers-acme.icims.com/jobs/11555/ml-intern/job") == "icims"
    assert platform("https://careers.acme.com/jobs/34303?icims=1") == "icims"
    assert platform("https://careers.acme.com/jobs/34303") == "generic"


def test_login_url_on_an_icims_host_is_built_from_the_job_number():
    assert icims.login_url("https://careers-acme.icims.com/jobs/11555/ml-intern/job") == \
        "https://careers-acme.icims.com/jobs/11555/login"


def test_login_url_on_a_company_site_is_read_from_its_page():
    page = 'x <a href="https://us-careers-acme.icims.com/jobs/34303/login">Apply</a> y'
    assert icims.login_url("https://careers.acme.com/jobs/34303?icims=1", fetch=lambda url: page) == \
        "https://us-careers-acme.icims.com/jobs/34303/login"
    assert icims.login_url("https://careers.acme.com/jobs/34303?icims=1", fetch=lambda url: "no link") is None


EMAIL_STEP = """<form onsubmit="event.preventDefault(); document.getElementById('cap').style.width='520px';
    document.getElementById('cap').style.height='570px';">
  <label for="email">Email</label><input type="email" id="email" name="css_loginName">
  <label for="accept_privacy"><input type="checkbox" id="accept_privacy" name="accept_privacy" value="1" aria-required="true">
    I confirm I have read the Candidate Data Privacy Notice linked below.</label>
  <input type="submit" id="enterEmailSubmitButton" value="Next"></form>
  <iframe id="cap" src="about:blank#hcaptcha" title="hCaptcha challenge" style="width:0;height:0;border:0"></iframe>"""


@pytest.fixture
def page():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch()
    page = browser.new_page()
    yield page
    browser.close()
    playwright.stop()


def test_email_step_is_filled_and_a_captcha_challenge_stops_for_the_user(page):
    page.set_content(EMAIL_STEP)
    report = icims.pass_email_step(page.main_frame, page, "pat@example.edu", wait_ms=1500)
    assert page.input_value("#email") == "pat@example.edu"
    assert page.is_checked("#accept_privacy")
    assert report["status"] == "captcha_required"


def test_a_central_sign_in_page_stops_for_the_user(page):
    report = icims.signin_gate("https://login.icims.com/u/login/identifier?state=abc")
    assert report["status"] == "account_signin_required"
    assert icims.signin_gate("https://us-careers-acme.icims.com/jobs/34303/x/candidate?mode=apply") is None


def test_the_application_frame_is_the_in_iframe_one(page):
    page.set_content('<iframe src="about:blank?in_iframe=1"></iframe>')
    assert icims.form_frame(page) is not page.main_frame


def test_a_native_select_marked_role_combobox_is_still_answered_and_checked(page):
    from job_agent.runner import fill_native
    from job_agent.profile import Profile
    page.set_content("""<label for="Q57">Are you pursuing post-graduate education?*</label>
      <select id="Q57" role="combobox" aria-required="true"><option value="">— Make a Selection —</option>
        <option value="1">Yes, Masters</option><option value="3">No</option></select>
      <label for="Q58">Did you hear at an event?*</label>
      <select id="Q58" role="combobox" aria-required="true"><option value="">— Make a Selection —</option><option value="9">No</option></select>""")
    profile = Profile({"answers": {"Are you pursuing post-graduate education?": "No"}})
    filled, missing = fill_native(page.main_frame, profile, {}, 0, [], lambda: False)
    assert page.input_value("#Q57") == "3" and filled == 1
    assert missing == ["Did you hear at an event?*"] or missing == ["Did you hear at an event?"]


FINAL_STEP = """<div class="iCIMS_Steps">Step 3 of 4. Voluntary Self-Identification(Completed Step)
  Step 4 of 4. Application Questions(Current Step)</div>
  <form onsubmit="event.preventDefault(); window.sent='yes'">
  <label for="Q1">Are you pursuing post-graduate education?*</label>
  <select id="Q1" aria-required="true"><option value="">— Make a Selection —</option><option value="3">No</option></select>
  <input type="submit" id="quesp_form_submit_i" class="iCIMS_PrimaryButton" value="Next"></form>"""


def test_next_on_the_last_icims_step_is_the_submit_and_is_never_clicked_while_filling(page):
    from job_agent.runner import prepare
    page.goto("data:text/html,<p>x</p>")
    page.set_content(FINAL_STEP)
    report = prepare(page, {"answers": {"Are you pursuing post-graduate education?": "No"}}, max_steps=3)
    assert report["status"] == "ready_for_review"
    assert page.evaluate("() => window.sent || null") is None


def test_icims_final_step_is_read_from_the_step_list(page):
    page.set_content(FINAL_STEP)
    assert icims.on_final_step(page.main_frame) is True
    page.set_content(FINAL_STEP.replace("Step 4 of 4. Application Questions(Current Step)", "Step 2 of 4. Questions(Current Step)"))
    assert icims.on_final_step(page.main_frame) is False


def test_the_answer_check_ignores_the_site_wrapper_around_an_icims_form_frame(page, tmp_path):
    from job_agent.runner import prepare
    (tmp_path / "form.html").write_text(FINAL_STEP)
    (tmp_path / "outer.html").write_text('<footer>Connect With Us<br>linkedin<br>instagram<br>twitter</footer>'
                                         '<iframe src="form.html?in_iframe=1" style="height:600px;width:600px"></iframe>')
    page.goto((tmp_path / "outer.html").as_uri())
    page.wait_for_timeout(500)
    report = prepare(page, {"answers": {"Are you pursuing post-graduate education?": "No",
                                        "LinkedIn": "https://www.linkedin.com/in/example"}}, max_steps=2)
    assert report["status"] == "ready_for_review", report


def test_an_older_differently_named_workday_resume_upload_is_removed(page, tmp_path):
    from job_agent.runner import upload_document
    resume = tmp_path / "Pat_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    page.set_content("""<div data-automation-id="formField-resume"><label>Resume/CV</label>
      <div data-automation-id="file-upload-item"><span data-automation-id="file-upload-item-name">Pat_Old_Resume.pdf</span> 31.5 KB
        <button data-automation-id="delete-file" onclick="this.parentElement.remove()">Delete</button></div>
      <input type="file" id="f" onchange="const d=document.createElement('div'); d.setAttribute('data-automation-id','file-upload-item');
        d.innerHTML='<span data-automation-id=file-upload-item-name>'+this.files[0].name+'</span> 1 KB'; this.before(d)"></div>""")
    filled, gap = upload_document(page.locator("#f").element_handle(), {"resume/cv": resume})
    names = page.locator("[data-automation-id=file-upload-item-name]").all_inner_texts()
    assert gap is None and names == ["Pat_Resume.pdf"]


def test_an_approved_submit_below_a_tall_icims_iframe_is_still_clicked(page, tmp_path):
    from job_agent.runner import submit_application
    (tmp_path / "form.html").write_text('<div style="height:2600px"></div><form onsubmit="event.preventDefault();'
                                        ' document.body.innerHTML=\'Your application was submitted successfully.\'">'
                                        '<input type="submit" id="go" value="Submit"></form>')
    (tmp_path / "outer.html").write_text('<body style="overflow:hidden;margin:0">'
                                         '<iframe src="form.html?in_iframe=1" style="height:3000px;width:600px;border:0"></iframe></body>')
    page.set_viewport_size({"width": 800, "height": 500})
    page.goto((tmp_path / "outer.html").as_uri())
    page.wait_for_timeout(500)
    frame = icims.form_frame(page)
    status, gaps, confirmation = submit_application(page, frame, frame.locator("#go"), lambda text: None)
    assert status == "submitted"


def test_submit_on_an_earlier_icims_step_only_advances_and_the_last_step_stops_for_review(page):
    from job_agent.runner import prepare
    page.set_content("""<div class="iCIMS_Steps">Step 1 of 3. Profile(Completed Step)
      Step 2 of 3. Candidate Questions(Current Step) Step 3 of 3. Job Questions(Incomplete Step)</div>
      <form onsubmit="event.preventDefault(); document.body.innerHTML = window.LAST">
      <label for="Q1">Are you currently enrolled at a university or college?*</label>
      <select id="Q1" aria-required="true"><option value="">— Make a Selection —</option><option value="1">Yes</option></select>
      <input type="submit" id="quesp_form_submit_i" value="Submit"></form>
      <script>window.LAST = `<div class="iCIMS_Steps">Step 3 of 3. Job Questions(Current Step)</div>
        <form onsubmit="event.preventDefault(); window.sent='yes'"><input type="submit" value="Next"></form>`;</script>""")
    report = prepare(page, {"answers": {"Are you currently enrolled at a university or college?": "Yes"}}, max_steps=4)
    assert report["status"] == "ready_for_review"
    assert "Step 3 of 3" in page.locator("body").inner_text()
    assert page.evaluate("() => window.sent || null") is None


VEVRAA = """<form><input type="submit" name="icims_submit" value="Submit">
  <input type="radio" name="icims_f_Veteran" id="icims_f_Veteran_ProtectedVeteran" value="ProtectedVeteran">
  <input type="radio" name="icims_f_Veteran" id="icims_f_Veteran_NotProtectedVeteran" value="NotProtectedVeteran">
  <input type="radio" name="icims_f_Veteran" id="icims_f_Veteran_optout" value="optout">
  <input type="text" id="icims_f_Name" value="Pat Doe"><input type="checkbox" id="icims_f_signature" name="icims_f_signature" value="1">
  <input type="submit" name="icims_submit" value="Submit"></form>"""


def test_the_icims_veteran_form_is_answered_from_the_saved_veteran_status(page):
    page.set_content(VEVRAA)
    from job_agent.profile import Profile
    filled, missing = icims.fill_veteran_form(page.main_frame, Profile({"answers": {"Veteran Status": "I am not a protected veteran"}}))
    assert missing == [] and filled == 2
    assert page.is_checked("#icims_f_Veteran_NotProtectedVeteran") and page.is_checked("#icims_f_signature")


def test_the_icims_veteran_form_stops_without_a_saved_veteran_status(page):
    page.set_content(VEVRAA)
    from job_agent.profile import Profile
    filled, missing = icims.fill_veteran_form(page.main_frame, Profile({"answers": {}}))
    assert missing == ["Veteran status (iCIMS self-identification form)"]


def test_a_ticked_signature_box_is_not_a_name_mismatch():
    from job_agent.review_check import check_review
    profile = {"answers": {"Signature": "Pat Doe"}}
    assert check_review("Signature:\nYes\n", profile) == []
    assert check_review("Signature:\nSam Roe\n", profile) != []


def test_a_portal_form_packet_submit_only_advances_until_its_last_form(page):
    from job_agent.runner import prepare
    page.set_content("""<form onsubmit="event.preventDefault(); document.body.innerHTML = window.LAST">
      <input type="hidden" name="next" value="Voluntary_Self_Identification_of_Disability">
      <input type="submit" name="icims_submit" value="Submit">
      <label for="Q1">Are you currently enrolled at a university or college?*</label>
      <select id="Q1" aria-required="true"><option value="">— Make a Selection —</option><option value="1">Yes</option></select>
      <input type="submit" name="icims_submit" value="Submit"></form>
      <script>window.LAST = `<p>Disability form</p><form onsubmit="event.preventDefault(); window.sent='yes'">
        <input type="hidden" name="next" value=""><input type="submit" name="icims_submit" value="Submit"></form>`;</script>""")
    report = prepare(page, {"answers": {"Are you currently enrolled at a university or college?": "Yes"}}, max_steps=4)
    assert report["status"] == "ready_for_review"
    assert "Disability form" in page.locator("body").inner_text()
    assert page.evaluate("() => window.sent || null") is None
