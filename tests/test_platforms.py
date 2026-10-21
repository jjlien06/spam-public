"""Greenhouse/Ashby-style one-page forms, modeled on markup captured from live postings."""
import json

from playwright.sync_api import sync_playwright

from job_agent.review_check import check_review, form_snapshot, is_form_page
from job_agent.runner import prepare

# Greenhouse's react-select: a role=combobox input whose options render only while
# open (typing filters them), a single-value div once chosen, and an aria-hidden
# required twin input.
REACT_SELECT = """<div class="field-wrapper"><div class="select__container">
  <label id="{id}-label" for="{id}" class="label select__label">{label}<span aria-hidden="true">*</span></label>
  <div class="select-shell"><div class="select__control"><div class="select__value-container">
    <div class="select__placeholder">Select...</div>
    <div class="select__input-container"><input class="select__input" id="{id}" role="combobox" type="text" aria-expanded="false"
      aria-labelledby="{id}-label" aria-required="true" autocomplete="off" value=""></div></div></div>
  <input required tabindex="-1" aria-hidden="true" class="requiredInput" value=""></div></div></div>
<script>(() => {{
  const input = document.getElementById("{id}"), all = {options};
  const shell = input.closest('.select__control');
  const render = () => {{
    document.getElementById("react-select-{id}-listbox")?.remove();
    const q = input.value.toLowerCase();
    const hits = all.filter(o => o.toLowerCase().includes(q));
    const menu = document.createElement('div'); menu.id = "react-select-{id}-listbox"; menu.setAttribute('role', 'listbox');
    (hits.length ? hits : ['No options']).forEach(text => {{
      const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = text;
      o.addEventListener('click', () => {{ if (text === 'No options') return;
        const shown = input.id === 'country' ? text.replace(/^.*(\+\d+)$/, '$1') : text;
        shell.querySelector('.select__placeholder, .select__single-value').outerHTML = '<div class="select__single-value">' + shown + '</div>';
        shell.parentElement.parentElement.querySelector('.requiredInput').value = text;
        input.value = ''; menu.remove(); input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-controls'); }});
      menu.appendChild(o); }});
    shell.after(menu); input.setAttribute('aria-expanded', 'true'); input.setAttribute('aria-controls', menu.id);
  }};
  input.addEventListener('click', render); input.addEventListener('input', render);
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') {{ document.getElementById("react-select-{id}-listbox")?.remove(); input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-controls'); }} }});
}})();</script>"""

TEXT = """<div class="field-wrapper"><label for="{id}">{label}{star}</label><input id="{id}" type="text" {req} value=""></div>"""
COUNTRIES = '["Canada", "Mexico"] .concat(Array.from({length: 40}, (_, i) => "Country " + i)).concat(["United States +1", "United States Minor Outlying Islands"])'

SUBMIT = """<button type="submit" onclick="event.preventDefault(); document.body.innerHTML = window.afterSubmit">Submit application</button>
<script>window.afterSubmit = '<h1>Thank you for applying!</h1><p>Your application has been received.</p>';</script>"""

PROFILE = {
    "answers": {
        "First Name": "Alex", "Last Name": "Rivera",
        "Country": {"value": ["United States of America", {"search": "United States", "matches": "^united states(?: of america)?(?: \\+1)?$"}], "aliases": []},
        "Location (City)": {"value": [{"search": "Springfield", "matches": "^springfield, (?:illinois|il), (?:united states|usa|us)$"},
                                      {"search": "Springfield", "matches": "^springfield, (?:illinois|il)\\b"}], "aliases": []},
    },
    "patterns": [{"match": "are you a us citizen", "value": "Yes"}],
}


def form():
    return "".join([
        "<h1>Apply for Machine Learning Intern</h1><button type=button onclick=\"document.title='wrong button'\">Apply</button>",
        TEXT.format(id="first_name", label="First Name", star="*", req="required"),
        TEXT.format(id="last_name", label="Last Name", star="*", req="required"),
        REACT_SELECT.format(id="country", label="Country", options=COUNTRIES),
        REACT_SELECT.format(id="candidate-location", label="Location (City)",
                            options='["Springfield, Illinois, United States", "Springfield, Illinois, United States", "Springfield, Charter Township, MI", "Anna, Texas"]'),
        REACT_SELECT.format(id="question_1", label="Are you a US citizen? Eligibility for a clearance is required.", options='["Yes", "No"]'),
        SUBMIT,
    ])


def _page(tmp_path, body):
    fixture = tmp_path / "form.html"
    fixture.write_text("<!doctype html><html><body>" + body + "</body></html>")
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_default_timeout(8000)
    page.goto(fixture.as_uri())
    return p, b, page


def test_react_select_comboboxes_are_answered_and_hidden_twins_ignored(tmp_path):
    p, b, page = _page(tmp_path, form())
    try:
        report = prepare(page, PROFILE, max_steps=1)
        assert report["status"] == "ready_for_review", report
        values = page.eval_on_selector_all(".select__single-value", "els => els.map(e => e.textContent)")
        assert values == ["+1", "Springfield, Illinois, United States", "Yes"]
    finally:
        b.close(); p.stop()


def test_unknown_required_combobox_stops_and_records_its_options(tmp_path):
    body = form().replace("Are you a US citizen? Eligibility for a clearance is required.", "Do you like the office dog?")
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, PROFILE, max_steps=1)
        assert report["status"] == "needs_input"
        assert report["missing"] == ["Do you like the office dog?"]
        assert report["options"]["Do you like the office dog?"] == ["Yes", "No"]
    finally:
        b.close(); p.stop()


def test_one_page_form_is_checked_from_its_controls_and_submits(tmp_path):
    p, b, page = _page(tmp_path, form())
    try:
        report = prepare(page, PROFILE, max_steps=1)
        assert report["status"] == "ready_for_review"
        assert is_form_page(page.main_frame)
        snapshot = form_snapshot(page.main_frame)
        assert "First Name:\nAlex" in snapshot
        assert "Are you a US citizen? Eligibility for a clearance is required.:\nYes" in snapshot
        assert check_review(snapshot, PROFILE) == []
        report = prepare(page, PROFILE, max_steps=1, submit=True)
        assert report["status"] == "submitted", report
        assert page.title() != "wrong button"  # the form's Submit, not the posting's Apply
    finally:
        b.close(); p.stop()


def test_a_wrong_combobox_answer_blocks_submit(tmp_path):
    p, b, page = _page(tmp_path, form())
    try:
        prepare(page, PROFILE, max_steps=1)
        page.evaluate("document.querySelectorAll('.select__single-value')[2].textContent = 'No'")
        # No saved answer re-picks it; the review rule must still catch it before Submit.
        guarded = {**PROFILE, "patterns": [], "review_checks": [{"match": "us citizen", "answer": "^yes\\b"}]}
        report = prepare(page, guarded, max_steps=1, submit=True)
        assert report["status"] == "review_flagged", report
        assert any("US citizen" in problem for problem in report["missing"])
        assert page.locator("button[type=submit]").count() == 1  # never clicked
    finally:
        b.close(); p.stop()


def test_uploaded_file_shows_in_the_snapshot_after_its_input_is_replaced(tmp_path):
    body = """<div role="group" aria-labelledby="upload-label-resume" class="file-upload"><div id="upload-label-resume">Resume/CV<span>*</span></div>
      <div class="file-upload__filename"><p>Alex_Rivera_Resume.pdf</p></div></div>
      <div role="group" aria-labelledby="upload-label-cover" class="file-upload"><div id="upload-label-cover">Cover Letter</div>
      <label for="cover_letter">Attach</label><input id="cover_letter" type="file"></div>"""
    p, b, page = _page(tmp_path, body + form())
    try:
        snapshot = form_snapshot(page.main_frame)
        assert "Resume/CV:\nAlex_Rivera_Resume.pdf" in snapshot
        assert "Cover Letter:\nNo Response" in snapshot
        assert "Attach:" not in snapshot
    finally:
        b.close(); p.stop()


def test_emailed_security_code_is_reported_not_counted_as_submitted(tmp_path):
    body = form().replace("<h1>Thank you for applying!</h1><p>Your application has been received.</p>",
                          "<h1>Check your email</h1><p>Enter the 8-character security code sent to your email to submit.</p><input><input><input>")
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, PROFILE, max_steps=1, submit=True)
        assert report["status"] == "email_code_required", report
    finally:
        b.close(); p.stop()


def test_ashby_radio_question_is_its_fieldset_label_not_the_form_name(tmp_path):
    body = """<form aria-labelledby="form-title"><h2 id="form-title">Application</h2>
      <div data-field-path="q1"><fieldset><label for="q1">Are you able to come into the San Francisco office 5 days a week?</label>
        <div><span><input type="radio" id="q1-0" name="q1"><label for="q1-0">Yes</label></span>
             <span><input type="radio" id="q1-1" name="q1"><label for="q1-1">No</label></span></div></fieldset></div>
      <div data-field-path="q2"><fieldset><label for="q2">Unrelated question?</label>
        <div><span><input type="radio" id="q2-0" name="q2"><label for="q2-0">Yes</label></span></div></fieldset></div></form>"""
    p, b, page = _page(tmp_path, body)
    try:
        prepare(page, {"answers": {}, "patterns": [{"match": "come into the [\\w .,-]{0,40}office", "value": "Yes"}]}, max_steps=1)
        assert page.locator("#q1-0").is_checked()
    finally:
        b.close(); p.stop()


def test_a_combobox_revealed_by_an_answer_is_filled_too(tmp_path):
    reveal = """<script>document.addEventListener('click', e => {
      if (e.target.getAttribute('role') === 'option' && e.target.textContent === 'No' && !document.getElementById('race')) {
        document.getElementById('hispanic').closest('.field-wrapper').insertAdjacentHTML('afterend', window.raceHtml);
        window.raceInit(); } }, true);</script>"""
    race = REACT_SELECT.format(id="race", label="Please identify your race", options='["White", "Asian", "Decline To Self Identify"]')
    race_html, race_js = race.split("<script>")
    body = (REACT_SELECT.format(id="hispanic", label="Are you Hispanic/Latino?", options='["Yes", "No"]') + reveal +
            "<script>window.raceHtml = " + repr(race_html) + "; window.raceInit = () => { " + race_js.replace("</script>", "").strip()[:-1] + " };</script>")
    profile = {"answers": {"Are you Hispanic/Latino?": "No"},
               "patterns": [{"match": "identify your race", "value": [{"matches": "^decline"}]}]}
    p, b, page = _page(tmp_path, body)
    try:
        prepare(page, profile, max_steps=1)
        values = page.eval_on_selector_all(".select__single-value", "els => els.map(e => e.textContent)")
        assert values == ["No", "Decline To Self Identify"]
    finally:
        b.close(); p.stop()


def test_cover_letter_box_under_a_resume_heading_never_gets_the_resume(tmp_path):
    resume = tmp_path / "Alex_Rivera_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    body = """<form aria-labelledby="t"><h2 id="t">Application</h2><h3>Autofill from resume</h3>
      <div data-field-path="resume"><label for="res">Resume</label><input type="file" id="res" required></div>
      <div data-field-path="cl"><label for="cl">Cover Letter</label><input type="file" id="cl"></div></form>"""
    p, b, page = _page(tmp_path, body)
    try:
        prepare(page, {"answers": {}, "documents": {"Resume": str(resume)}}, max_steps=1)
        assert page.eval_on_selector("#res", "e => e.files.length") == 1
        assert page.eval_on_selector("#cl", "e => e.files.length") == 0
    finally:
        b.close(); p.stop()


GH_CHECKBOXES = """<fieldset><legend>{question} *</legend>{boxes}</fieldset>
<style>input[type=checkbox] {{ position: absolute; opacity: 0; width: 0; height: 0; }}</style>"""


def gh_boxes(question, name, options):
    boxes = "".join(f'<input type="checkbox" required id="{name}_{i}" name="{name}[]" value="{i}"><label for="{name}_{i}">{o}</label>'
                    for i, o in enumerate(options))
    return GH_CHECKBOXES.format(question=question, boxes=boxes)


def test_hidden_greenhouse_checkbox_groups_are_ticked_or_stop_the_run(tmp_path):
    body = (gh_boxes("What is your expected Month and Date of graduation?", "grad", ["December 2027", "May 2029", "Other"]) +
            gh_boxes("Are you open to relocating to Montpelier, VT or Dallas, TX?", "reloc", ["Texas", "Vermont", "Not willing to relocate"]) +
            "<button type=submit>Submit application</button>")
    profile = {"answers": {}, "patterns": [
        {"match": "expected (?:month and date of )?graduation", "value": [{"matches": "^may 2029$"}]},
        {"match": "open to relocating", "value": "Yes"}]}
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, profile, max_steps=1)
        assert page.locator("#grad_1").is_checked() and not page.locator("#grad_0").is_checked()
        assert report["status"] == "needs_input"  # "Yes" matches no place: stop, don't submit an empty required group
        assert any("relocating" in m for m in report["missing"]), report
        profile["patterns"][1]["value"] = ["Texas", "Vermont"]
        report = prepare(page, profile, max_steps=1)
        assert report["status"] == "ready_for_review", report
        assert page.locator("#reloc_0").is_checked() and page.locator("#reloc_1").is_checked() and not page.locator("#reloc_2").is_checked()
    finally:
        b.close(); p.stop()


ASHBY_TOGGLE = """<div data-field-path="{id}"><label for="{id}">{question}</label><div>
  <button type="button" aria-pressed="false" data-option="yes">Yes</button><button type="button" aria-pressed="false" data-option="no">No</button>
  <input type="checkbox" tabindex="-1" name="{id}" style="display:none"></div></div>"""
TOGGLE_JS = """<script>document.querySelectorAll('button[aria-pressed]').forEach(b => b.addEventListener('click', () => {
  b.parentElement.querySelectorAll('button[aria-pressed]').forEach(x => x.setAttribute('aria-pressed', String(x === b))); }));</script>"""


def test_ashby_yes_no_toggle_buttons_are_answered_checked_and_required(tmp_path):
    body = (ASHBY_TOGGLE.format(id="auth", question="Are you legally authorized to work in the United States for any employer?") +
            ASHBY_TOGGLE.format(id="visa", question="Will you now or will you in the future require employment visa sponsorship?") +
            TOGGLE_JS + form())
    profile = {**PROFILE, "patterns": PROFILE["patterns"] + [
        {"match": "legally authori[sz]ed to work", "value": "Yes"}, {"match": "require[^?]{0,60}sponsorship", "value": "No"}]}
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, profile, max_steps=1)
        assert report["status"] == "ready_for_review", report
        pressed = page.eval_on_selector_all("button[aria-pressed=true]", "els => els.map(e => e.textContent)")
        assert pressed == ["Yes", "No"]
        snapshot = form_snapshot(page.main_frame)
        assert "Will you now or will you in the future require employment visa sponsorship?:\nNo" in snapshot
    finally:
        b.close(); p.stop()
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, PROFILE, max_steps=1)  # no answers for them: stop, as Ashby would refuse the submit
        assert "Are you legally authorized to work in the United States for any employer?" in report["missing"]
    finally:
        b.close(); p.stop()


# Ashby "autocomplete" education fields (Frostline, 2026-09-25): a fieldset whose <label for> points at no element,
# a combobox input named only by its placeholder, and options that appear as you type.
ASHBY_AUTOCOMPLETE = """<fieldset class="_fieldEntry"><label class="ashby-application-form-question-title" for="{uuid}">{label}</label>
<div class="_inputContainer"><input class="ashby-application-form-input-autocomplete" placeholder="Start typing..."
  aria-autocomplete="list" aria-expanded="false" aria-haspopup="listbox" role="combobox" value=""></div></fieldset>
<script>(() => {{
  const input = [...document.querySelectorAll('fieldset')].pop().querySelector('input'), all = {options};
  const render = () => {{
    document.getElementById('list-{uuid}')?.remove();
    const q = input.value.toLowerCase(), menu = document.createElement('div');
    menu.id = 'list-{uuid}'; menu.setAttribute('role', 'listbox');
    all.filter(o => q && o.toLowerCase().includes(q)).forEach(text => {{
      const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = text;
      o.addEventListener('click', () => {{ input.value = text; menu.remove(); input.setAttribute('aria-expanded', 'false'); }});
      menu.appendChild(o); }});
    input.after(menu); input.setAttribute('aria-expanded', 'true'); input.setAttribute('aria-controls', menu.id);
  }};
  input.addEventListener('input', render);
}})();</script>"""


def test_ashby_autocomplete_fields_are_named_by_their_fieldset_label(tmp_path):
    body = "".join([
        ASHBY_AUTOCOMPLETE.format(uuid="u1", label="Degree", options=json.dumps(["Associate Degree", "Bachelor's Degree", "Master's Degree"])),
        ASHBY_AUTOCOMPLETE.format(uuid="u2", label="Discipline", options=json.dumps(["Computer Engineering", "Computer Science"])),
        SUBMIT])
    profile = {"answers": {"Degree": {"value": [{"search": "Bachelor", "matches": "^bachelor'?s degree$"}], "aliases": []},
                           "Discipline": {"value": [{"search": "Computer Science", "matches": "^computer science$"}], "aliases": []}},
               "facts": {"education": [{"degree": "Bachelor's Degree", "degree_synonyms": ["Bachelor's Degree"]}]}}
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, profile, max_steps=1)
        assert report["status"] == "ready_for_review", report
        assert page.eval_on_selector_all("input[role=combobox]", "els => els.map(e => e.value)") == ["Bachelor's Degree", "Computer Science"]
        assert "Degree:\nBachelor's Degree" in form_snapshot(page.main_frame)
    finally:
        b.close(); p.stop()


ASHBY_LOCATION = ASHBY_AUTOCOMPLETE.replace('<fieldset class="_fieldEntry">', '<div class="ashby-application-form-field-entry">') \
    .replace('</div></fieldset>', '</div></div>').replace('<label class="ashby-application-form-question-title"',
                                                         '<label class="_required_f7cvd_91 ashby-application-form-question-title"') \
    .replace("[...document.querySelectorAll('fieldset')].pop()", "[...document.querySelectorAll('.ashby-application-form-field-entry')].pop()")


def test_an_ashby_location_box_outside_a_fieldset_is_named_required_and_picked(tmp_path):
    body = ASHBY_LOCATION.format(uuid="_systemfield_location", label="Location",
                                 options=json.dumps(["Springfield, Illinois, United States", "Springdale, Arkansas, United States"])) + SUBMIT
    places = [{"search": "Springfield", "matches": "^springfield, (?:illinois|il), (?:united states|usa|us)$"}]
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, {"answers": {"Location": {"value": places, "aliases": []}}}, max_steps=1)
        assert report["status"] == "ready_for_review", report
        assert page.locator("input[role=combobox]").input_value() == "Springfield, Illinois, United States"
        assert "Location:\nSpringfield, Illinois, United States" in form_snapshot(page.main_frame)
        page.reload()
        report = prepare(page, {"answers": {}}, max_steps=1)
        assert report["status"] == "needs_input" and report["missing"] == ["Location"], report  # required: never skipped
    finally:
        b.close(); p.stop()


def test_an_empty_saved_answer_leaves_an_optional_combobox_blank(tmp_path):
    """HRT: "Do you have any upcoming offer deadlines?" is optional and every option describes an existing
    offer. A per-posting variant saves [] there: leave it blank instead of stopping."""
    optional = REACT_SELECT.format(id="question_9", label="Do you have any upcoming offer deadlines?",
                                   options='["Less than 2 weeks", "2 to 4 weeks", "I have an offer with no deadline"]') \
        .replace('<span aria-hidden="true">*</span>', "").replace('aria-required="true"', 'aria-required="false"').replace("<input required ", "<input ")
    body = form().replace(SUBMIT, optional + SUBMIT)
    profile = {**PROFILE, "answers": {**PROFILE["answers"], "Do you have any upcoming offer deadlines?": {"value": [], "aliases": []}}}
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, profile, max_steps=1)
        assert report["status"] == "ready_for_review", report
    finally:
        b.close(); p.stop()
