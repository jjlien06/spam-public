"""Workday-style ARIA controls: radio groups, listbox buttons, and pill widgets.

Selectors follow markup captured from a live myworkdayjobs tenant and are
exercised by local fixtures. Answers come only from the saved profile.
"""
import re
from playwright.sync_api import TimeoutError as BrowserTimeout
from .browser import LABEL
from .form_controls import field_value
from .profile import normalize

PLACEHOLDERS = {"", "select one", "select", "choose", "choose one", "please select"}
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# Visible field name: <label for>, then aria-labelledby, then aria-label minus
# Workday's appended placeholder/required words, then the enclosing fieldset's
# legend (questionnaire items). Trailing asterisks are dropped.
FIELD_LABEL = """e => {
  const clean = t => (t || '').replace(/\\s+/g, ' ').replace(/\\s*\\*+\\s*$/, '').trim();
  const doc = e.ownerDocument;
  if (e.id) { const l = doc.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) return clean(l.textContent); }
  const ids = (e.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
  if (ids.length) return clean(ids.map(id => doc.getElementById(id)?.textContent || '').join(' '));
  const aria = clean((e.getAttribute('aria-label') || '').replace(/\\b(Select One|Required)\\b/g, ''));
  if (aria) return aria;
  const fieldset = e.closest('fieldset');
  const legend = fieldset?.querySelector('legend');
  if (legend) return clean(legend.textContent);
  // Ashby autocomplete (Frostline): <fieldset> or <div class=…field-entry><label for="uuid-of-nothing">Degree</label> … one input.
  const entry = fieldset || e.closest('.ashby-application-form-field-entry');
  const own = entry?.querySelector(':scope > label');
  const controls = entry ? entry.querySelectorAll('input:not([type=hidden]),select,textarea').length : 0;
  return own && controls === 1 ? clean(own.textContent) : '';
}"""

# Question for a radio: enclosing legend, else the group's aria-labelledby text.
GROUP_LABEL = """e => {
  const clean = t => (t || '').replace(/\\s+/g, ' ').replace(/\\s*\\*+\\s*$/, '').trim();
  const doc = e.ownerDocument;
  const group = e.closest('fieldset,[role=radiogroup],[role=group],[data-automation-id^="formField"]');
  if (!group) {
    // Lever: <li class=application-question><div class=application-label>Question ✱</div> … radios in labels.
    const question = e.closest('li.application-question')?.querySelector('.application-label');
    if (!question) return '';
    const copy = question.cloneNode(true);
    copy.querySelectorAll('.required').forEach(n => n.remove());
    return clean(copy.textContent);
  }
  const legend = group.querySelector('legend');
  if (legend) return clean(legend.textContent);
  // Ashby: <fieldset><label>Question</label>…radios</fieldset>. A labelled ancestor
  // outside the group (the whole form, "Application") names the form, not this question.
  const own = group.querySelector(':scope > label');
  if (own && !own.contains(e)) return clean(own.textContent);
  const labelled = e.closest('[aria-labelledby]');
  const onlyThis = el => [...el.querySelectorAll('input[type=radio]')].every(r => r.name === e.name);
  if (labelled && (labelled === group || group.contains(labelled) || onlyThis(labelled))) return clean(labelled.getAttribute('aria-labelledby').split(/\\s+/).map(id => doc.getElementById(id)?.textContent || '').join(' '));
  return clean(group.getAttribute('aria-label') || group.querySelector('label')?.textContent || '');
}"""

# Required: the control's own attributes, its own <label for>, its Workday
# formField wrapper (one field each), or — for radios only — the group legend.
# A plain <fieldset> shared by several fields says nothing about any one of them.
REQUIRED = """e => {
  if (e.required || e.getAttribute('aria-required') === 'true') return true;
  const star = el => !!el && /\\*\\s*$/.test(el.textContent.trim());
  if (e.id && star(e.ownerDocument.querySelector('label[for="' + CSS.escape(e.id) + '"]'))) return true;
  const wrapper = e.closest('[data-automation-id^="formField"]');
  if (wrapper && (wrapper.querySelector('[aria-required="true"]') || star(wrapper.querySelector('legend,label')))) return true;
  // Ashby marks a required question on its fieldset's own label (class _required_…).
  const entry = e.closest('fieldset, .ashby-application-form-field-entry');
  const ashby = entry?.querySelector(':scope > label');
  if (ashby && /_required/.test(ashby.className) && entry.querySelectorAll('input:not([type=hidden]),select,textarea').length === 1) return true;
  if (e.type === 'radio') {
    const group = e.closest('fieldset,[role=radiogroup]');
    if (group && (group.querySelector('[aria-required="true"]') || star(group.querySelector('legend')))) return true;
  }
  return /\\bRequired\\b/.test(e.getAttribute('aria-label') || '');
}"""

# A pill widget's search box stays empty even when the field holds a selection.
TOKEN_FILLED = """e => !!e.closest('[data-automation-id=multiselectInputContainer],[data-automation-id=multiSelectContainer]')
  ?.querySelector('[data-automation-id=selectedItem]')"""


def option_match(options, value):
    """Unique option value for a saved answer, or None; never a partial guess.

    Exact label match first; then the answer as a whole-word prefix of exactly one
    option (long disclosure texts); then a US state abbreviation for its full name.
    """
    if isinstance(value, dict) and value.get("matches"):
        # A pattern answer (e.g. "the company's careers site") picks the one option
        # it matches; two or more matches abstain.
        pattern = re.compile(value["matches"], re.IGNORECASE)
        hits = [item for item in options if not item.get("disabled") and normalize(item["label"]) not in PLACEHOLDERS
                and pattern.search(item["label"])]
        if value.get("first") and hits:
            return hits[0]["value"]  # "any of these is fine": the first the site lists
        return hits[0]["value"] if len(hits) == 1 else None
    if isinstance(value, list):
        # Ordered preferences: the first one this site offers wins.
        return next((found for found in (option_match(options, choice) for choice in value) if found is not None), None)
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    wanted = normalize(str(value))
    live = [item for item in options if not item.get("disabled")]
    exact = [item for item in live if normalize(item["label"]) == wanted]
    if len(exact) == 1:
        return exact[0]["value"]
    # A saved number (e.g. GPA 3.8) picks the one numeric range that contains it.
    if not exact and re.fullmatch(r"\d+(?:\.\d+)?", wanted):
        number = float(wanted)
        def contains(label):
            # Money ranges too: "$55,000 - $65,000", "50,000 to 75,000", "Up to $40,000", "$200,000+".
            text = re.sub(r"(?<=\d),(?=\d{3})", "", normalize(label)).replace("$", "").strip()
            if m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:[-–]|to)\s*(\d+(?:\.\d+)?)", text):
                return float(m.group(1)) <= number <= float(m.group(2))
            if m := re.fullmatch(r"(?:below|under|less than|up to)\s+(\d+(?:\.\d+)?)", text):
                return number < float(m.group(1))
            if m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:or higher|or above|\+|and above)", text):
                return number >= float(m.group(1))
            return False
        ranges = [item for item in live if contains(item["label"])]
        if len(ranges) == 1:
            return ranges[0]["value"]
        if ranges:
            return None
    # A bare Yes/No matches the single option phrased "No, never held ..." / "Yes - ...".
    if not exact and wanted in {"yes", "no"}:
        lead = [item for item in live if re.match(wanted + r"\b[\s,\-–:]", normalize(item["label"]) + " ")]
        if len(lead) == 1:
            return lead[0]["value"]
        if lead:
            return None
    # "Category - Value" choices (e.g. "Job Board - LinkedIn"): the whole value
    # after the category must equal the saved answer, in exactly one option.
    if not exact and wanted:
        tail = [item for item in live if " - " in item["label"] and normalize(item["label"].rsplit(" - ", 1)[1]) == wanted]
        if len(tail) == 1:
            return tail[0]["value"]
    # Workday tenants use inconsistent punctuation for the same degree choice.
    degree_aliases = {
        "bachelor's": "bachelors", "bachelor’s": "bachelors",
        "bachelor degree": "bachelors", "bachelors degree": "bachelors",
    }
    alias = degree_aliases.get(wanted)
    if alias:
        degree_matches = [item for item in live if normalize(item["label"]) == alias]
        if len(degree_matches) == 1:
            return degree_matches[0]["value"]
    if exact or not wanted:
        return None
    # Only a long saved answer (a disclosure sentence) may match as a prefix; a short
    # value like "1" must not pick "1-2 years".
    def whole_prefix(label):
        text = normalize(label)
        return text.startswith(wanted) and not text[len(wanted):len(wanted) + 1].isalnum()
    prefixed = [item for item in live if whole_prefix(item["label"])] if len(wanted) >= 12 else []
    if len(prefixed) == 1:
        return prefixed[0]["value"]
    if prefixed:
        return None
    state = US_STATES.get(str(value).strip().upper())
    if state:
        return option_match(options, state)
    return None


def fill_radio_groups(frame, profile, should_stop, on_event):
    filled, missing = 0, []
    groups = {}
    for radio in frame.locator("input[type=radio]").element_handles():
        if not radio.is_visible() or not radio.is_enabled():
            continue
        groups.setdefault((radio.get_attribute("name"), radio.evaluate(GROUP_LABEL)), []).append(radio)
    for (name, question), radios in groups.items():
        if should_stop():
            break
        if name in ("icims_f_Veteran", "icims_f_Disability"):
            continue  # unlabeled radios: icims.fill_veteran_form answers them by value
        checked = [index for index, radio in enumerate(radios) if radio.is_checked()]
        options = [{"label": radio.evaluate(LABEL), "value": str(index)} for index, radio in enumerate(radios)]
        remember_options(profile, question, [option["label"] for option in options])
        value = profile.lookup(question, choice=True) if question else None
        if value is None:
            if not checked and any(radio.evaluate(REQUIRED) for radio in radios):
                missing.append(question or name or "Unlabelled radio group")
            elif checked and question.rstrip().endswith("?") and not radios[0].evaluate("e=>!!e.closest('[data-job-agent-record]')"):
                missing.append(question + " (filled in by the site; no saved answer)")  # never keep a choice nobody approved
            continue
        if value == []:
            # An empty saved answer (a per-posting variant) means "leave this optional group blank".
            if not checked and any(radio.evaluate(REQUIRED) for radio in radios):
                missing.append(question or name or "Unlabelled radio group")
            continue
        chosen = option_match(options, value)
        if chosen is None:
            if not checked:
                missing.append(question + " (saved answer does not match a radio option)")
            continue
        if int(chosen) in checked:
            continue
        # A saved answer corrects a different earlier choice (a stale draft answer).
        try:
            radios[int(chosen)].check(timeout=5000)
        except BrowserTimeout:
            from .repeated_sections import close_prompts
            close_prompts(frame.page)
            radios[int(chosen)].check(timeout=5000)
        filled += 1
        on_event("Answered: " + question)
    return filled, missing


def remember_options(profile, label, labels):
    """Keep the choices of a question SPAM could not answer, for the answer deck."""
    seen = getattr(profile, "seen_options", None)
    if seen is not None:
        seen[label] = [text.strip() for text in labels if normalize(text) not in PLACEHOLDERS]


def record_options(frame, button, label, profile):
    """Open an unanswered dropdown just long enough to read its options."""
    if getattr(profile, "seen_options", None) is None or button.evaluate("e => !!e.form && e.type !== 'button'"):
        return
    popup = frame.locator("[role=listbox]:not([data-automation-id=selectedItemList]):visible")
    try:
        button.click(timeout=3000)
        popup.first.wait_for(state="visible", timeout=3000)
        remember_options(profile, label, popup.first.locator("[role=option]").all_inner_texts())
    except BrowserTimeout:
        pass
    finally:
        frame.page.keyboard.press("Escape")


def fill_listbox_buttons(frame, profile, should_stop, on_event):
    filled, missing = 0, []
    for button in frame.locator("button[aria-haspopup=listbox]").element_handles():
        if should_stop():
            break
        if not button.is_visible() or not button.is_enabled():
            continue
        label = button.evaluate(FIELD_LABEL)
        if not label:
            continue  # e.g. header menus; not a form field
        current = normalize(button.inner_text())
        if normalize(label) == current and current not in PLACEHOLDERS:
            # The "label" is the chosen answer (a saved draft). Read the real question so a wrong
            # answer left in a draft (J&J: "No internship experience") is corrected, not kept.
            question = button.evaluate("""e => { const f = e.closest('[data-automation-id^=formField]');
              const l = f && f.querySelector('label, legend'); return l ? l.innerText.replace(/\\*\\s*$/, '').trim() : ''; }""")
            if not question or normalize(question) == current:
                continue
            label = question
        value = field_value(profile, label, choice=True)
        if button.evaluate("e=>!!e.closest('[data-job-agent-record]')"):
            value = None
        if value is None:
            if current in PLACEHOLDERS and button.evaluate(REQUIRED):
                missing.append(label)
                record_options(frame, button, label, profile)
            elif current not in PLACEHOLDERS and label.rstrip().endswith("?") and not button.evaluate("e=>!!e.closest('[data-job-agent-record]')"):
                missing.append(label + " (filled in by the site; no saved answer)")  # a draft or the site chose it (HPE citizenship YES)
            continue
        if current not in PLACEHOLDERS and option_match([{"label": button.inner_text(), "value": "x"}], value) == "x":
            continue
        # A trigger without type=button inside a <form> is an implicit submit button.
        if button.evaluate("e => !!e.form && e.type !== 'button'"):
            missing.append(label + " (dropdown trigger would submit the form)")
            continue
        popup = frame.locator("[role=listbox]:not([data-automation-id=selectedItemList]):visible")
        try:
            # A previous popup can linger while it closes; never open two at once.
            if popup.count():
                frame.page.keyboard.press("Escape")
                popup.first.wait_for(state="hidden", timeout=5000)
            button.click(timeout=5000)
            popup.first.wait_for(state="visible", timeout=5000)
        except BrowserTimeout:
            missing.append(label + " (dropdown did not open)")
            continue
        if popup.count() != 1:
            frame.page.keyboard.press("Escape")
            missing.append(label + " (dropdown has no unique list)")
            continue
        items = popup.first.locator("[role=option]").all()
        options = [{"label": item.inner_text(), "value": str(index), "disabled": item.get_attribute("aria-disabled") == "true"}
                   for index, item in enumerate(items)]
        chosen = option_match(options, value)
        if chosen is None:
            remember_options(profile, label, [o["label"] for o in options])
            frame.page.keyboard.press("Escape")
            missing.append(label + " (saved answer not among options)")
            continue
        expected = normalize(options[int(chosen)]["label"])
        try:
            items[int(chosen)].click(timeout=5000)
            frame.wait_for_function("([el, text]) => (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase().replace(/[ *:؟?.]+$/, '') === text",
                                    arg=[button, expected], timeout=5000)
        except BrowserTimeout:
            missing.append(label + " (selection did not apply)")
            continue
        try:
            popup.first.wait_for(state="hidden", timeout=5000)
        except BrowserTimeout:
            frame.page.keyboard.press("Escape")
        filled += 1
        on_event("Selected: " + label)
    return filled, missing


# Search-and-pick comboboxes (Greenhouse's react-select, and ARIA comboboxes
# like it): a text input with role=combobox whose options appear in a listbox
# only while it is open. Workday's pill prompts are handled by fill_search.
COMBOBOX_STATE = """e => {
  const shell = e.closest('.select__control, [class*=select__control], [class*=-control]') || e.parentElement;
  const single = shell && shell.querySelector('[class*=single-value], [class*=singleValue]');
  const multi = shell ? [...shell.querySelectorAll('[class*=multi-value__label], [class*=multiValue] [class*=label]')].map(x => x.textContent.trim()) : [];
  // Ashby autocomplete and Oracle's cx-select keep the chosen option in the input itself (no single-value element).
  const typed = e.classList.contains('ashby-application-form-input-autocomplete') || e.classList.contains('cx-select-input')
    ? (e.value || '').trim() : '';
  return {value: single ? single.textContent.trim() : (multi.join('; ') || typed), values: multi, multi: multi.length > 0,
          controls: e.getAttribute('aria-controls') || '', expanded: e.getAttribute('aria-expanded') === 'true'};
}"""


def _combobox_options(frame, box):
    """Visible options of an open combobox: [{label, value, disabled}] with value = index."""
    state = box.evaluate(COMBOBOX_STATE)
    listbox = frame.locator(f"[id='{state['controls']}']") if state["controls"] else None
    if listbox is None or not listbox.count():
        listbox = frame.locator("[role=listbox]:visible:not([data-automation-id=selectedItemList])")
    # Oracle's cx-select lists its choices as grid cells (role=grid > row > gridcell).
    found = listbox.first.locator("[role=option], [role=gridcell]").all() if listbox.count() else []
    items, texts = [], []
    for item in found:
        try:
            text = " ".join(item.inner_text().split())
        except Exception:
            continue
        if normalize(text) in {normalize(t) for t in texts}:
            continue  # the same place listed twice (Greenhouse location search) is one choice
        items.append(item)
        texts.append(text)
    return items, [{"label": t, "value": str(i), "disabled": t.lower() in {"no options", "loading..."}} for i, t in enumerate(texts)]


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
# Greenhouse's education block: controls named by id (school--0, start-month--0 …)
# take the saved education record, never a global answer that could also match
# an employment section's "Start date month".
RECORD_ID = re.compile(r"^(school|degree|discipline|start-month|start-year|end-month|end-year)--(\d+)$")


# Its employment block (company-name-0, start-date-month-0 …) requires an end date on
# every row, so rows take completed roles in saved order.
EMPLOYMENT_ID = re.compile(r"^(company-name|title|start-date-month|start-date-year|end-date-month|end-date-year)-(\d+)$")


def record_value(control_id, profile):
    facts = (getattr(profile, "data", {}) or {}).get("facts", {})
    if match := EMPLOYMENT_ID.match(control_id or ""):
        records = [r for r in facts.get("experience", []) if r.get("end_date")]
        if int(match.group(2)) >= len(records):
            return None
        field, record = match.group(1), records[int(match.group(2))]
        if field in ("company-name", "title"):
            return record.get("company" if field == "company-name" else "title")
        year, month = record["start_date" if field.startswith("start") else "end_date"].split("-")[:2]
        return MONTHS[int(month) - 1] if field.endswith("month") else year
    match = RECORD_ID.match(control_id or "")
    records = facts.get("education", [])
    if not match or int(match.group(2)) >= len(records):
        return None
    field, record = match.group(1), records[int(match.group(2))]
    if field == "school":
        school = record.get("school") or ""
        first = school.split(" - ")[0]
        choices = [school, {"search": first, "matches": "^" + re.escape(school.lower()).replace("\\ \\-\\ ", "\\W+") + "$"}]
        if record.get("school_domain"):
            # Ashby's school search lists "Example State UniversityUnited Statesexample.edu": name, country, domain.
            choices.append({"search": first, "matches": "^" + re.escape(first.lower()) + r"\W*united states\W*" + re.escape(record["school_domain"]) + "$"})
        return choices
    if field == "degree":
        return record.get("degree_synonyms") or record.get("degree")
    if field == "discipline":
        return record.get("major_synonyms") or record.get("major")
    date = record.get("start_date" if field.startswith("start") else "end_date") or ""
    if not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", date):
        return None
    year, month = date.split("-")[:2]
    return MONTHS[int(month) - 1] if field.endswith("month") else year


def _pick_combobox(frame, box, preference):
    """Try one saved preference on an open-able combobox; True once it is selected."""
    search = preference.get("search") if isinstance(preference, dict) else None
    box.click(timeout=5000)
    if search:
        box.fill("")
        box.type(search, delay=30)  # async searches (school, location) only react to key presses
    frame.page.wait_for_timeout(700)
    items, options = _combobox_options(frame, box)
    for _ in range(12 if search else 6):
        if [o for o in options if not o["disabled"]]:
            break
        frame.page.wait_for_timeout(400)  # results (and some plain lists, QV Trading's Degree) arrive late
        items, options = _combobox_options(frame, box)
    chosen = option_match(options, preference) if options else None
    if chosen is None and options and not search and not isinstance(preference, (dict, bool)) and len(options) >= 25:
        # Long lists (countries) are filtered by typing, then matched exactly.
        box.fill(str(preference)[:40])
        frame.page.wait_for_timeout(900)
        items, options = _combobox_options(frame, box)
        chosen = option_match(options, preference) if options else None
    if chosen is None:
        if search or len(options) >= 25:
            box.fill("")  # leave no half-typed search behind
        frame.page.keyboard.press("Escape")
        return False, options
    label = options[int(chosen)]["label"]
    items[int(chosen)].click(timeout=5000)
    frame.page.wait_for_timeout(300)
    state = box.evaluate(COMBOBOX_STATE)
    if any(option_match([{"label": item, "value": "selected"}], preference) == "selected" for item in state["values"]):
        frame.page.keyboard.press("Escape")
        return True, options
    selected = normalize(state["value"])
    # The box may show only part of the choice (Greenhouse's phone country: "+1").
    return bool(selected) and (selected in normalize(label) or normalize(label) in selected), options


def fill_comboboxes(frame, profile, should_stop, on_event):
    """Answer comboboxes, including ones an earlier answer reveals (Greenhouse shows
    "Please identify your race" only after "Hispanic/Latino?" is answered)."""
    filled, missing, done = 0, [], set()
    for _ in range(3):
        boxes = [b for b in frame.locator("input[role=combobox]").element_handles() if b.evaluate("e => e.id || e.name") not in done]
        if not boxes:
            break
        count, gaps = _fill_boxes(frame, profile, should_stop, on_event, boxes)
        done.update(b.evaluate("e => e.id || e.name") for b in boxes)
        filled += count
        missing += gaps
    return filled, missing


MULTI_PROMPT = re.compile(r"select up to|all that apply|check all|select all", re.I)


def _fill_boxes(frame, profile, should_stop, on_event, boxes):
    filled, missing = 0, []
    for box in boxes:
        if should_stop():
            break
        if not box.is_visible() or box.evaluate("e => !!e.closest('[data-automation-id=multiSelectContainer]')"):
            continue
        label = box.evaluate(FIELD_LABEL)
        if (box.get_attribute("id") or "").startswith("country-codes-dropdown"):
            label = "Phone country code"  # Oracle labels this box by its row ("Phone Number")
        if not label:
            continue
        value = record_value(box.get_attribute("id"), profile)
        if value is None:
            value = field_value(profile, label, choice=True)
        state = box.evaluate(COMBOBOX_STATE)
        current = state["value"]
        required = box.evaluate(REQUIRED)
        if value == []:
            # An empty saved answer (a per-posting variant) means "leave this optional box blank".
            if required and not current:
                missing.append(label)
            continue
        if value is None:
            if current and label.rstrip().endswith("?") and not box.evaluate("e=>!!e.closest('[data-job-agent-record]')"):
                missing.append(label + " (filled in by the site; no saved answer)")
            elif required and not current:
                missing.append(label)
                try:
                    box.click(timeout=3000)
                    frame.page.wait_for_timeout(700)
                    remember_options(profile, label, [o["label"] for o in _combobox_options(frame, box)[1]])
                except BrowserTimeout:
                    pass
                finally:
                    frame.page.keyboard.press("Escape")
            continue
        preferences = value if isinstance(value, list) else [value]
        selected_values = state["values"] or ([current] if current else [])
        if any(option_match([{"label": item, "value": "x"}], p) == "x" for item in selected_values for p in preferences):
            continue
        seen, picked_any = [], False
        try:
            for preference in preferences:
                ok, options = _pick_combobox(frame, box, preference)
                seen = seen or options
                if ok:
                    filled += 1
                    on_event("Selected: " + label)
                    # Multi-select prompts ("select up to three", "all that apply") take every saved choice.
                    if box.evaluate(COMBOBOX_STATE)["multi"] and MULTI_PROMPT.search(label):
                        picked_any = True
                        continue
                    break
            else:
                if picked_any:
                    continue
                remember_options(profile, label, [o["label"] for o in seen])
                missing.append(label + " (saved answer not among options)")
        except BrowserTimeout:
            frame.page.keyboard.press("Escape")
            missing.append(label + " (dropdown did not open)")
    return filled, missing


# Ashby's Yes/No questions: a label and a row of toggle buttons (aria-pressed),
# backed by a hidden checkbox. Each group is one question.
TOGGLE_GROUPS = """() => {
  const out = [];
  for (const row of document.querySelectorAll('div:has(> button[aria-pressed])')) {
    const buttons = [...row.querySelectorAll(':scope > button[aria-pressed]')];
    if (buttons.length < 2 || !(row.offsetWidth || row.offsetHeight)) continue;
    const field = row.closest('[data-field-path], fieldset, .field-wrapper') || row.parentElement;
    const label = field.querySelector('label, legend');
    const question = (label ? label.textContent : '').replace(/\\s+/g, ' ').replace(/\\s*\\*+\\s*$/, '').trim();
    if (!question) continue;
    out.push({question, options: buttons.map(b => b.innerText.trim()), pressed: buttons.findIndex(b => b.getAttribute('aria-pressed') === 'true')});
  }
  return out;
}"""


def fill_toggle_buttons(frame, profile, should_stop, on_event):
    filled, missing = 0, []
    for group in frame.evaluate(TOGGLE_GROUPS):
        if should_stop():
            break
        question = group["question"]
        value = field_value(profile, question, choice=True)
        options = [{"label": text, "value": str(i)} for i, text in enumerate(group["options"])]
        if value is None:
            if group["pressed"] < 0:
                missing.append(question)  # Ashby requires these (its submit says "Missing entry")
                remember_options(profile, question, group["options"])
            continue
        chosen = option_match(options, value)
        if chosen is None:
            if group["pressed"] < 0:
                missing.append(question + " (saved answer not among options)")
                remember_options(profile, question, group["options"])
            continue
        if group["pressed"] == int(chosen):
            continue
        field = frame.locator("[data-field-path], fieldset, .field-wrapper").filter(has=frame.get_by_text(question, exact=True))
        target = field.last.locator("button[aria-pressed]").nth(int(chosen))
        try:
            target.click(timeout=5000)
        except BrowserTimeout:
            missing.append(question + " (button did not respond)")
            continue
        if target.get_attribute("aria-pressed") != "true":
            missing.append(question + " (did not stick)")
            continue
        filled += 1
        on_event("Answered: " + question)
    return filled, missing


ASHBY_EDUCATION = "[data-field-path=_systemfield_education_history] .ashby-application-form-input-education-entry"


def fill_ashby_education(frame, profile, on_event=lambda text: None):
    """Ashby's Education History block (Ledgerly): school search, degree and field-of-study text, and
    start/end month and year selects, all from the saved education record. The entry is marked as a
    record so the generic pass leaves it alone."""
    entries = frame.locator(ASHBY_EDUCATION)
    records = (getattr(profile, "data", {}) or {}).get("facts", {}).get("education", [])
    if not entries.count() or not records:
        return 0, []
    entry, record, filled, missing = entries.first, records[0], 0, []
    entry.evaluate("e => e.setAttribute('data-job-agent-record', 'true')")
    school = entry.locator("input[role=combobox]").first
    if school.count() and not school.input_value().strip():
        names = {normalize(record.get("school") or ""), normalize((record.get("school") or "").split(" - ")[0])}
        # Ashby's box shows the picked school's name; the react-select readback does not apply here.
        if any(_pick_combobox(frame, school, choice)[0] or normalize(school.input_value()) in names
               for choice in record_value("school--0", profile)):
            filled += 1
        else:
            missing.append("Education: school not among the site's options")
    for field_id, key in (("degree", "degree"), ("major", "major")):
        box = entry.locator(f"#_systemfield_education_history-{field_id}")
        synonyms = [v for v in (record.get(key + "_synonyms") or [record.get(key)]) if isinstance(v, str)]
        if box.count() and synonyms and box.input_value().strip() not in synonyms:
            box.fill(synonyms[0])
            filled += 1
    for group, key in (("startDate", "start_date"), ("endDate", "end_date")):
        date = record.get(key) or ""
        selects = entry.locator(f"[id='_systemfield_education_history-{group}'] select")
        if not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", date) or selects.count() != 2:
            continue
        year, month = date.split("-")[:2]
        month_box, year_box = selects.all()
        offered = year_box.evaluate("e => [...e.options].map(o => o.value)")
        if year not in offered:
            # Ledgerly lists end years only up to next year, and Ashby pre-fills dates it parsed from the
            # resume: never leave a wrong year. Clear the end date and say "still a student" instead.
            for box in (month_box, year_box):
                box.evaluate("""e => { const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
                  set.call(e, ''); e.dispatchEvent(new Event('change', {bubbles: true})); }""")
            student = entry.locator("input[type=checkbox]")
            if group == "endDate" and student.count() == 1:
                if not student.is_checked():
                    student.check(force=True)
                filled += 1
            elif any(b.input_value() for b in (month_box, year_box)) or group != "endDate":
                missing.append(f"Education: {group} {year} not offered")
            continue
        for select, value in zip((month_box, year_box), (str(int(month)), year)):
            if select.input_value() != value:
                select.select_option(value=value)
                filled += 1
    return filled, missing


def fill_date_pickers(frame, profile, on_event=lambda text: None):
    """react-datepicker text boxes ("Pick date..."). Only the graduation date is a known fact; it is
    typed as MM/DD/YYYY, confirmed with Enter, and read back."""
    filled, missing = 0, []
    records = (getattr(profile, "data", {}) or {}).get("facts", {}).get("education", [])
    for box in frame.locator(".react-datepicker__input-container input").all():
        if not box.is_visible() or box.input_value().strip():
            continue
        label = normalize(box.evaluate("""e => { const f = e.closest('.ashby-application-form-field-entry, [class*=field]');
          const t = f && f.querySelector('label, .ashby-application-form-question-title'); return t ? t.textContent : ''; }""")
                          or box.evaluate(FIELD_LABEL) or "")
        end = (records[0].get("end_date") or "") if records else ""
        if not (re.search(r"graduat", label) and re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", end)):
            continue  # not a date we know: the generic pass reports it
        parts = end.split("-")
        text = f"{parts[1]}/{parts[2] if len(parts) > 2 else '01'}/{parts[0]}"
        box.click()
        box.press("Meta+A")
        box.type(text, delay=40)
        box.press("Enter")
        frame.page.keyboard.press("Escape")
        if box.input_value().strip() == text:
            filled += 1
        else:
            missing.append(f"{label}: date {text} did not stick")
    return filled, missing


def fill_lever_location(frame, profile, on_event):
    """Lever's "Current location": type the city, then pick the matching place from its dropdown (the typed text
    alone is rejected: the form sends the picked place's hidden JSON)."""
    box = frame.locator("input[data-qa=location-input]")
    if not box.count() or not box.first.is_visible() or frame.locator("#selected-location").first.input_value():
        return 0, []
    box = box.first
    label = box.evaluate(LABEL)
    preferences = field_value(profile, "Location (City)", choice=True)
    for preference in preferences if isinstance(preferences, list) else [preferences]:
        if not isinstance(preference, dict) or not preference.get("search"):
            continue
        box.click(timeout=5000)
        box.fill("")
        box.type(preference["search"], delay=30)  # the search only reacts to key presses
        places = frame.locator(".dropdown-results .dropdown-location")
        for _ in range(15):
            frame.page.wait_for_timeout(300)
            if places.count():
                break
        options = [{"label": " ".join(t.split()), "value": str(i)} for i, t in enumerate(places.all_inner_texts())]
        chosen = option_match(options, preference) if options else None
        if chosen is not None:
            places.nth(int(chosen)).click(timeout=5000)
            on_event(f"{label}: {options[int(chosen)]['label']}")
            return 1, []
    box.fill("")
    return 0, [label]


def fill_custom_controls(frame, profile, should_stop=lambda: False, on_event=lambda text: None):
    """Answer radio groups, listbox buttons, comboboxes and toggle buttons; return (filled, missing)."""
    place, gaps_place = fill_lever_location(frame, profile, on_event)
    education, gaps_edu = fill_ashby_education(frame, profile, on_event)
    dates, gaps_date = fill_date_pickers(frame, profile, on_event)
    radios, missing = fill_radio_groups(frame, profile, should_stop, on_event)
    missing = gaps_edu + gaps_date + missing
    buttons, more = fill_listbox_buttons(frame, profile, should_stop, on_event)
    boxes, rest = fill_comboboxes(frame, profile, should_stop, on_event)
    toggles, gaps = fill_toggle_buttons(frame, profile, should_stop, on_event)
    return place + education + dates + radios + buttons + boxes + toggles, gaps_place + missing + more + rest + gaps
