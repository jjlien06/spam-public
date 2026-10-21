from playwright.sync_api import sync_playwright

from job_agent.repeated_sections import close_prompts, fill_repeated, search_key
from job_agent.runner import prepare


ROW = """<div data-automation-id=editableListItem>
  <label>Job title<input name=title></label><label>Company<input name=company></label>
  <label>Location<input name=location></label><label>Description<textarea name=description></textarea></label>
</div>"""


def _page(body):
    p = sync_playwright().start()
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(body)
    return p, b, page


def test_fill_repeated_work_record_and_second_run_does_not_duplicate():
    body = """<section><h2>Work Experience</h2><button type=button id=add>Add</button><div id=rows></div></section>
    <script>add.onclick=()=>{rows.innerHTML += %r;}</script>""" % ROW.replace("'", "\\'")
    p, b, page = _page(body)
    data = {"facts": {"experience": [{"title": "Engineer", "company": "Example", "location": "Remote", "bullets": ["Built tools"]}]}}
    try:
        assert fill_repeated(page, data) == []
        assert page.locator('[data-automation-id=editableListItem]').count() == 1
        assert page.locator("input[name=title]").input_value() == "Engineer"
        assert fill_repeated(page, data) == []
        assert page.locator('[data-automation-id=editableListItem]').count() == 1
    finally:
        b.close(); p.stop()


def test_fill_repeated_stop_callback_does_not_add_entry():
    body = "<section><h2>Education</h2><button type=button>Add</button></section>"
    p, b, page = _page(body)
    try:
        missing = fill_repeated(page, {"facts": {"education": [{"school": "Example U"}]}}, stop=lambda: True)
        assert missing == []
        assert page.locator('[data-automation-id=editableListItem]').count() == 0
    finally:
        b.close(); p.stop()


def test_global_company_answer_does_not_overwrite_scoped_saved_record(tmp_path):
    body = """<section><h2>Work Experience</h2><button type=button>Add</button>
      <div data-automation-id=editableListItem><label>Company<input name=company></label></div></section>
      <button type=button>Submit application</button>"""
    p, b, page = _page(body)
    try:
        data = {"answers": {"Company": "Global Company"},
                "facts": {"experience": [{"company": "Scoped Company"}]}}
        report = prepare(page, data)
        assert report["status"] == "ready_for_review"
        assert page.locator("input[name=company]").input_value() == "Scoped Company"
    finally:
        b.close(); p.stop()


def test_heading_based_numbered_work_record_fills_dates_and_is_idempotent():
    body = """<section><h2>Work Experience</h2><button type=button>Add Another</button>
      <div><h3>Work Experience 1</h3>
        <label>Job title<input aria-label='Job title'></label>
        <label>Company<input aria-label=Company></label>
        <div aria-label=From><label>Month<input aria-label=Month></label><label>Year<input aria-label=Year></label></div>
      </div></section>"""
    p, b, page = _page(body)
    data = {"facts": {"experience": [{"title": "Engineer", "company": "Example", "start_date": "2023-05"}]}}
    try:
        assert fill_repeated(page, data) == []
        assert page.locator("input[aria-label='Job title']").input_value() == "Engineer"
        assert page.locator("input[aria-label=Month]").input_value() == "05"
        assert page.locator("input[aria-label=Year]").input_value() == "2023"
        assert fill_repeated(page, data) == []
        assert page.locator("h3").count() == 1
    finally:
        b.close(); p.stop()


def test_level_four_sections_and_level_five_records_are_discovered():
    body = """<section><h4>Work Experience</h4><button type=button>Add Another</button>
      <div><h5>Work Experience 1</h5><label>Job title<input aria-label='Job title'></label>
      <label>Company<input aria-label=Company></label></div></section>
      <section><h4>Education</h4><button type=button>Add Another</button>
      <div><h5>Education 1</h5><label>School<input aria-label=School></label>
      <label>Degree<input aria-label=Degree></label></div></section>"""
    p, b, page = _page(body)
    data = {"facts": {"experience": [{"title": "Engineer", "company": "Example"}],
                       "education": [{"school": "Example University", "degree": "BSc"}]}}
    try:
        assert fill_repeated(page, data) == []
        assert page.locator("input[aria-label='Job title']").input_value() == "Engineer"
        assert page.locator("input[aria-label=School]").input_value() == "Example University"
        assert fill_repeated(page, data) == []
        assert page.locator("h5").count() == 2
    finally:
        b.close(); p.stop()


def test_month_picker_sets_saved_month_year_without_inventing_day_and_degree_alias():
    body = """<section><h4>Education</h4><button type=button>Add Another</button>
      <div><h5>Education 1</h5><input aria-label=RecordMarker style='display:none'><div aria-label='From'><button aria-label=Calendar type=button></button></div>
      <label>Degree<button aria-label=Degree aria-haspopup=listbox type=button>Select One</button></label></div></section>
      <script>
      const row=document.querySelector('h5').parentElement;
      row.querySelector('[aria-label=Calendar]').onclick=()=>{
        document.querySelector('[role=list]')?.remove(); document.body.insertAdjacentHTML('beforeend','<div role=list aria-label="Month Picker"><button aria-label="Previous Year">Prev</button><button aria-label="August 2026">August 2026</button></div>');
        document.querySelector('[aria-label="Previous Year"]').onclick=()=>document.querySelector('[role=list] button[aria-label="August 2026"]').setAttribute('aria-label','August 2025');
      };
      row.querySelector('[aria-haspopup=listbox]').onclick=()=>document.body.insertAdjacentHTML('beforeend','<div role=listbox><div role=option>Bachelors</div></div>');
      document.addEventListener('click',e=>{if(e.target.getAttribute('role')==='option'){e.target.parentElement.remove();document.querySelector('[aria-haspopup=listbox]').textContent=e.target.textContent;}});
      </script>"""
    p, b, page = _page(body)
    data = {"facts": {"education": [{"degree": "Bachelor's", "start_date": "2025-08"}]}}
    try:
        assert fill_repeated(page, data) == []
        assert page.get_by_role('button', name='August 2025', exact=True).count() == 1
        assert page.locator("button[aria-haspopup=listbox]").inner_text() == "Bachelors"
        assert fill_repeated(page, data) == []
    finally:
        b.close(); p.stop()


def test_calendar_stop_callback_does_not_click_or_add_day():
    body = """<section><h4>Education</h4><button type=button>Add Another</button>
      <div><h5>Education 1</h5><div aria-label='From'><button aria-label=Calendar type=button></button></div></div></section>"""
    p, b, page = _page(body)
    try:
        assert fill_repeated(page, {"facts": {"education": [{"start_date": "2025-08"}]}}, stop=lambda: True) == []
        assert page.locator('[role=list][aria-label="Month Picker"]').count() == 0
    finally:
        b.close(); p.stop()


def test_search_key_normalizes_dash_spacing_without_merging_campuses():
    assert search_key("Example State University - Springfield") == search_key("Example State University–Springfield")
    assert search_key("Example State University - Riverside") != search_key("Example State University - Springfield")
    assert search_key("Example State University - Springfield not checked") == search_key("Example State University - Springfield")


def test_multiselect_search_popup_is_closed_before_the_next_dropdown():
    # QTX: picking a Field of Study keeps the multiselect prompt open over the
    # page until Escape, so the Degree dropdown click is intercepted.
    body = """<section><h4>Education</h4><button type=button>Add Another</button>
      <div><h5>Education 1</h5><input aria-label=RecordMarker style='display:none'>
      <label>Field of Study<input placeholder=Search id=fos></label>
      <label>Degree<button aria-label=Degree aria-haspopup=listbox type=button id=deg>Select One</button></label></div></section>
      <script>
      fos.addEventListener('keydown', e => { if (e.key !== 'Enter') return;
        document.body.insertAdjacentHTML('beforeend', `<div id=overlay style="position:fixed;inset:0;background:#fff">
          <div role=listbox aria-label="Options Expanded"><div role=option>Computer Science</div><div role=option>Computer Science/Communications</div></div></div>`);
        overlay.querySelectorAll('[role=option]').forEach(o => o.onclick = () => { fos.dataset.picked = o.textContent; fos.insertAdjacentHTML('afterend', '<div data-automation-id=selectedItem><p data-automation-id=promptOption>' + o.textContent + '</p></div>'); });
      });
      document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('overlay')?.remove(); });
      deg.onclick = () => document.body.insertAdjacentHTML('beforeend', '<div role=listbox id=degopts><div role=option>Bachelors</div></div>');
      document.addEventListener('click', e => { if (e.target.closest('#degopts') && e.target.getAttribute('role') === 'option') {
        deg.textContent = e.target.textContent; degopts.remove(); } });
      </script>"""
    p, b, page = _page(body)
    data = {"facts": {"education": [{"major": "Computer Science", "degree": "Bachelor's"}]}}
    try:
        page.set_default_timeout(8000)
        assert fill_repeated(page, data) == []
        assert page.locator("#fos").get_attribute("data-picked") == "Computer Science"
        assert page.locator("#deg").inner_text() == "Bachelors"
    finally:
        b.close(); p.stop()


SPIN = """<input role=spinbutton aria-label={label} data-automation-id=dateSection{label}-input value="" data-max={size}>"""
SPIN_SCRIPT = """<script>
  // Workday spinbuttons (QTX live): typed digits set the value and a full month
  // advances to the year; programmatic fill is reverted; focusing the year box
  // directly bounces focus back to the month box.
  document.querySelectorAll('[role=spinbutton]').forEach(e => {
    e.dataset.state = e.value;
    e.addEventListener('keydown', k => { if (!/^[0-9]$/.test(k.key)) return; k.preventDefault();
      e.dataset.buf = ((e.dataset.buf || '') + k.key).slice(-Number(e.dataset.max)); e.value = e.dataset.state = String(Number(e.dataset.buf));
      const year = e.parentElement.querySelector('[aria-label=Year]');
      if (e.getAttribute('aria-label') === 'Month' && e.dataset.buf.length === 2) { window.advancing = true; year.focus(); window.advancing = false; } });
    e.addEventListener('input', () => { e.value = e.dataset.state; });
    e.addEventListener('focus', () => { e.dataset.buf = '';
      const month = e.parentElement.querySelector('[aria-label=Month]');
      if (e.getAttribute('aria-label') === 'Year' && month && !window.advancing) month.focus(); });
  });
  document.querySelectorAll('[aria-label=Calendar]').forEach(c => c.onclick = () => { window.calendarClicked = true; });
</script>"""


def test_workday_spinbutton_dates_are_typed_and_verified_not_picked_from_calendar():
    def date(group):
        return (f"<div aria-label='{group}'><div data-automation-id=dateInputWrapper>" + SPIN.format(label="Month", size=2) +
                SPIN.format(label="Year", size=4) + "</div><button aria-label=Calendar type=button></button></div>")
    body = ("""<section><h4>Work Experience</h4><button type=button>Add Another</button>
      <div><h5>Work Experience 1</h5><label>Job Title<input></label>""" + date("From") + date("To") +
            "</div></section>" + SPIN_SCRIPT)
    p, b, page = _page(body)
    data = {"facts": {"experience": [{"title": "Intern", "start_date": "2025-06", "end_date": "2025-08"}]}}
    try:
        # Start from wrong values, as left by the earlier calendar bug.
        page.evaluate("document.querySelectorAll('[role=spinbutton]').forEach(e => e.value = e.dataset.state = e.getAttribute('aria-label') === 'Month' ? '12' : '2012')")
        assert fill_repeated(page, data) == []
        values = page.locator("[role=spinbutton]").evaluate_all("es => es.map(e => e.value)")
        assert values == ["6", "2025", "8", "2025"]
        assert fill_repeated(page, data) == [], "second pass sees correct dates and changes nothing"
        assert page.evaluate("window.calendarClicked") is None
    finally:
        b.close(); p.stop()


def _education_with_prompt(selected=""):
    """QTX multiselect: results stay open until a click outside (Escape is ignored);
    an already-selected pill repeats the option text."""
    pill = (f"<div role=listbox data-automation-id=selectedItemList><div data-automation-id=selectedItem>"
            f"<div data-automation-id=promptOption>{selected}</div></div></div>" if selected else "")
    return f"""<h2>My Experience</h2><section><h4>Education</h4><button type=button>Add Another</button>
      <div><h5>Education 1</h5><input aria-label=RecordMarker style='display:none'>
      <div data-automation-id=formField-fieldOfStudy><label>Field of Study<input placeholder=Search id=fos></label>{pill}</div>
      <label>Degree<button aria-label=Degree aria-haspopup=listbox type=button id=deg>Select One</button></label></div></section>
      <script>
      window.searches = 0;
      fos.addEventListener('keydown', e => {{ if (e.key !== 'Enter') return; window.searches++;
        document.body.insertAdjacentHTML('beforeend', `<div id=overlay data-behavior-click-outside-close=topmost style="position:fixed;top:120px;left:0;right:0;bottom:0;background:#fff">
          <div role=listbox aria-label="Options Expanded"><div role=option>Computer Science</div><div role=option>Computer Science & Engineering</div></div></div>`);
        overlay.querySelectorAll('[role=option]').forEach(o => o.onclick = () => {{ fos.dataset.picked = o.textContent; fos.insertAdjacentHTML('afterend', '<div data-automation-id=selectedItem><p data-automation-id=promptOption>' + o.textContent + '</p></div>'); }});
      }});
      document.addEventListener('mousedown', e => {{ const o = document.getElementById('overlay'); if (o && !o.contains(e.target)) o.remove(); }}, true);
      deg.onclick = () => document.body.insertAdjacentHTML('beforeend', '<div role=listbox id=degopts><div role=option>Bachelors</div></div>');
      document.addEventListener('click', e => {{ if (e.target.closest('#degopts') && e.target.getAttribute('role') === 'option') {{
        deg.textContent = e.target.textContent; degopts.remove(); }} }});
      </script>"""


def test_prompt_that_ignores_escape_is_closed_by_clicking_outside():
    p, b, page = _page(_education_with_prompt())
    data = {"facts": {"education": [{"major": "Computer Science", "degree": "Bachelor's"}]}}
    try:
        page.set_default_timeout(8000)
        assert fill_repeated(page, data) == []
        assert page.locator("#fos").get_attribute("data-picked") == "Computer Science"
        assert page.locator("#deg").inner_text() == "Bachelors"
    finally:
        b.close(); p.stop()


def test_already_selected_field_of_study_is_not_searched_again():
    p, b, page = _page(_education_with_prompt(selected="Computer Science"))
    data = {"facts": {"education": [{"major": "Computer Science", "degree": "Bachelor's"}]}}
    try:
        page.set_default_timeout(8000)
        assert fill_repeated(page, data) == []
        assert page.evaluate("window.searches") == 0
        assert page.locator("#deg").inner_text() == "Bachelors"
    finally:
        b.close(); p.stop()


def test_section_titles_with_optional_suffix_are_recognized():
    body = """<section><h4>Work History (Optional)</h4><button type=button id=add>Add</button><div id=rows></div></section>
    <script>add.onclick=()=>{rows.innerHTML += %r;}</script>""" % ROW.replace("'", "\\'")
    p, b, page = _page(body)
    data = {"facts": {"experience": [{"title": "Engineer", "company": "Example", "location": "Remote", "bullets": ["Built tools"]}]}}
    try:
        assert fill_repeated(page, data) == []
        assert page.locator("input[name=title]").input_value() == "Engineer"
    finally:
        b.close(); p.stop()


def test_rbc_your_education_title_is_recognized():
    body = """<section><h4>Your Education</h4><button type=button id=add>Add</button><div id=rows></div></section>
    <script>add.onclick=()=>{rows.innerHTML += '<div><h5>Your Education 1</h5><label>School or University<input name=school required></label></div>';}</script>"""
    p, b, page = _page(body)
    try:
        assert fill_repeated(page, {"facts": {"education": [{"school": "Example State University - Springfield"}]}}) == []
        assert page.locator("input[name=school]").input_value() == "Example State University - Springfield"
    finally:
        b.close(); p.stop()


def test_rbc_your_experience_title_is_recognized_beside_the_my_experience_page_heading():
    body = """<h2>My Experience</h2>
    <section><h4>Your Experience</h4><button type=button id=add>Add</button><div id=rows></div></section>
    <script>add.onclick=()=>{rows.innerHTML += %r;}</script>""" % ROW.replace("'", "\\'")
    p, b, page = _page(body)
    data = {"facts": {"experience": [{"title": "Engineer", "company": "Example", "location": "Remote", "bullets": ["Built tools"]}]}}
    try:
        assert fill_repeated(page, data) == []
        assert page.locator("input[name=title]").input_value() == "Engineer"
    finally:
        b.close(); p.stop()


def test_degree_and_major_synonyms_pick_the_first_name_a_site_offers():
    body = """<section><h4>Education (Required)</h4><button type=button>Add Another</button>
      <div><h5>Education (Required) 1</h5><input aria-label=RecordMarker style='display:none'>
      <label>Degree<button aria-label=Degree aria-haspopup=listbox type=button id=deg>Select One</button></label></div></section>
      <script>
      deg.onclick = () => document.body.insertAdjacentHTML('beforeend', '<div role=listbox id=degopts><div role=option>BA</div><div role=option>BS</div></div>');
      document.addEventListener('click', e => { if (e.target.closest('#degopts') && e.target.getAttribute('role') === 'option') {
        deg.textContent = e.target.textContent; degopts.remove(); } });
      </script>"""
    p, b, page = _page(body)
    data = {"facts": {"education": [{"degree": "Bachelor's", "degree_synonyms": ["Bachelor's", "Bachelors", "BS"]}]}}
    try:
        page.set_default_timeout(8000)
        assert fill_repeated(page, data) == []
        assert page.locator("#deg").inner_text() == "BS"
    finally:
        b.close(); p.stop()


def test_websites_section_gets_github_and_linkedin_from_profile_links():
    row = "<div data-automation-id=editableListItem><label>URL<input name=url></label></div>"
    body = f"""<section><h4>Websites</h4><button type=button id=add>Add</button><div id=rows></div></section>
    <script>add.onclick=()=>{{rows.insertAdjacentHTML('beforeend', {row!r});}}</script>"""
    p, b, page = _page(body)
    data = {"facts": {"links": {"linkedin": "https://www.linkedin.com/in/alexrivera", "github": "https://github.com/alexrivera"}}}
    try:
        assert fill_repeated(page, data) == []
        urls = page.locator("input[name=url]").evaluate_all("es => es.map(e => e.value)")
        assert urls == ["https://github.com/alexrivera", "https://www.linkedin.com/in/alexrivera"]
        assert fill_repeated(page, data) == [], "second pass adds nothing"
        assert page.locator("input[name=url]").count() == 2
    finally:
        b.close(); p.stop()


def test_open_list_covering_the_heading_is_closed_without_clicking_an_option():
    # J&J: a field-of-study list stayed open over the page heading; clicking the
    # heading would have toggled an option instead.
    body = """<h2>My Experience</h2><button id=deg type=button>Select One</button>
    <div id=list data-automation-id=activeListContainer role=listbox style="position:absolute;left:0;top:0;width:300px;height:200px;background:#fff">
      <div role=option onclick="window.toggled=true">Computer and Information Science</div></div>
    <script>document.addEventListener('mousedown', e => { if (!list.contains(e.target)) list.remove(); }, true);</script>"""
    p, b, page = _page(body)
    try:
        close_prompts(page)
        assert page.locator("#list").count() == 0
        assert not page.evaluate("window.toggled === true")
    finally:
        b.close(); p.stop()


LANG_ROW = """<div><h5>Languages {n}</h5><input aria-label=RecordMarker style='display:none'>
  <label>Language<button aria-label=Language aria-haspopup=listbox type=button class=lang>Select One</button></label>
  <label><input type=checkbox class=fluent>I am fluent in this language.</label>
  <label>Overall<button aria-label=Overall aria-haspopup=listbox type=button class=level>Select One</button></label></div>"""


def test_languages_get_one_row_each_at_the_highest_level():
    body = """<section><h4>Languages</h4><button type=button id=add>Add</button><div id=rows></div></section>
      <script>
      let n = 0; const row = %r;
      add.onclick = () => { n += 1; rows.insertAdjacentHTML('beforeend', row.replace('{n}', n)); };
      document.addEventListener('click', e => {
        const b = e.target.closest('button[aria-haspopup]');
        if (b) { const opts = b.classList.contains('lang') ? ['English', 'Portuguese (Portugal)', 'Portuguese (Brazil)', 'French']
                                                           : ['Beginner', 'Intermediate', 'Advanced', 'Native'];
          window.open_for = b; document.body.insertAdjacentHTML('beforeend', '<div role=listbox id=pop>' + opts.map(o => '<div role=option>' + o + '</div>').join('') + '</div>'); return; }
        if (e.target.getAttribute('role') === 'option') { window.open_for.textContent = e.target.textContent; pop.remove(); }
      });
      </script>""" % LANG_ROW
    p, b, page = _page(body)
    level = ["Native", "Fluent", "Advanced"]
    data = {"facts": {"languages_rows": True, "languages": [
        {"names": ["English"], "fluent": True, "level": level},
        {"names": ["Portuguese", {"matches": "^portuguese(?!.*portugal)"}], "fluent": True, "level": level}]}}
    try:
        page.set_default_timeout(8000)
        assert fill_repeated(page, data) == []
        assert page.locator(".lang").all_inner_texts() == ["English", "Portuguese (Brazil)"]
        assert page.locator(".level").all_inner_texts() == ["Native", "Native"]
        assert all(box.is_checked() for box in page.locator(".fluent").all())
    finally:
        b.close(); p.stop()


def test_skills_prompt_adds_every_saved_skill_it_offers_and_skips_missing_ones():
    from job_agent.repeated_sections import search_any
    from job_agent.skills import choice
    body = """<div data-automation-id=multiSelectContainer><input placeholder=Search id=sk></div>
      <script>
      const offered = ['Python (Programming Language)', 'Python Frameworks', 'C++ (Programming Language)', 'Linux'];
      sk.addEventListener('keydown', e => { if (e.key !== 'Enter') return;
        const hits = offered.filter(o => o.toLowerCase().includes(sk.value.toLowerCase()));
        document.body.insertAdjacentHTML('beforeend', '<div role=listbox aria-label="Options Expanded" id=pop>' +
          hits.map(o => '<div role=option>' + o + '</div>').join('') + '</div>');
        pop.querySelectorAll('[role=option]').forEach(o => o.onclick = () => {
          sk.insertAdjacentHTML('afterend', '<div data-automation-id=selectedItem><p data-automation-id=promptOption>' + o.textContent + '</p></div>'); });
      });
      document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('pop')?.remove(); });
      </script>"""
    p, b, page = _page(body)
    skills = {"add_all": [choice("Python", "python"), choice("Rust", "rust"), choice("C++", r"c\+\+"), choice("Linux", "linux")], "max": 20}
    try:
        page.set_default_timeout(6000)
        assert search_any(page, page.locator("#sk"), skills)
        picked = page.locator("[data-automation-id=selectedItem]").all_inner_texts()
        assert sorted(picked) == ["C++ (Programming Language)", "Linux", "Python (Programming Language)"]
    finally:
        b.close(); p.stop()


def test_a_search_that_picks_its_only_result_on_enter_counts_as_filled():
    # NVIDIA's "How Did You Hear About Us?": Enter with a single hit selects it and shows no list.
    from job_agent.repeated_sections import search_any
    body = """<div data-automation-id=formField-source><div data-automation-id=multiSelectContainer><input placeholder=Search id=src></div></div>
      <script>
      src.addEventListener('keydown', e => { if (e.key !== 'Enter') return;
        if (src.value.toLowerCase() === 'nvidia.com') src.insertAdjacentHTML('afterend',
          '<div data-automation-id=selectedItem><p data-automation-id=promptOption>NVIDIA.COM</p></div>'); });
      </script>"""
    p, b, page = _page(body)
    try:
        page.set_default_timeout(6000)
        assert search_any(page, page.locator("#src"), [{"search": "NVIDIA.COM", "matches": r"^nvidia\.com$"}])
    finally:
        b.close(); p.stop()
