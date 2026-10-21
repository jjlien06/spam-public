"""Workday-style custom controls, modeled on markup captured from a live tenant."""
import pytest
from playwright.sync_api import sync_playwright

from job_agent.custom_controls import option_match
from job_agent.runner import prepare


# Emulates Workday's listbox button: click renders a separate ul[role=listbox]
# asynchronously; choosing an option updates the button text; Escape closes.
POPUP_SHIM = """<script>
document.querySelectorAll('button[aria-haspopup=listbox]').forEach(button => {
  const options = JSON.parse(button.dataset.options);
  const key = button.id || ('anon' + Math.random().toString(36).slice(2));
  const close = () => { document.getElementById('popup-' + key)?.remove(); button.setAttribute('aria-expanded', 'false'); };
  button.addEventListener('click', () => {
    if (button.getAttribute('aria-expanded') === 'true') return close();
    const ul = document.createElement('ul'); ul.setAttribute('role', 'listbox'); ul.id = 'popup-' + key;
    ['Select One', ...options].forEach((text, index) => {
      const li = document.createElement('li'); li.setAttribute('role', 'option'); li.dataset.value = index ? 'v' + index : '';
      if (!index) li.setAttribute('aria-disabled', 'true');
      li.innerHTML = '<div>' + text + '</div>';
      li.onclick = () => { button.textContent = text; button.value = li.dataset.value; setTimeout(close, 300); };  // Workday closes late
      ul.appendChild(li);
    });
    setTimeout(() => { document.body.appendChild(ul); button.setAttribute('aria-expanded', 'true'); }, 150);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
});
</script>"""


def listbox(field, label, options, required=True, current="Select One"):
    star = '<abbr aria-hidden="true">*</abbr>' if required else ""
    aria = f"{label} {current}" + (" Required" if required else "")
    return f"""<div data-automation-id="formField-{field}"><label for="{field}"><span>{label}{star}</span></label>
      <div><button aria-haspopup="listbox" aria-label="{aria}" id="{field}" name="{field}" type="button" value=""
        data-options='{options}'>{current}</button><input type="text" value="" style="width:0;height:0;border:0;padding:0"></div></div>"""


def text_field(field, label, required=False):
    star = '<abbr aria-hidden="true">*</abbr>' if required else ""
    return f"""<div data-automation-id="formField-{field}"><label for="{field}"><span>{label}{star}</span></label>
      <div><input {'aria-required="true"' if required else ''} id="{field}" name="{field}" type="text" value=""></div></div>"""


def radio_group(question, name="candidateIsPreviousWorker"):
    return f"""<div data-automation-id="formField-{name}"><fieldset><legend><label id="radio-label-{name}"><span>{question}<abbr aria-hidden="true">*</abbr></span></label></legend>
      <div aria-labelledby="radio-label-{name}" aria-required="true" id="group-{name}" name="{name}">
        <div><input aria-checked="false" id="{name}-yes" name="{name}" type="radio" value="true"><label for="{name}-yes">Yes</label></div>
        <div><input aria-checked="false" id="{name}-no" name="{name}" type="radio" value="false"><label for="{name}-no">No</label></div>
      </div></fieldset></div>"""


TOKEN_WIDGET = """<div data-automation-id="formField-countryPhoneCode"><label for="phoneNumber--countryPhoneCode"><span>Country Phone Code<abbr aria-hidden="true">*</abbr></span></label>
  <div data-automation-id="multiSelectContainer"><div data-automation-id="multiselectInputContainer">
    <input aria-required="true" autocomplete="off" id="phoneNumber--countryPhoneCode" placeholder="Search" value="">
    <ul aria-label="items selected" data-automation-id="selectedItemList" role="listbox">
      <li data-automation-id="menuItem" role="presentation"><div aria-label="United States of America (+1), press delete to clear value." data-automation-id="selectedItem" role="option">
        <p data-automation-id="promptOption">United States of America (+1)</p></div></li></ul></div></div></div>"""

FOOTER = """<button data-automation-id="pageFooterNextButton" type="button"
  onclick="document.body.innerHTML='<h2>Review</h2><button type=button onclick=&quot;localStorage.setItem(\\'submitted\\',\\'yes\\')&quot;>Submit</button>'">Save and Continue</button>"""

PROFILE = {
    "answers": {
        "First Name": "Alex", "Last Name": "Rivera", "Phone": {"value": "(555) 010-0100", "aliases": ["Phone Number"]},
        "Address Line 1": "100 Example Street", "City": "Springfield", "State": "Illinois", "Postal Code": "62701",
        "How Did You Hear About Us?": "LinkedIn", "Phone Device Type": "Mobile",
        "Country Phone Code": "United States of America (+1)",
    },
    "patterns": [{"match": "previously employed", "value": "No", "unless_mentions": ["Acme Robotics"]}],
}


def _page(tmp_path, body):
    fixture = tmp_path / "workday.html"
    fixture.write_text("<!doctype html><html><body>" + body + POPUP_SHIM + "</body></html>")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(fixture.as_uri())
    return playwright, browser, page


def my_information_page():
    return "".join([
        listbox("source--source", "How Did You Hear About Us?", '["Indeed","LinkedIn","Other"]'),
        radio_group("Were you previously employed at Red Maple Bank?"),
        listbox("country--country", "Country", '["Canada","United States of America"]', current="United States of America"),
        text_field("name--legalName--firstName", "First Name", required=True),
        text_field("name--legalName--lastName", "Last Name", required=True),
        listbox("name--legalName--social", "Suffix", '["Jr.","Sr."]', required=False),
        text_field("address--addressLine1", "Address Line 1"),
        text_field("address--city", "City"),
        listbox("address--countryRegion", "State", '["Alabama","Illinois","Minnesota"]', required=False),
        text_field("address--postalCode", "Postal Code"),
        listbox("phoneNumber--phoneType", "Phone Device Type", '["Landline","Mobile"]'),
        TOKEN_WIDGET,
        text_field("phoneNumber--phoneNumber", "Phone Number", required=True),
        text_field("phoneNumber--extension", "Phone Extension"),
        FOOTER,
    ])


def test_workday_my_information_page_fills_hands_free(tmp_path):
    playwright, browser, page = _page(tmp_path, my_information_page())
    try:
        report = prepare(page, PROFILE)
        assert report["missing"] == []
        assert report["status"] == "ready_for_review"
        assert page.evaluate("Object.assign({}, localStorage)") == {}
    finally:
        browser.close()
        playwright.stop()


def test_workday_controls_receive_saved_values_before_navigation(tmp_path):
    playwright, browser, page = _page(tmp_path, my_information_page().replace(FOOTER, ""))
    try:
        report = prepare(page, PROFILE, max_steps=1)
        assert report["missing"] == ["No unique supported navigation control"]
        assert page.locator("#candidateIsPreviousWorker-no").is_checked()
        assert not page.locator("#candidateIsPreviousWorker-yes").is_checked()
        texts = {field: page.locator("#" + field.replace("--", "\\-\\-")).inner_text()
                 for field in ("source--source", "phoneNumber--phoneType", "address--countryRegion", "country--country", "name--legalName--social")}
        assert texts == {"source--source": "LinkedIn", "phoneNumber--phoneType": "Mobile", "address--countryRegion": "Illinois",
                         "country--country": "United States of America", "name--legalName--social": "Select One"}
        assert page.locator("#name--legalName--firstName").input_value() == "Alex"
        assert page.locator("#address--addressLine1").input_value() == "100 Example Street"
        assert page.locator("#phoneNumber--phoneNumber").input_value() == "(555) 010-0100"
        # The prefilled pill widget is left alone and its search box stays empty.
        assert page.locator('[data-automation-id=selectedItem]').count() == 1
        assert page.locator("#phoneNumber--countryPhoneCode").input_value() == ""
        assert page.locator("[role=listbox]:not([data-automation-id=selectedItemList])").count() == 0, "popups closed"
    finally:
        browser.close()
        playwright.stop()


def test_required_listbox_without_answer_is_reported_by_its_clean_label(tmp_path):
    playwright, browser, page = _page(tmp_path, listbox("source--source", "How Did You Hear About Us?", '["Indeed"]') + FOOTER)
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "needs_input"
        assert report["missing"] == ["How Did You Hear About Us?"]
        assert page.locator("#source--source").inner_text() == "Select One"
    finally:
        browser.close()
        playwright.stop()


def test_listbox_answer_not_among_options_is_reported_and_popup_closed(tmp_path):
    playwright, browser, page = _page(tmp_path, listbox("source--source", "How Did You Hear About Us?", '["Indeed"]') + FOOTER)
    try:
        report = prepare(page, {"answers": {"How Did You Hear About Us?": "LinkedIn"}})
        assert report["status"] == "needs_input"
        assert report["missing"] == ["How Did You Hear About Us? (saved answer not among options)"]
        assert page.locator("#source--source").get_attribute("aria-expanded") != "true"
    finally:
        browser.close()
        playwright.stop()


def test_radio_question_naming_exception_employer_stops_for_user(tmp_path):
    playwright, browser, page = _page(tmp_path, radio_group("Were you previously employed at Acme Robotics?") + FOOTER)
    try:
        report = prepare(page, PROFILE)
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Were you previously employed at Acme Robotics?"]
        assert page.locator("input[type=radio]:checked").count() == 0
    finally:
        browser.close()
        playwright.stop()


def test_boolean_answers_select_yes_no_radios(tmp_path):
    playwright, browser, page = _page(tmp_path, radio_group("Are you at least 18 years old?", name="age"))
    try:
        report = prepare(page, {"answers": {"Are you at least 18 years old?": True}}, max_steps=1)
        assert report["missing"] == ["No unique supported navigation control"]
        assert page.locator("#age-yes").is_checked()
    finally:
        browser.close()
        playwright.stop()


def test_saved_answer_corrects_a_stale_radio_choice_and_asks_about_unanswered_ones(tmp_path):
    # Strykon: an earlier run left "No" on "Are you 18 years or older?".
    body = (radio_group("Are you 18 years or older?", name="age") + radio_group("Do you like pizza?", name="pizza"))
    playwright, browser, page = _page(tmp_path, body)
    try:
        page.check("#age-no"); page.check("#pizza-yes")
        report = prepare(page, {"answers": {"Are you 18 years or older?": "Yes"}}, max_steps=1)
        # No saved answer: the existing choice is left alone, but nobody approved it, so ask.
        assert report["missing"] == ["Do you like pizza? (filled in by the site; no saved answer)"]
        assert page.locator("#age-yes").is_checked()
        assert page.locator("#pizza-yes").is_checked()
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.parametrize("value, expected", [
    ("LinkedIn", "v2"),                       # exact, case-insensitive
    ("United States of America (+1)", None),  # no option; never a partial guess
    ("Mich", None),                           # prefixes of options are not accepted
    ("No, I do not have a disability", "v4"),  # saved answer may be a prefix of one long option
    ("IL", "v3"),                             # US state abbreviation resolves to the full name
    ("Select One", None),                     # placeholder is disabled
])
def test_option_match_variants(value, expected):
    options = [
        {"label": "Select One", "value": "", "disabled": True},
        {"label": "Indeed", "value": "v1"}, {"label": "linkedin", "value": "v2"}, {"label": "Illinois", "value": "v3"},
        {"label": "No, I do not have a disability and have not had one in the past", "value": "v4"},
    ]
    assert option_match(options, value) == expected


def test_option_match_rejects_ambiguous_prefix():
    options = [{"label": "Yes, currently", "value": "a"}, {"label": "Yes, previously", "value": "b"}]
    assert option_match(options, "Yes") is None


def questionnaire_item(question, options='["Yes","No"]'):
    # Workday questionnaire: question in a rich-text legend; button has no label[for].
    ident = "q" + str(abs(hash(question)) % 10**8)
    return f"""<div data-automation-id="formField-{ident}"><fieldset><legend><div id="rich-{ident}"><div data-automation-id="richText">
      <p><b>{question}</b><abbr title="required">*</abbr></p></div></div></legend>
      <div><button aria-haspopup="listbox" aria-label=" Select One Required" id="primaryQuestionnaire--{ident}" name="{ident}" type="button" value=""
        data-options='{options}'>Select One</button><input type="text" value="" style="width:0;height:0;border:0;padding:0"></div></fieldset></div>"""


def test_questionnaire_listbox_is_named_by_its_legend(tmp_path):
    body = questionnaire_item("Are you at least 18 years or older? (If no, you may be required to provide authorization to work).") + \
        questionnaire_item("Have you ever been convicted of a crime of dishonesty?")
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {}, "patterns": [{"match": r"at least 18|18 years or older", "value": "Yes"}]}, max_steps=1)
        assert report["missing"] == ["Have you ever been convicted of a crime of dishonesty?"]
        assert page.locator("button[aria-haspopup=listbox]").first.inner_text() == "Yes"
    finally:
        browser.close()
        playwright.stop()


def test_option_match_prefix_rule_needs_a_long_answer():
    options = [{"label": "Select One", "value": "", "disabled": True}, {"label": "1-2 years", "value": "a"},
               {"label": "3-5 years", "value": "b"}, {"label": "10+ years", "value": "c"}]
    assert option_match(options, "1") is None
    assert option_match(options, 1) is None


def test_shared_fieldset_does_not_make_optional_fields_required(tmp_path):
    body = """<fieldset><legend>Application *</legend>
      <label for=first>First Name *</label><input id=first aria-required=true>
      <label for=middle>Middle Name</label><input id=middle>
      <label for=nick>Nickname</label><button aria-haspopup=listbox id=nick data-options='["A","B"]'>Select One</button>
      </fieldset>"""
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"First Name": "Alex"}}, max_steps=1)
        assert report["missing"] == ["No unique supported navigation control"], "optional fields must not be flagged"
    finally:
        browser.close()
        playwright.stop()


def test_listbox_button_without_id_is_verified_by_its_handle(tmp_path):
    body = """<span id=lbl>Phone Device Type</span><button aria-haspopup=listbox aria-labelledby=lbl data-options='["Landline","Mobile"]'>Select One</button>"""
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"Phone Device Type": "Mobile"}}, max_steps=1)
        assert report["missing"] == ["No unique supported navigation control"]
        assert page.locator("button[aria-haspopup=listbox]").inner_text() == "Mobile"
    finally:
        browser.close()
        playwright.stop()


def test_listbox_trigger_that_would_submit_a_form_is_left_alone(tmp_path):
    body = """<form onsubmit="localStorage.setItem('submitted','yes');return false">
      <label for=st>State</label><button id=st aria-haspopup=listbox data-options='["Illinois"]'>Select One</button></form>"""
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"State": "Illinois"}}, max_steps=1)
        assert "State (dropdown trigger would submit the form)" in report["missing"]
        assert page.evaluate("Object.assign({}, localStorage)") == {}
    finally:
        browser.close()
        playwright.stop()


def test_unknown_required_prefilled_text_is_preserved_and_not_missing(tmp_path):
    body = """<label>Imported question *<input required value='already answered'></label>"""
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {}}, max_steps=1)
        assert report["status"] == "needs_input"
        assert report["missing"] == ["No unique supported navigation control"]
        assert page.locator("input").input_value() == "already answered"
    finally:
        browser.close()
        playwright.stop()


def test_unknown_empty_required_text_still_blocks_progress(tmp_path):
    body = """<label>Imported question *<input required></label>
      <button type=button>Continue</button>"""
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {}}, max_steps=1)
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Imported question", "Imported question (validation failed)"]
    finally:
        browser.close()
        playwright.stop()


def test_unknown_invalid_prefilled_email_is_rejected(tmp_path):
    body = """<label>Imported email *<input type=email required value='not-an-email'></label>
      <button type=button>Continue</button>"""
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {}}, max_steps=1)
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Imported email (validation failed)"]
    finally:
        browser.close()
        playwright.stop()


def test_option_match_accepts_one_category_prefixed_option_with_the_exact_value():
    from job_agent.custom_controls import option_match
    options = [{"label": text, "value": str(i)} for i, text in enumerate(
        ["Job Board - Indeed", "Job Board - LinkedIn", "Social Media - Facebook", "Social Media - LinkedIn Ads"])]
    assert option_match(options, "LinkedIn") == "1"
    # Only whole values after the category count; ambiguity abstains.
    assert option_match(options, "Linked") is None
    twice = options + [{"label": "Social Media - LinkedIn", "value": "9"}]
    assert option_match(twice, "LinkedIn") is None


def test_option_match_picks_the_one_numeric_range_containing_a_saved_number():
    from job_agent.custom_controls import option_match
    labels = ["Select One", "Below 2.0", "2.0 - 2.49", "2.5 - 2.99", "3.0 - 3.49", "3.5 or higher"]
    options = [{"label": text, "value": str(i)} for i, text in enumerate(labels)]
    assert option_match(options, "3.8") == "5"
    assert option_match(options, "3.2") == "4"
    assert option_match(options, "1.9") == "1"
    assert option_match(options, "3.49") == "4"
    # Overlapping ranges are ambiguous; non-numeric answers never use ranges.
    overlap = options + [{"label": "3.0 - 4.0", "value": "9"}]
    assert option_match(overlap, "3.8") is None
    assert option_match(options, "Yes") is None


def test_textarea_named_only_by_its_fieldset_legend_is_filled():
    from job_agent.runner import prepare
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start(); b = p.chromium.launch(headless=True); page = b.new_page()
    page.set_content("""<div data-automation-id=formField-x><fieldset><legend><div><p><b>How many credit hours will you have completed?<abbr>*</abbr></b></p></div></legend>
      <div><textarea aria-required=true id=credits></textarea></div></fieldset></div>
      <button type=button onclick="document.body.innerHTML='<button type=button>Submit</button>'">Save and Continue</button>""")
    try:
        report = prepare(page, {"answers": {"How many credit hours will you have completed?": "100"}})
        assert report["status"] == "ready_for_review"
    finally:
        b.close(); p.stop()


def test_question_revealed_by_an_earlier_answer_is_filled_before_advancing(tmp_path):
    # QTX: answering "enrolled: Yes" reveals "What is your Current Degree Program?".
    body = (listbox("enrolled", "Are you currently enrolled in a degree seeking program?", '["Yes","No"]') +
            '<div id=reveal style="display:none">' + listbox("degree", "What is your Current Degree Program?", '["Associates","Bachelors"]') + "</div>" +
            """<script>new MutationObserver(() => { if (document.getElementById('enrolled').textContent === 'Yes')
                 setTimeout(() => document.getElementById('reveal').style.display = 'block', 1500); }).observe(document.body, {subtree: true, childList: true, characterData: true});</script>""" + FOOTER)
    playwright, browser, page = _page(tmp_path, body)
    answers = {"Are you currently enrolled in a degree seeking program?": "Yes", "What is your Current Degree Program?": "Bachelor's"}
    try:
        filled = []
        report = prepare(page, {"answers": answers}, on_event=filled.append)
        assert report["status"] == "ready_for_review", report
        assert "Selected: What is your Current Degree Program?" in filled, filled
    finally:
        browser.close()
        playwright.stop()


PROMPT_WIDGET = """<div data-automation-id="formField-source"><label for="source--source"><span>How Did You Hear About Us?<abbr aria-hidden="true">*</abbr></span></label>
  <div data-automation-id="multiSelectContainer"><div data-automation-id="multiselectInputContainer">
    <input aria-required="true" autocomplete="off" id="source--source" placeholder="Search" value=""></div></div></div>
  <script>
  const leaves = ["Job Board", "LinkedIn Connection Post", "Indeed"];
  const src = document.getElementById('source--source');
  src.addEventListener('keydown', e => { if (e.key !== 'Enter') return;
    document.getElementById('prompt')?.remove();
    const hits = leaves.filter(l => l.toLowerCase().includes(src.value.toLowerCase()));
    document.body.insertAdjacentHTML('beforeend', '<div id=prompt data-behavior-click-outside-close=topmost><div role=listbox aria-label="Options Expanded">' +
      hits.map(h => '<div role=option><p data-automation-id=promptOption>' + h + '</p></div>').join('') + '</div></div>');
    document.querySelectorAll('#prompt [role=option]').forEach(o => o.onclick = () => {
      src.value = ''; src.closest('[data-automation-id=multiselectInputContainer]').insertAdjacentHTML('beforeend',
        '<ul role=listbox data-automation-id=selectedItemList><li><div data-automation-id=selectedItem role=option><p data-automation-id=promptOption>' + o.innerText + '</p></div></li></ul>'); });
  });
  document.addEventListener('mousedown', e => { const p = document.getElementById('prompt'); if (p && !p.contains(e.target)) p.remove(); }, true);
  </script>"""


def test_search_prompt_picks_the_first_saved_preference_the_site_offers(tmp_path):
    checked_footer = FOOTER.replace('onclick="', 'onclick="if (!document.querySelector(\'[data-automation-id=selectedItem]\')) return; ', 1)
    playwright, browser, page = _page(tmp_path, "<h2>My Information</h2>" + PROMPT_WIDGET + checked_footer)
    try:
        report = prepare(page, {"answers": {"How Did You Hear About Us?": ["LinkedIn", "Job Board"]}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


NESTED_WIDGET = PROMPT_WIDGET.replace("""    const hits = leaves.filter(l => l.toLowerCase().includes(src.value.toLowerCase()));
    document.body.insertAdjacentHTML('beforeend', '<div id=prompt data-behavior-click-outside-close=topmost><div role=listbox aria-label="Options Expanded">' +
      hits.map(h => '<div role=option><p data-automation-id=promptOption>' + h + '</p></div>').join('') + '</div></div>');""",
"""    const cats = {"Website": ["Glassdoor", "Company Website"], "Social": ["Linkedln", "Social Media"]};
    const hits = Object.keys(cats).filter(l => l.toLowerCase().includes(src.value.toLowerCase()));
    document.body.insertAdjacentHTML('beforeend', '<div id=prompt data-behavior-click-outside-close=topmost><div role=listbox aria-label="Options Expanded">' +
      hits.map(h => '<div role=option class=cat><div data-uxi-multiselectlistitem-hassidecharm=true><p data-automation-id=promptOption>' + h + '</p></div></div>').join('') + '</div></div>');
    document.querySelectorAll('#prompt .cat').forEach(c => c.addEventListener('click', e => { e.stopImmediatePropagation();
      document.querySelector('#prompt [role=listbox]').innerHTML = cats[c.innerText.trim()].map(h => '<div role=option class=leaf><p data-automation-id=promptOption>' + h + '</p></div>').join('');
      document.querySelectorAll('#prompt .leaf').forEach(o => o.onclick = () => { src.value = ''; src.closest('[data-automation-id=multiselectInputContainer]').insertAdjacentHTML('beforeend',
        '<ul role=listbox data-automation-id=selectedItemList><li><div data-automation-id=selectedItem role=option><p data-automation-id=promptOption>' + o.innerText + '</p></div></li></ul>'); });
    }, true));""")


def test_search_prompt_picks_inside_a_category(tmp_path):
    # Athene: "Website" and "Social" are categories whose entries open on click.
    assert "cats" in NESTED_WIDGET
    checked_footer = FOOTER.replace('onclick="', 'onclick="if (document.querySelector(\'[data-automation-id=selectedItem]\')?.innerText !== \'Company Website\') return; ', 1)
    playwright, browser, page = _page(tmp_path, "<h2>My Information</h2>" + NESTED_WIDGET + checked_footer)
    try:
        page.set_default_timeout(8000)
        answer = [{"search": "Website", "matches": "\\bwebsite\\b"}]
        report = prepare(page, {"answers": {"How Did You Hear About Us?": answer}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_search_prompt_category_pick_shows_as_a_prefixed_chip_and_counts(tmp_path):
    # KLA: the search lists "Linkedin" (a Social Network entry) and the chip then reads
    # "Social Network: Linkedin". The saved plain "LinkedIn" must still count as picked.
    widget = (PROMPT_WIDGET.replace('["Job Board", "LinkedIn Connection Post", "Indeed"]', '["Job Board", "Linkedin", "Indeed"]')
              .replace("<p data-automation-id=promptOption>' + o.innerText + '</p></div></li></ul>'",
                       "<p data-automation-id=promptOption>Social Network: ' + o.innerText + '</p></div></li></ul>'"))
    assert "Social Network: ' + o.innerText" in widget
    checked_footer = FOOTER.replace('onclick="', 'onclick="if (document.querySelector(\'[data-automation-id=selectedItem]\')?.innerText !== \'Social Network: Linkedin\') return; ', 1)
    playwright, browser, page = _page(tmp_path, "<h2>My Information</h2>" + widget + checked_footer)
    try:
        page.set_default_timeout(8000)
        report = prepare(page, {"answers": {"How Did You Hear About Us?": ["LinkedIn"]}})
        assert report["status"] == "ready_for_review", report
        assert report["missing"] == []
    finally:
        browser.close()
        playwright.stop()


def test_option_match_uses_preferences_in_order():
    opts = [{"label": t, "value": str(i)} for i, t in enumerate(["Job Board - LinkedIn", "Job Board - Indeed", "Job Board"])]
    assert option_match(opts, ["LinkedIn", "Job Board"]) == "0"
    assert option_match(opts[1:], ["LinkedIn", "Job Board"]) == "2"


CAREERS = {"search": "Career", "matches": r"career(s)?\s*(web)?\s*site|careers? website"}


def test_option_match_pattern_answer_picks_the_one_careers_site_option():
    qtx = [{"label": t, "value": str(i)} for i, t in enumerate(["Job Board - LinkedIn", "QTX Corporate Career Site", "Radio/Music Apps"])]
    assert option_match(qtx, CAREERS) == "1"
    both = qtx + [{"label": "Careers Website", "value": "9"}]
    assert option_match(both, CAREERS) is None, "two matches: abstain"


def test_search_prompt_uses_a_pattern_answer(tmp_path):
    widget = PROMPT_WIDGET.replace('["Job Board", "LinkedIn Connection Post", "Indeed"]',
                                   '["Job Board", "Cloudforce Careers Website", "Indeed"]')
    checked_footer = FOOTER.replace('onclick="', 'onclick="if (!document.querySelector(\'[data-automation-id=selectedItem]\')) return; ', 1)
    playwright, browser, page = _page(tmp_path, "<h2>My Information</h2>" + widget + checked_footer)
    try:
        report = prepare(page, {"answers": {"How Did You Hear About Us?": {"value": CAREERS}}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_stray_open_prompt_is_closed_before_filling_the_page(tmp_path):
    overlay = """<div id=stray data-behavior-click-outside-close=topmost style="position:fixed;inset:60px 0 0 0;background:#fff">
      <div data-automation-id=promptLeafNode>Leftover option</div></div>
      <script>document.addEventListener('mousedown', e => { const s = document.getElementById('stray'); if (s && !s.contains(e.target)) s.remove(); }, true);</script>"""
    playwright, browser, page = _page(tmp_path, "<h2>My Information</h2>" + radio_group("Have you previously worked for Example?") + overlay + FOOTER)
    try:
        page.set_default_timeout(8000)
        report = prepare(page, {"answers": {"Have you previously worked for Example?": "No"}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_search_prompt_without_a_named_listbox_popup(tmp_path):
    # Cloudforce: results render in a plain click-outside popup, not "Options Expanded".
    widget = (PROMPT_WIDGET.replace('["Job Board", "LinkedIn Connection Post", "Indeed"]', '["Job Board", "Cloudforce Careers Website", "Indeed"]')
              .replace('<div role=listbox aria-label="Options Expanded">', '<div>'))
    checked_footer = FOOTER.replace('onclick="', 'onclick="if (!document.querySelector(\'[data-automation-id=selectedItem]\')) return; ', 1)
    playwright, browser, page = _page(tmp_path, "<h2>My Information</h2>" + widget + checked_footer)
    try:
        report = prepare(page, {"answers": {"How Did You Hear About Us?": {"value": CAREERS}}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_mm_dd_yyyy_spinbutton_date_is_typed_from_the_first_box(tmp_path):
    # Cloudforce "anticipated graduation date": Month/Day/Year spinbuttons that only
    # take typed digits, advance on their own, and bounce direct focus to Month.
    seg = lambda name, size: f'<input role=spinbutton aria-label={name} data-automation-id=dateSection{name}-input value="" data-max={size}>'
    body = ("""<div data-automation-id=formField-grad><label><span>Please input your anticipated graduation date<abbr>*</abbr></span></label>
      <div data-automation-id=dateInputWrapper>""" + seg("Month", 2) + seg("Day", 2) + seg("Year", 4) + """</div></div>
      <script>
      const segs = [...document.querySelectorAll('[role=spinbutton]')];
      segs.forEach((e, i) => { e.dataset.state = '';
        e.addEventListener('keydown', k => { if (!/^[0-9]$/.test(k.key)) return; k.preventDefault();
          e.dataset.buf = ((e.dataset.buf || '') + k.key).slice(-Number(e.dataset.max)); e.value = e.dataset.state = String(Number(e.dataset.buf));
          if (e.dataset.buf.length === Number(e.dataset.max) && segs[i + 1]) { window.advancing = true; segs[i + 1].focus(); window.advancing = false; } });
        e.addEventListener('input', () => { e.value = e.dataset.state; });
        e.addEventListener('focus', () => { e.dataset.buf = ''; if (i > 0 && !window.advancing) segs[0].focus(); });
      });
      </script>""" + FOOTER)
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"Graduation date": "2029-05-01"}})
        assert report["status"] == "ready_for_review", report
        # Leidon words the same date "Expected degree completion date".
        page.set_content(page.content().replace("Please input your anticipated graduation date", "5a. Expected degree completion date."))
        report = prepare(page, {"answers": {"Graduation date": "2029-05-01"}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_numbers_only_field_takes_the_next_text_preference(tmp_path):
    # Athene "Desired Salary": the box drops everything but digits.
    body = (text_field("salary", "Desired Salary", required=True) +
            "<script>document.getElementById('salary').addEventListener('input', e => { e.target.value = e.target.value.replace(/\\D/g, ''); });</script>")
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"Desired Salary": ["Open to discussion", "63200", {"matches": "^60,000"}]}}, max_steps=1)
        assert page.locator("#salary").input_value() == "63200"
        assert report["missing"] == ["No unique supported navigation control"], report
    finally:
        browser.close()
        playwright.stop()


def test_text_answer_the_site_throws_away_is_reported_not_skipped(tmp_path):
    body = (text_field("city", "City", required=True) +
            "<script>document.getElementById('city').addEventListener('input', e => { e.target.value = ''; });</script>" + FOOTER)
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"City": "Springfield"}})
        assert report["status"] == "needs_input"
        assert "City (did not stick)" in report["missing"]
    finally:
        browser.close()
        playwright.stop()


def test_search_prompt_pick_that_does_not_register_is_reported(tmp_path):
    # Clicking the option closes the prompt but the site never records a selection.
    widget = (PROMPT_WIDGET.replace('["Job Board", "LinkedIn Connection Post", "Indeed"]', '["Job Board"]')
              .replace("src.value = ''; src.closest('[data-automation-id=multiselectInputContainer]').insertAdjacentHTML('beforeend',", "src.value = ''; void(")
              )
    playwright, browser, page = _page(tmp_path, "<h2>My Information</h2>" + widget + FOOTER)
    try:
        report = prepare(page, {"answers": {"How Did You Hear About Us?": "Job Board"}})
        assert report["status"] == "needs_input", report
        assert report["missing"] == ["How Did You Hear About Us? (saved answer not among options)"]
    finally:
        browser.close()
        playwright.stop()


REVIEW_PAGE = """<h2>Review</h2><h3>My Experience</h3><h4>Work Experience</h4>
  <h5>Work Experience 1</h5><div>Job Title</div><div>Teaching Assistant</div><div>From</div><div>{grader}</div>
  <h5>Work Experience 2</h5><div>Job Title</div><div>Software Engineering Intern</div><div>From</div><div>06/2025</div><div>To</div><div>08/2025</div>
  <h4>Education</h4><div>Degree</div><div>BS</div>
  <button type=button onclick="window.submitted = true">Submit</button>"""
EXPERIENCE = {"facts": {"experience": [
    {"title": "Teaching Assistant", "start_date": "2025-09", "current": True},
    {"title": "Software Engineering Intern", "start_date": "2025-06", "end_date": "2025-08"}],
    "education": [{"degree": "Bachelor's", "degree_synonyms": ["Bachelor of Science", "Bachelor's", "BS", {"matches": "^bachelor"}]}]}}


@pytest.mark.parametrize("grader, status", [("09/2025", "submitted_or_review"), ("12/2012", "review_mismatch")])
def test_review_audit_blocks_submit_when_the_summary_disagrees_with_the_profile(tmp_path, grader, status):
    playwright, browser, page = _page(tmp_path, REVIEW_PAGE.format(grader=grader))
    try:
        report = prepare(page, EXPERIENCE, submit=True)
        if status == "review_mismatch":
            assert report["status"] == "review_mismatch"
            assert report["missing"] == ["Review: Teaching Assistant shows no 09/2025 start date"]
            assert page.evaluate("window.submitted") is None, "never submits a mismatched application"
        else:
            assert report["status"] != "review_mismatch", report
    finally:
        browser.close()
        playwright.stop()


def test_a_field_that_cannot_be_clicked_stops_cleanly_and_saves_the_page(tmp_path):
    # An overlay SPAM cannot close covers the question: no traceback, a saved capture.
    blocker = '<div style="position:fixed;inset:0;background:rgba(0,0,0,.1)"></div>'
    playwright, browser, page = _page(tmp_path, "<h2>Questions</h2>" + radio_group("Have you previously worked for Example?") + blocker + FOOTER)
    try:
        page.set_default_timeout(4000)
        report = prepare(page, {"answers": {"Have you previously worked for Example?": "No"}}, bug_dir=tmp_path / "bugs")
        assert report["status"] == "needs_input"
        assert report["missing"][0].startswith("Could not fill this page")
        saved = list((tmp_path / "bugs").glob("*.html"))
        assert saved and "Have you previously worked" in saved[0].read_text()
    finally:
        browser.close()
        playwright.stop()


def test_answer_the_site_clears_is_refilled_once_after_its_required_error(tmp_path):
    # QTX: picking a later dropdown cleared an earlier answer; Next then said
    # "The field ... is required and must have a value."
    q1, q2 = "Are you a FORMER federal employee?", "Are you a PwC employee?"
    body = (listbox("former", q1, '["Yes","No"]') + listbox("pwc", q2, '["Yes","No"]') + """
      <script>let cleared = false;
      new MutationObserver(() => { const f = document.getElementById('former');
        if (!cleared && document.getElementById('pwc').textContent === 'No' && f.textContent === 'No') { cleared = true; f.textContent = 'Select One'; f.value = ''; } })
        .observe(document.body, {subtree: true, childList: true, characterData: true});</script>
      <button data-automation-id=pageFooterNextButton type=button onclick="
        if (!document.getElementById('former').value) { document.querySelectorAll('[data-automation-id=inputAlert]').forEach(e => e.remove());
          document.body.insertAdjacentHTML('beforeend', '<p data-automation-id=inputAlert>Error: The field """ + q1 + """ is required and must have a value.</p>'); return; }
        document.body.innerHTML = '<h2>Review</h2><button type=button>Submit</button>'">Save and Continue</button>""")
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {q1: "No", q2: "No"}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_yes_no_answer_matches_the_one_option_that_starts_with_it():
    opts = [{"label": t, "value": str(i)} for i, t in enumerate(
        ["Select One", "Previously cleared (debriefed two or more years ago)", "Yes, current active clearance", "No, never held a clearance"])]
    assert option_match(opts, "No") == "3"
    assert option_match(opts, "Yes") == "2"
    two = opts + [{"label": "No - prefer not to say", "value": "9"}]
    assert option_match(two, "No") is None, "two options start with No: abstain"


def checkbox_group(field, question, options):
    boxes = "".join(f'<div><input type=checkbox id="{field}-{i}"><label for="{field}-{i}">{o}</label></div>' for i, o in enumerate(options))
    return f'<div data-automation-id="formField-{field}"><fieldset><legend><label><span>{question}<abbr>*</abbr></span></label></legend>{boxes}</fieldset></div>'


def test_checkbox_group_is_answered_as_a_choice_by_its_question(tmp_path):
    # Harlow Allen: Yes/No and preferred locations are checkbox groups under a question.
    q1 = "This internship does not offer relocation. Can you be at the work location without company support?"
    q2 = "Select your preferred internship location(s)."
    body = (checkbox_group("reloc", q1, ["Yes", "No"]) + checkbox_group("loc", q2, ["Atlanta, GA", "McLean, VA", "San Diego, CA"]) + FOOTER)
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {q1: "Yes", q2: "McLean, VA"}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_checkbox_group_marks_only_the_chosen_box(tmp_path):
    q = "Select your preferred internship location(s)."
    playwright, browser, page = _page(tmp_path, checkbox_group("loc", q, ["Atlanta, GA", "McLean, VA"]))
    try:
        prepare(page, {"answers": {q: "McLean, VA"}})
        assert page.locator("#loc-1").is_checked() and not page.locator("#loc-0").is_checked()
    finally:
        browser.close()
        playwright.stop()


def test_signature_date_is_filled_with_today(tmp_path):
    import datetime
    seg = lambda name, size: f'<input role=spinbutton aria-label={name} data-automation-id=dateSection{name}-input value="" data-max={size}>'
    body = ("""<div data-automation-id=formField-sig><label><span>Date<abbr>*</abbr></span></label>
      <div data-automation-id=dateInputWrapper>""" + seg("Month", 2) + seg("Day", 2) + seg("Year", 4) + """</div></div>
      <script>const segs=[...document.querySelectorAll('[role=spinbutton]')];
      segs.forEach((e,i)=>{e.addEventListener('keydown',k=>{if(!/^[0-9]$/.test(k.key))return;k.preventDefault();
        e.dataset.buf=((e.dataset.buf||'')+k.key).slice(-Number(e.dataset.max));e.value=String(Number(e.dataset.buf));
        if(e.dataset.buf.length===Number(e.dataset.max)&&segs[i+1])segs[i+1].focus();});
        e.addEventListener('focus',()=>{e.dataset.buf='';});});</script>""" + FOOTER)
    playwright, browser, page = _page(tmp_path, body)
    try:
        today = datetime.date.today()
        filled = []
        page.evaluate("() => { window.readback = () => [...document.querySelectorAll('[role=spinbutton]')].map(e => e.value); }")
        report = prepare(page, {"answers": {}}, on_event=filled.append)
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_review_audit_reads_the_degree_line_not_a_word_inside_it(tmp_path):
    # Vistara: "Degree" then "Bachelor’s or Equivalent First-Degree (ISCED 6: BA, BSc, ...)".
    body = """<h2>Review</h2><div>Education</div><div>Degree</div>
      <div>Bachelor’s or Equivalent First-Degree (ISCED 6: BA, BSc, BEng, LLB, etc.)</div>
      <button type=button>Submit</button>"""
    playwright, browser, page = _page(tmp_path, body)
    try:
        from job_agent.runner import audit_review
        profile = {"facts": {"education": [{"degree": "Bachelor's", "degree_synonyms": ["Bachelor of Science", "BS", {"matches": "^bachelor(?!.*\\\\barts?\\\\b)"}]}]}}
        assert audit_review(page, profile) == []
    finally:
        browser.close()
        playwright.stop()


def test_unanswered_dropdown_options_are_recorded_in_the_report(tmp_path):
    body = listbox("mq", "Do you meet the minimum qualifications?", '["Yes","No"]') + FOOTER
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {}})
        assert report["status"] == "needs_input"
        assert report["options"]["Do you meet the minimum qualifications?"] == ["Yes", "No"]
    finally:
        browser.close()
        playwright.stop()


def test_checkbox_named_like_a_text_answer_falls_back_to_its_group_question(tmp_path):
    # P&G: a "LinkedIn" source checkbox must not use the saved LinkedIn profile URL.
    q = "What source(s) of information influenced your decision to apply?"
    body = checkbox_group("src", q, ["LinkedIn", "Company Website"]) + FOOTER
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"LinkedIn": "https://www.linkedin.com/in/x", q: "Company Website"}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_a_saved_number_picks_the_money_range_that_contains_it():
    opts = lambda labels: [{"label": label, "value": label} for label in labels]
    assert option_match(opts(["$25,000 - $35,000", "$55,000 - $65,000", "$65,000 - $75,000"]), "63200") == "$55,000 - $65,000"
    assert option_match(opts(["Up to $40,000", "$40,000-$60,000", "$60,000-$80,000", "$200,000+"]), "63200") == "$60,000-$80,000"
    assert option_match(opts(["Up to 25,000", "50,000 to 75,000", "75,000 to 100,000"]), "63200") == "50,000 to 75,000"


VIRTUAL_WIDGET = """<div data-automation-id="formField-fos"><label for="fos"><span>Field of Study<abbr>*</abbr></span></label>
  <div data-automation-id="multiSelectContainer"><div data-automation-id="multiselectInputContainer">
    <input aria-required="true" id="fos" placeholder="Search" value=""></div></div></div>
  <script>
  // J&J/Birchline: an alphabetical list of 200 majors that renders only the rows in view; typing does not filter.
  const majors = Array.from({length: 200}, (_, i) => 'Major ' + String(i).padStart(3, '0'));
  majors[150] = 'Computer and Information Science';
  const fos = document.getElementById('fos');
  fos.addEventListener('keydown', e => { if (e.key !== 'Enter') return;
    document.getElementById('prompt')?.remove();
    document.body.insertAdjacentHTML('beforeend', '<div id=prompt data-behavior-click-outside-close=topmost><div role=listbox aria-label="Options Expanded" style="height:100px;overflow-y:auto;position:relative"><div id=spacer style="height:4000px"></div></div></div>');
    const list = document.querySelector('#prompt [role=listbox]');
    const draw = () => { list.querySelectorAll('.row').forEach(r => r.remove());
      const first = Math.floor(list.scrollTop / 20);
      majors.slice(first, first + 6).forEach((m, k) => list.insertAdjacentHTML('beforeend',
        '<div class=row role=option style="position:absolute;top:' + (first + k) * 20 + 'px;height:20px"><p data-automation-id=promptOption>' + m + '</p></div>'));
      list.querySelectorAll('.row').forEach(o => o.onclick = () => { fos.closest('[data-automation-id=multiselectInputContainer]').insertAdjacentHTML('beforeend',
        '<ul role=listbox data-automation-id=selectedItemList><li><div data-automation-id=selectedItem role=option><p data-automation-id=promptOption>' + o.innerText + '</p></div></li></ul>'); });
    };
    list.addEventListener('scroll', draw); draw();
  });
  document.addEventListener('mousedown', e => { const p = document.getElementById('prompt'); if (p && !p.contains(e.target)) p.remove(); }, true);
  </script>"""


def test_search_scrolls_a_virtualized_list_to_find_the_saved_choice(tmp_path):
    checked_footer = FOOTER.replace('onclick="', 'onclick="if (document.querySelector(\'[data-automation-id=selectedItem]\')?.innerText !== \'Computer and Information Science\') return; ', 1)
    playwright, browser, page = _page(tmp_path, "<h2>My Experience</h2>" + VIRTUAL_WIDGET + checked_footer)
    try:
        page.set_default_timeout(8000)
        report = prepare(page, {"answers": {"Field of Study": ["Computer Science", "Computer and Information Science"]}})
        assert report["status"] == "ready_for_review", report
    finally:
        browser.close()
        playwright.stop()


def test_greenhouse_employment_rows_take_completed_experience_records():
    """Coinbase's Greenhouse form has an Employment row (company-name-0, start-date-month-0 ...)
    that requires an end date; it takes completed roles, never a global "Title" answer."""
    from types import SimpleNamespace
    from job_agent.custom_controls import record_value
    facts = {"experience": [
        {"company": "State University", "title": "Teaching Assistant", "start_date": "2026-08", "end_date": None, "current": True},
        {"company": "Acme Vision", "title": "Software Engineering Intern", "start_date": "2026-06", "end_date": "2026-08"}],
        "education": [{"school": "State University", "start_date": "2024-08", "end_date": "2028-05"}]}
    profile = SimpleNamespace(data={"facts": facts})
    assert record_value("company-name-0", profile) == "Acme Vision"
    assert record_value("title-0", profile) == "Software Engineering Intern"
    assert record_value("start-date-month-0", profile) == "June"
    assert record_value("start-date-year-0", profile) == "2026"
    assert record_value("end-date-month-0", profile) == "August"
    assert record_value("end-date-year-0", profile) == "2026"
    assert record_value("company-name-1", profile) is None  # no second completed role
    assert record_value("start-month--0", profile) == "August"  # education rows unchanged


def test_draft_choice_without_a_saved_answer_stops_the_run(tmp_path):
    """HPE: a Workday draft already held YES for a citizenship question SPAM has no
    answer for; keeping it would submit an answer nobody approved."""
    question = "For any of the countries listed below, are you a citizen or have a passport?"
    body = questionnaire_item(question).replace(">Select One</button>", ">YES</button>").replace('value=""', 'value="y"', 1)
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {}}, max_steps=1)
        assert question + " (filled in by the site; no saved answer)" in report["missing"], report
    finally:
        browser.close()
        playwright.stop()


def test_an_empty_saved_answer_leaves_an_optional_radio_group_blank(tmp_path):
    optional = radio_group("If applicable, when do you estimate you will require sponsorship?", "sponsorWhen").replace(
        ' aria-required="true"', "").replace('<abbr aria-hidden="true">*</abbr>', "")
    playwright, browser, page = _page(tmp_path, optional + FOOTER)
    try:
        report = prepare(page, {"answers": {"If applicable, when do you estimate you will require sponsorship?": []}})
        assert report["missing"] == []
        assert page.locator("input[type=radio]:checked").count() == 0
    finally:
        browser.close()
        playwright.stop()


def test_listbox_option_ending_in_a_period_is_confirmed_as_applied(tmp_path):
    # KLA's veteran list offers "I am not a Veteran."; normalize() drops the trailing period, so the
    # read-back compared "i am not a veteran" to the button's "i am not a veteran." and reported
    # "selection did not apply" although the pick had worked.
    body = listbox("veteranStatus", "Please select your veteran status:", '["I am a Veteran.","I am not a Veteran."]')
    playwright, browser, page = _page(tmp_path, body)
    try:
        profile = {"answers": {"Please select your veteran status:": "I am not a Veteran."}}
        report = prepare(page, profile, max_steps=1)
        assert not any("selection did not apply" in m for m in report["missing"])
        assert page.locator("#veteranStatus").inner_text() == "I am not a Veteran."
    finally:
        browser.close()
        playwright.stop()


def test_checkbox_group_whose_question_is_repeated_in_two_paragraphs_is_answered(tmp_path):
    # GM's Workday questionnaire: the legend holds "What is your major or concentration?" twice
    # (the second with the required asterisk), and the options are plain checkboxes.
    options = ["Computer Engineering", "Computer Science", "Mathematics"]
    boxes = "".join(f'<div><input id="m{i}" type="checkbox" aria-required="true"><label for="m{i}">{name}</label></div>'
                    for i, name in enumerate(options))
    body = (f'<div data-automation-id="formField-major"><fieldset><legend><div><p>What is your major or concentration?</p>'
            f'<p>What is your major or concentration?<abbr title="required">*</abbr></p></div></legend>{boxes}</fieldset></div>')
    playwright, browser, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"What is your major or concentration?": {"value": ["Computer Science"], "aliases": []}}}, max_steps=1)
        assert not any("major or concentration" in m for m in report["missing"]), report["missing"]
        assert [page.locator(f"#m{i}").is_checked() for i in range(3)] == [False, True, False]
    finally:
        browser.close()
        playwright.stop()
