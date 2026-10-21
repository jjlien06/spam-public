import pytest
from job_agent.decisions import LayaDecider
from playwright.sync_api import sync_playwright


class Model:
    def __init__(self, result):
        self.result = result

    def predict(self, state, options):
        return self.result


class Abstain:
    def choose(self, state, options):
        return None


def test_decider_returns_bounded_index_for_valid_high_confidence_choice():
    model = Model({"answers": {"action": {"choice": "2: continue", "probabilities": {"2: continue": 0.91}}}})
    assert LayaDecider(model).choose("state", ["1: next", "2: continue"]) == 1


def test_decider_abstains_on_malformed_schema():
    assert LayaDecider(Model({"invalid": True})).choose("state", ["1: next"]) is None


def test_decider_abstains_on_out_of_set_choice():
    result = {"answers": {"action": {"choice": "run code", "probabilities": {"run code": 0.99}}}}
    assert LayaDecider(Model(result)).choose("state", ["1: next"]) is None


def test_decider_abstains_below_probability_threshold():
    result = {"answers": {"action": {"choice": "1: next", "probabilities": {"1: next": 0.74}}}}
    assert LayaDecider(Model(result)).choose("state", ["1: next"]) is None


def test_local_laya_chooses_bounded_next_on_two_step_fixture_without_submit():
    pytest.importorskip("laya_mlx")  # optional local navigation model (Apple silicon, MLX)
    from job_agent.runner import prepare

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<label>First name<input name=first></label>
      <button type=button onclick="document.body.innerHTML='<button type=button onclick=window.submitted=true>Submit application</button>'">Continue</button>""")
    try:
        report = prepare(page, {"answers": {"First name": "Alex"}}, max_steps=2,
                         decider=LayaDecider())
        assert report["status"] == "ready_for_review"
        assert report["history"][0]["decision_engine"] == "laya"
        assert page.evaluate("window.submitted === true") is False
    finally:
        browser.close()
        playwright.stop()


def test_abstaining_decider_falls_back_to_sole_validated_next():
    from job_agent.runner import prepare
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<button type=button onclick="document.body.innerHTML='<button type=button>Submit application</button>'">Next</button>""")
    try:
        report = prepare(page, {"answers": {}}, max_steps=2, decider=Abstain())
        assert report["status"] == "ready_for_review"
        assert report["history"][0]["decision_engine"] == "rules_fallback"
    finally:
        browser.close(); playwright.stop()


def test_missing_required_field_blocks_fallback_and_navigation():
    from job_agent.runner import prepare
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<label>Required field<input required></label>
      <button type=button onclick="window.advanced=true">Next</button>""")
    try:
        report = prepare(page, {"answers": {}}, decider=Abstain())
        assert report["status"] == "needs_input"
        assert "Required field" in report["missing"]
        assert page.evaluate("window.advanced === true") is False
    finally:
        browser.close(); playwright.stop()


def test_abstaining_decider_does_not_choose_between_multiple_next_controls():
    from job_agent.runner import prepare
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<button type=button onclick="window.first=true">Next</button>
      <button type=button onclick="window.second=true">Continue</button>""")
    try:
        report = prepare(page, {"answers": {}}, decider=Abstain())
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Laya could not resolve multiple navigation controls"]
        assert page.evaluate("window.first === true || window.second === true") is False
    finally:
        browser.close(); playwright.stop()


def test_ready_for_review_never_clicks_submit_button():
    from job_agent.runner import prepare
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("<button type=button onclick=\"window.submitted=true\">Submit application</button>")
    try:
        report = prepare(page, {"answers": {}}, decider=Abstain())
        assert report["status"] == "ready_for_review"
        assert page.evaluate("window.submitted === true") is False
    finally:
        browser.close(); playwright.stop()


def test_saved_experience_with_empty_add_section_blocks_navigation():
    from job_agent.runner import prepare
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<h2>Work Experience</h2><section><button type=button>Add</button></section>
      <button type=button onclick="window.advanced=true">Next</button>""")
    try:
        report = prepare(page, {"facts": {"experience": [{"company": "Example"}]}}, decider=Abstain())
        assert report["status"] == "needs_input"
        # The repeated-section filler presses Add; when no entry appears it stops
        # rather than pressing Add again or navigating past saved history.
        assert report["missing"] == ["Work Experience: added entry has unsupported markup; not adding again"]
        assert page.evaluate("window.advanced === true") is False
    finally:
        browser.close(); playwright.stop()


def test_navigation_that_finishes_just_after_observation_timeout_is_recognized():
    from job_agent.runner import prepare
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<button type=button onclick="setTimeout(() => document.body.innerHTML='<button type=button>Submit application</button>', 10000)">Next</button>""")
    try:
        report = prepare(page, {"answers": {}}, max_steps=2)
        assert report["status"] == "ready_for_review"
    finally:
        browser.close(); playwright.stop()


def test_checkbox_with_short_name_and_long_consent_label_uses_short_name():
    from job_agent.runner import prepare
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content("""<input id=sms type=checkbox>
      <label for=sms>SMS Opt-in</label>
      <label for=sms>I accept the terms above, and would like to receive both one way informational and two-way conversational text messages.</label>
      <button type=button onclick="window.smsWasChecked=document.getElementById('sms').checked; document.body.innerHTML='<button type=button>Submit application</button>'">Next</button>""")
    try:
        report = prepare(page, {"answers": {"SMS Opt-in": True}}, max_steps=2)
        assert report["status"] == "ready_for_review"
        assert page.evaluate("window.smsWasChecked") is True
    finally:
        browser.close(); playwright.stop()
