from playwright.sync_api import sync_playwright

from job_agent.repeated_sections import fill_repeated


def _page(body):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(body)
    return playwright, browser, page


def test_existing_different_website_url_is_preserved_for_review():
    body = """<section><h4>Websites</h4><button type=button>Add</button>
      <div data-automation-id=editableListItem><label>URL<input type=url value='https://manual.example/me'></label></div></section>"""
    playwright, browser, page = _page(body)
    try:
        report = fill_repeated(page, {"facts": {"links": {"github": "https://github.com/example"}}})
        assert report == ["Websites: existing URL differs from saved record; preserving it"]
        assert page.locator("input[type=url]").input_value() == "https://manual.example/me"
    finally:
        browser.close(); playwright.stop()


def test_invalid_saved_website_url_is_not_reported_as_stuck_successfully():
    body = """<section><h4>Websites</h4><button type=button id=add>Add</button><div id=rows></div></section>
      <script>add.onclick=()=>{rows.innerHTML='<div data-automation-id=editableListItem><label>URL<input type=url></label></div>';
        rows.querySelector('input').addEventListener('input', e=>e.target.value='');};</script>"""
    playwright, browser, page = _page(body)
    try:
        report = fill_repeated(page, {"facts": {"links": {"linkedin": "not-a-url"}}})
        assert any("validation failed" in item or "URL did not stick" in item for item in report)
    finally:
        browser.close(); playwright.stop()


def test_profile_websites_are_added_verified_and_not_duplicated_on_second_pass():
    body = """<section><h4>Websites</h4><button type=button id=add>Add</button><div id=rows></div></section>
      <script>add.onclick=()=>{rows.insertAdjacentHTML('beforeend',
        '<div data-automation-id=editableListItem><label>Website URL<input type=url></label></div>');};</script>"""
    playwright, browser, page = _page(body)
    data = {"facts": {"links": {
        "github": "https://github.com/example", "linkedin": "https://linkedin.com/in/example",
        "portfolio": "https://github.com/example",
    }}}
    try:
        assert fill_repeated(page, data) == []
        fields = page.locator("input[type=url]")
        assert fields.count() == 2  # duplicate profile URLs become one record
        assert fields.evaluate_all("es => es.map(e => e.value)") == [
            "https://github.com/example", "https://linkedin.com/in/example"]
        assert fill_repeated(page, data) == []
        assert fields.count() == 2
    finally:
        browser.close(); playwright.stop()


def test_saved_links_already_present_in_another_order_are_accepted():
    # GM: rows already hold LinkedIn then GitHub (reverse of the profile order).
    body = """<section><h4>Websites</h4><button type=button>Add Another</button>
      <div data-automation-id=editableListItem><label>URL<input type=url value='https://www.linkedin.com/in/alexrivera'></label></div>
      <div data-automation-id=editableListItem><label>URL<input type=url value='github.com/alexrivera/'></label></div></section>"""
    playwright, browser, page = _page(body)
    try:
        report = fill_repeated(page, {"facts": {"links": {"github": "https://github.com/alexrivera", "linkedin": "https://www.linkedin.com/in/alexrivera"}}})
        assert report == []
        assert page.locator("[data-automation-id=editableListItem]").count() == 2
    finally:
        browser.close(); playwright.stop()
