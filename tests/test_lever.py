"""Lever's one-page application form (jobs.lever.co/<company>/<id>/apply), copied from live markup (a Lever posting,
2026-09-25): questions named by a wrapping <label> with a ✱ marker, custom "cards" named by .application-label
outside any label, and a location typeahead that must be picked from its dropdown."""
from playwright.sync_api import sync_playwright

from job_agent.review_check import check_review, form_snapshot
from job_agent.runner import prepare

LEVER = """<form id="application-form"><div class="section page-centered application-form"><h4>Submit your application</h4><ul>
<li class="application-question"><label><div class="application-label">Full name<span class="required">✱</span></div><div class="application-field"><input type="text" data-qa="name-input" name="name" required=""></div></label></li>
<li class="application-question"><label><div class="application-label">Email<span class="required">✱</span></div><div class="application-field"><input name="email" data-qa="email-input" type="email" required=""></div></label></li>
<li class="application-question" data-qa="structured-contact-location-question"><label><div class="application-label">Current location <span class="required" data-qa="SCL-question-required-asterisk">✱</span></div><div class="application-field"><input class="location-input" data-qa="location-input" id="location-input" type="text" maxlength="100" name="location" required=""><input id="selected-location" type="hidden" name="selectedLocation"><div class="momentum-body dropdown-container" style="display:none"><div class="dropdown-results width-full cursor-pointer"></div><div class="dropdown-no-results">No location found. Try entering a different location</div><div class="dropdown-loading-results"><span>Loading</span></div></div></div></label></li>
<li class="application-question"><label><div class="application-label">Current company <span class="required">✱</span></div><div class="application-field"><input type="text" data-qa="org-input" name="org" required=""></div></label></li>
</ul></div>
<div class="section page-centered application-form" data-qa="additional-cards"><h4 data-qa="card-name">Export Compliance</h4><ul>
<li class="application-question custom-question"><div><div class="application-label full-width dropdown"><div class="text">Please select what best applies to you regarding ITAR export regulations.<span class="required">✱</span></div></div><div class="application-field full-width required-field"><div class="application-dropdown"><select name="cards[69609295][field0]" required=""><option value="">Please select</option><option>U.S. citizen or national of the United States</option><option>U.S. lawful permanent resident</option><option>Other (please explain below)</option></select></div></div></div></li>
<li class="application-question custom-question"><div><div class="application-label full-width text"><div class="text">If Other, please explain:</div></div><div class="application-field full-width"><input class="card-field-input" type="text" placeholder="Type your response" value="" name="cards[69609295][field1]"></div></div></li>
</ul></div>
<div class="section page-centered application-form" data-qa="additional-cards"><h4 data-qa="card-name">Onsite Work</h4><ul>
<li class="application-question custom-question"><div><div class="application-label full-width multiple-choice"><div class="text">Would you be willing and able to work onsite at our Springfield, IL office five days per week?<span class="required">✱</span></div></div><div class="application-field full-width required-field"><ul data-qa="multiple-choice">
<li><label><input type="radio" name="cards[816f1cbf][field0]" value="Yes, I can work onsite five days per week" required="required"><span class="application-answer-alternative">Yes, I can work onsite five days per week</span></label></li>
<li><label><input type="radio" name="cards[816f1cbf][field0]" value="No, I am not able to work onsite five days per week" required="required"><span class="application-answer-alternative">No, I am not able to work onsite five days per week</span></label></li>
</ul></div></div></li></ul></div>
<button id="btn-submit" type="button" class="postings-btn template-btn-submit black" data-qa="btn-submit" href="#"
  onclick="document.body.innerHTML = '<h3>Application submitted!</h3><p>Thanks for applying.</p>'">Submit application</button>
</form>
<script>(() => {
  const input = document.getElementById('location-input'), box = input.parentElement.querySelector('.dropdown-container');
  const results = box.querySelector('.dropdown-results'), places = ['Springfield, IL, USA', 'Springdale, AR, USA'];
  input.addEventListener('input', () => {
    document.getElementById('selected-location').value = '';
    results.innerHTML = ''; box.style.display = 'block';
    places.filter(p => p.toLowerCase().startsWith(input.value.toLowerCase())).forEach((p, i) => {
      const d = document.createElement('div'); d.className = 'break-word dropdown-location width-full py1 px2'; d.id = 'location-' + i;
      d.textContent = p;
      d.addEventListener('click', () => { input.value = p; box.style.display = 'none';
        document.getElementById('selected-location').value = JSON.stringify({name: p, id: 'x' + i}); });
      results.appendChild(d);
    });
  });
})();</script>"""

PROFILE = {
    "answers": {
        "Full name": "Alex Doe", "Email": "alex@example.com", "Current company": "Example University",
        "Current Location": {"value": ["Springfield, Illinois", {"matches": "^springfield, (?:illinois|il), (?:united states|usa|us)$"}], "aliases": []},
        "Location (City)": {"value": [{"search": "Springfield", "matches": "^springfield, (?:illinois|il), (?:united states|usa|us)$"}], "aliases": []},
        "Please select what best applies to you regarding ITAR export regulations.": "U.S. citizen or national of the United States",
    },
    "patterns": [{"match": "work onsite", "value": {"matches": "^yes"}}],
}


def _page(tmp_path, body):
    fixture = tmp_path / "apply.html"
    fixture.write_text("<!doctype html><html><body>" + body + "</body></html>")
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_default_timeout(8000)
    page.goto(fixture.as_uri())
    return p, b, page


def test_lever_form_is_filled_by_its_question_text_and_location_is_picked(tmp_path):
    p, b, page = _page(tmp_path, LEVER)
    try:
        report = prepare(page, PROFILE, max_steps=1)
        assert report["status"] == "ready_for_review", report
        assert page.locator("[name=name]").input_value() == "Alex Doe"
        assert page.locator("[name=org]").input_value() == "Example University"
        assert page.locator("#location-input").input_value() == "Springfield, IL, USA"
        assert "Springfield, IL, USA" in page.locator("#selected-location").input_value()
        assert page.locator("select").input_value() == "U.S. citizen or national of the United States"
        assert page.locator("input[type=radio]").first.is_checked()
        snapshot = form_snapshot(page.main_frame)
        assert "Would you be willing and able to work onsite at our Springfield, IL office five days per week?" in snapshot
        assert check_review(snapshot, PROFILE) == []
    finally:
        b.close(); p.stop()


def test_unknown_lever_card_question_stops_with_its_text(tmp_path):
    p, b, page = _page(tmp_path, LEVER.replace("regarding ITAR export regulations", "about your hobbies"))
    try:
        report = prepare(page, PROFILE, max_steps=1)
        assert report["status"] == "needs_input"
        assert "Please select what best applies to you about your hobbies." in report["missing"], report
    finally:
        b.close(); p.stop()


def test_value_the_resume_parser_prefilled_without_a_saved_answer_stops_the_run(tmp_path):
    """Palantir: Lever's resume parser typed 2028 (the college year) into an optional
    "Year of High School Graduation" box; SPAM has no answer for it, so it must not ship."""
    card = """<div class="section page-centered application-form" data-qa="additional-cards"><h4 data-qa="card-name">Education</h4><ul>
<li class="application-question custom-question"><div><div class="application-label full-width text"><div class="text">Year of High School Graduation</div></div><div class="application-field full-width"><input class="card-field-input" type="text" placeholder="Type your response" value="2028" name="cards[aa][field0]"></div></div></li>
</ul></div>"""
    p, b, page = _page(tmp_path, LEVER.replace('<button id="btn-submit"', card + '<button id="btn-submit"'))
    try:
        report = prepare(page, PROFILE, max_steps=1)
        assert report["status"] == "needs_input"
        assert "Year of High School Graduation (filled in by the site; no saved answer)" in report["missing"], report
    finally:
        b.close(); p.stop()


def test_single_checkbox_card_is_answered_by_its_question(tmp_path):
    """Palantir: a multiple-select card with one "Yes" box confirms the role applied for;
    its question lives in the card's .application-label, outside any label or fieldset."""
    question = "The FDSE and SWE roles have different applications. Please confirm that your answer to the above question matches the role you are applying for."
    card = f"""<div class="section page-centered application-form" data-qa="additional-cards"><ul>
<li class="application-question custom-question"><div><div class="application-label full-width multiple-select"><div class="text">{question}</div></div><div class="application-field full-width"><ul data-qa="checkboxes"><li><label><input type="checkbox" name="cards[bb][field4]" value="Yes"><span class="application-answer-alternative">Yes</span></label></li></ul></div></div></li>
</ul></div>"""
    p, b, page = _page(tmp_path, LEVER.replace('<button id="btn-submit"', card + '<button id="btn-submit"'))
    try:
        profile = {**PROFILE, "answers": {**PROFILE["answers"], question: "Yes"}}
        report = prepare(page, profile, max_steps=1)
        assert report["status"] == "ready_for_review", report
        assert page.locator("input[name='cards[bb][field4]']").is_checked()
    finally:
        b.close(); p.stop()


def test_select_up_to_three_card_ticks_only_the_saved_choices(tmp_path):
    """Palantir: "Select 1-3" office card; a broad location pattern must not tick every box
    when an exact answer names three."""
    question = "What are your preferred office location(s) to work from in addition to the location you are applying to? Select 1-3 from below."
    boxes = "".join(f'<li><label><input type="checkbox" name="cards[cc][field5]" value="{o}"><span class="application-answer-alternative">{o}</span></label></li>'
                    for o in ["New York, NY", "Washington, DC", "Palo Alto, CA", "Seattle, WA", "Denver, CO"])
    card = f"""<div class="section page-centered application-form" data-qa="additional-cards"><ul>
<li class="application-question custom-question"><div><div class="application-label full-width multiple-select"><div class="text">{question}<span class="required">✱</span></div></div><div class="application-field full-width required-field"><ul data-qa="checkboxes">{boxes}</ul></div></div></li>
</ul></div>"""
    p, b, page = _page(tmp_path, LEVER.replace('<button id="btn-submit"', card + '<button id="btn-submit"'))
    try:
        profile = {**PROFILE, "answers": {**PROFILE["answers"], question: [{"matches": "^new york"}, {"matches": "^palo alto"}, {"matches": "^seattle"}]},
                   "patterns": PROFILE["patterns"] + [{"match": r"preferred (?:work |office )?locations?\b", "value": [{"matches": "new york", "first": True}, {"matches": ".+", "first": True}]}]}
        report = prepare(page, profile, max_steps=1)
        checked = [x.get_attribute("value") for x in page.locator("input[name='cards[cc][field5]']").all() if x.is_checked()]
        assert checked == ["New York, NY", "Palo Alto, CA", "Seattle, WA"], (checked, report)
    finally:
        b.close(); p.stop()


def test_captcha_after_submit_leaves_the_form_for_the_user(tmp_path):
    """Shield AI / Palantir (Lever): Submit opens an hCaptcha challenge instead of sending. Nothing was
    sent; SPAM never solves it, so the job becomes hand_submit with the window left open."""
    body = LEVER.replace("""onclick="document.body.innerHTML = '<h3>Application submitted!</h3><p>Thanks for applying.</p>'\"""",
        """onclick="document.body.insertAdjacentHTML('beforeend', '<div style=&quot;position:fixed;inset:0&quot;><iframe title=&quot;Main content of the hCaptcha challenge&quot; src=&quot;about:blank#https://newassets.hcaptcha.com/captcha/v1/static/hcaptcha.html#frame=challenge&quot; style=&quot;width:400px;height:600px&quot;></iframe></div>')\"""")
    assert "hCaptcha challenge" in body
    p, b, page = _page(tmp_path, body)
    try:
        report = prepare(page, PROFILE, max_steps=1, submit=True)
        assert report["status"] == "hand_submit", report
        assert any("CAPTCHA" in m for m in report["missing"])
    finally:
        b.close(); p.stop()
