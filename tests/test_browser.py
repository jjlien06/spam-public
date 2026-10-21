import json
from playwright.sync_api import sync_playwright
from job_agent.browser import inspect


def test_browser_saved_answers_and_no_submission(tmp_path):
    fixture = tmp_path / "application.html"
    fixture.write_text('''<!doctype html><html><body>
    <form onsubmit="localStorage.setItem('submitted','yes');return false">
      <label>Given name *<input name="first"></label>
      <label>Email address<input name="email" type="email"></label>
      <label>Country<select name="country"><option>Choose</option><option>Canada</option></select></label>
      <label>Unknown question<input name="unknown"></label>
      <label>Password<input name="password" type="password"></label>
      <input type="hidden" name="secret"><input disabled name="disabled">
      <label>Consent<input name="consent" type="checkbox"></label>
      <button type="submit">Submit application</button>
    </form>
    <script>document.addEventListener('input',e=>localStorage.setItem(e.target.name,e.target.value));
    document.addEventListener('change',e=>localStorage.setItem(e.target.name,e.target.value));</script>
    </body></html>''')
    profile = {"answers": {"First name": {"value": "FixtureUser", "aliases": ["Given name"]},
                           "Email address": "fixture@example.com", "Country": "Canada", "Consent": True}}
    browser_dir = tmp_path / "browser"
    report = inspect(fixture.as_uri(), profile, browser_dir, fill=True)
    assert sum(x["state"] == "filled" for x in report["fields"]) == 3
    assert any(x["state"] == "manual_control" for x in report["fields"])
    assert "FixtureUser" not in json.dumps(report)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(browser_dir), headless=True)
        try:
            page = context.new_page()
            page.goto(fixture.as_uri())
            values = page.evaluate("Object.assign({},localStorage)")
            assert values == {"first": "FixtureUser", "email": "fixture@example.com", "country": "Canada"}
            assert "submitted" not in values
        finally:
            context.close()


def test_inspection_does_not_fill_and_sees_frame(tmp_path):
    fixture = tmp_path / "frame.html"
    fixture.write_text('''<label>Email<input name="email" value="untouched"></label>
    <iframe srcdoc='<label>First name<input name="first"></label>'></iframe>''')
    report = inspect(fixture.as_uri(), {"answers": {"Email": "fixture@example.com", "First name": "Alex"}}, tmp_path / "browser")
    assert len(report["fields"]) == 2
    assert all(x["state"] == "matched" for x in report["fields"])
