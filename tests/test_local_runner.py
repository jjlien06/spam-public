import pytest
from playwright.sync_api import sync_playwright

from job_agent.form_controls import field_actions
from job_agent.local_runner import LocalPlanner, observe, step
from job_agent.profile import Profile


def _profile(**answers):
    return Profile({"facts": {"address": answers.pop("address", {})},
                    "answers": {key: {"value": value} for key, value in answers.items()}})


@pytest.fixture
def page():
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        yield page
    finally:
        browser.close()
        playwright.stop()


def test_address_aliases_use_structured_saved_address_and_unknown_stays_missing(page):
    page.set_content("""<label>Street address <input required></label>
      <label>City or town <input required></label>
      <label>State / Province <input required></label>
      <label>ZIP / Postal Code <input required></label>""")
    profile = _profile(address={"line1": "123 Main St", "city": "Springfield", "state": "MI", "postal_code": "48109"})
    actions, missing = field_actions(page, profile)
    assert {action.value for action in actions} == {"123 Main St", "Springfield", "MI", "48109"}
    assert missing == []

    page.set_content("<label>Street address <input required></label>")
    actions, missing = field_actions(page, _profile())
    assert actions == []
    assert missing == ["Street address"]


def test_native_month_and_split_graduation_selects(page):
    page.set_content("""<label>Graduation date <input type=month required></label>
      <fieldset><legend>Graduation Date</legend>
        <label>Month<select required><option value=0>January</option><option value=4>May</option></select></label>
        <label>Year<select required><option>2027</option><option>2029</option></select></label>
      </fieldset>""")
    profile = _profile(**{"Graduation Date": "2029-05"})
    actions, missing = field_actions(page, profile)
    assert missing == []
    assert [(a.kind, a.value) for a in actions[:1]] == [("fill", "2029-05")]
    assert {a.value for a in actions[1:]} == {"4", "2029"}


def test_aria_combobox_requires_open_then_exact_associated_option(page):
    page.set_content("""<label>Country<input role=combobox aria-label=Country aria-controls=country-list aria-expanded=false></label>
      <div id=country-list role=listbox hidden><div role=option>Canada</div><div role=option>United States</div></div>
      <script>
        const box = document.querySelector('[role=combobox]');
        box.onclick = () => { box.setAttribute('aria-expanded', 'true'); document.querySelector('[role=listbox]').hidden = false; };
      </script>""")
    profile = _profile(Country="Canada")
    actions, missing = field_actions(page, profile)
    assert missing == [] and len(actions) == 1 and actions[0].kind == "click"
    actions[0].control.click()
    actions, missing = field_actions(page, profile)
    assert missing == [] and len(actions) == 1 and actions[0].kind == "click"
    assert actions[0].control.inner_text() == "Canada"


def test_calendar_month_year_commit_does_not_invent_day(page):
    page.set_content("""<label>Graduation Date<button aria-label="Graduation Date" aria-haspopup=dialog aria-controls=grad-dialog>Pick</button></label>
      <div id=grad-dialog role=dialog hidden><label>Year<select aria-label=Year><option>2027</option><option>2029</option></select></label>
        <label>Month<select aria-label=Month><option value=0>January</option><option value=4>May</option></select></label>
        <button>Use this month</button></div>
      <script>document.querySelector('[aria-haspopup=dialog]').onclick=()=>document.getElementById('grad-dialog').hidden=false;</script>""")
    profile = _profile(**{"Graduation Date": "2029-05"})
    actions, missing = field_actions(page, profile)
    assert missing == [] and actions[0].kind == "click"
    actions[0].control.click()
    actions, missing = field_actions(page, profile)
    assert missing == []
    assert {a.value for a in actions} == {"2029", "4"}
    for action in actions:
        action.control.select_option(value=action.value)
    actions, missing = field_actions(page, profile)
    assert missing == [] and len(actions) == 1
    assert actions[0].description == "Confirm graduation month and year"

    page.set_content('<label>Graduation Date<input type=date required></label>')
    actions, missing = field_actions(page, profile)
    assert actions == []
    assert missing == ["Graduation Date (full date required; no day inferred)"]


def test_calendar_without_month_commit_pauses_without_day_guess(page):
    page.set_content("""<label>Graduation Date<button aria-label="Graduation Date" aria-haspopup=dialog aria-controls=grad-dialog>Pick</button></label>
      <div id=grad-dialog role=dialog><select aria-label=Year><option>2029</option></select><select aria-label=Month><option value=4>May</option></select></div>""")
    actions, missing = field_actions(page, _profile(**{"Graduation Date": "2029-05"}))
    assert actions == []
    assert missing == ["Graduation Date (calendar requires a day; only month/year is known)"]


class _Planner:
    def __init__(self, mode="perform", mutate=None):
        self.mode, self.mutate = mode, mutate

    def choose(self, observation):
        if self.mutate:
            self.mutate()
        if self.mode == "perform":
            return observation.actions[0], {"choice": "perform"}
        return None, {"choice": self.mode}


def test_step_stops_on_pause_stale_dom_account_and_final_submit(page):
    page.set_content('<label>Name<input value=""><button>Submit application</button></label>')
    profile = _profile(Name="Alex")
    assert step(page, profile, _Planner("pause"), ("about", ""))["status"] == "model_uncertain"
    page.set_content('<input type=password value=""><button data-automation-id=createAccountSubmitButton>create</button>')
    result = step(page, _profile(), _Planner(), ("about", ""))
    assert result["status"] == "account_handoff"
    assert page.locator('input[type=password]').input_value() == ""
    page.set_content('<label>Name<input value=""><script>window.changed=false</script></label>')
    result = step(page, profile, _Planner(mutate=lambda: page.locator('input').evaluate("e=>e.remove()")), ("about", ""))
    assert result["status"] == "page_changed"
    page.set_content('<button>Submit application</button>')
    assert step(page, _profile(), _Planner(), ("about", ""))["status"] == "ready_for_review"


def test_step_refuses_wrong_origin(page):
    page.route("https://example.com/**", lambda route: route.fulfill(body="<p>Synthetic wrong-origin fixture</p>", content_type="text/html"))
    page.goto("https://example.com")
    assert step(page, _profile(), _Planner(), ("https", "other.example"))["status"] == "needs_input"


def test_local_planner_pauses_on_malformed_or_low_confidence_model_output():
    class BadLaya:
        def predict(self, state, options):
            return {"answers": {"action": {"choice": "perform", "probabilities": {"perform": "unknown"}}}}

    action = object()
    observation = type("Observation", (), {"actions": [type("Action", (), {"id": "x", "description": "safe", "kind": "click"})()]})()
    chosen, trace = LocalPlanner(laya=BadLaya()).choose(observation)
    assert chosen is None
    assert trace["choice"] == "perform"
    assert trace["probability"] == "unknown"
