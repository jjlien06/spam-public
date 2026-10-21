"""A new friend end to end, offline: setup → confirmed facts → a one-page form filled to Review from the shared
library alone (no private answers yet) → a stop on an unknown question → learning it → share export."""
import json

from playwright.sync_api import sync_playwright

from job_agent import library, setup
from job_agent.runner import prepare

FORM = """<form>
<div class="field-wrapper"><label for="first">First Name*</label><input id="first" name="first" required></div>
<div class="field-wrapper"><label for="gpa">GPA*</label><input id="gpa" name="gpa" required></div>
<fieldset><legend>Are you legally authorized to work in the United States?*</legend>
  <label><input type="radio" name="auth" value="Yes" required>Yes</label><label><input type="radio" name="auth" value="No">No</label></fieldset>
<div class="field-wrapper"><label for="spon">Will you now or in the future require visa sponsorship?*</label>
  <select id="spon" required><option value="">Select</option><option>Yes</option><option>No</option></select></div>
<div class="field-wrapper"><label for="vet">Protected veteran status</label>
  <select id="vet"><option value="">Select</option><option>I am a protected veteran</option><option>I am not a protected veteran</option>
  <option>I don't wish to answer</option></select></div>
{extra}
<button type="submit" onclick="event.preventDefault()">Submit application</button>
</form>"""
EXTRA = """<fieldset><legend>Do you possess unrestricted permission to be employed stateside?*</legend>
  <label><input type="radio" name="perm" value="Yes" required>Yes</label><label><input type="radio" name="perm" value="No">No</label></fieldset>"""


def fill(tmp_path, body, profile):
    page_file = tmp_path / "form.html"
    page_file.write_text("<!doctype html><html><body>" + body + "</body></html>")
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_default_timeout(8000)
    page.goto(page_file.as_uri())
    try:
        report = prepare(page, profile, max_steps=1)
        values = page.evaluate("""() => ({auth: document.querySelector('[name=auth]:checked')?.value,
            spon: document.getElementById('spon').value, vet: document.getElementById('vet').value,
            gpa: document.getElementById('gpa').value, first: document.getElementById('first').value})""")
        return report, values
    finally:
        browser.close(); p.stop()


def test_a_new_friend_goes_from_setup_to_a_filled_review_page(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAM_HOME", str(tmp_path / "me"))
    setup.start()
    setup.confirm({"legal_first_name": "Alex", "legal_last_name": "Doe", "gpa": 3.6, "work_authorized": True,
                   "needs_sponsorship": False, "veteran": "decline"})
    profile_path = tmp_path / "me" / "profile.json"

    report, values = fill(tmp_path, FORM.replace("{extra}", ""), json.loads(profile_path.read_text()))
    assert report["status"] == "ready_for_review", report
    assert values == {"auth": "Yes", "spon": "No", "vet": "I don't wish to answer", "gpa": "3.6", "first": "Alex"}

    report, _ = fill(tmp_path, FORM.replace("{extra}", EXTRA), json.loads(profile_path.read_text()))
    assert report["status"] == "needs_input"
    assert report["missing"] == ["Do you possess unrestricted permission to be employed stateside?"]

    library.learn(profile_path, "Do you possess unrestricted permission to be employed stateside?", "work_authorized")
    report, _ = fill(tmp_path, FORM.replace("{extra}", EXTRA), json.loads(profile_path.read_text()))
    assert report["status"] == "ready_for_review", report

    exported = library.export(json.loads(profile_path.read_text()))
    assert [r["fact"] for r in exported["rules"]] == ["work_authorized"]
    assert "Alex" not in json.dumps(exported) and "3.6" not in json.dumps(exported)
