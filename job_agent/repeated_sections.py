"""Fill repeatable work, education, and website records from saved profile data."""
import re
import calendar
from .browser import LABEL
from .profile import normalize
from .custom_controls import FIELD_LABEL, option_match


def search_key(text):
    text = re.sub(r'\s+(?:not checked|checked)$', '', text, flags=re.I)
    return re.sub(r'\s*[-–—]\s*', '-', normalize(text))


# A point in empty page margin (nothing clickable, outside any open prompt); an
# "outside click" on a heading can land on a menu that covers it (J&J).
BLANK_POINT = """() => {
  const w = innerWidth, h = innerHeight;
  for (const [x, y] of [[3, h / 2], [w - 4, h / 2], [3, h - 4], [w / 2, h - 4], [w - 4, 80]]) {
    const e = document.elementFromPoint(x, y);
    if (!e || e.closest('a,button,input,textarea,select,label,[role=option],[role=listbox],[role=dialog],[data-behavior-click-outside-close],[data-automation-id*=prompt],[data-automation-id=menuItem]')) continue;
    return [x, y];
  }
  // Otherwise the page heading, only where nothing covers it.
  const heading = [...document.querySelectorAll('h2')].find(e => e.getClientRects().length);
  if (heading) {
    const r = heading.getBoundingClientRect(), x = r.left + Math.min(r.width / 2, 20), y = r.top + r.height / 2;
    if (heading.contains(document.elementFromPoint(x, y))) return [x, y];
  }
  return null;
}"""


def close_prompts(page):
    """Close open Workday prompts; some ignore Escape and close only on an outside click."""
    try:
        page.keyboard.press('Escape')
        prompts = page.locator('[data-behavior-click-outside-close]:visible, [role=dialog]:visible:has([data-automation-id*="promptOption"]), '
                               '[data-automation-id=activeListContainer]:visible')  # J&J field-of-study list
        if not prompts.count():
            return
        # Search dialogs (Workday Skills) close with their own Done button.
        done = prompts.first.get_by_role('button', name='Done', exact=True)
        if done.count():
            done.first.click(timeout=3000)
        elif point := page.evaluate(BLANK_POINT):
            page.mouse.click(*point)
        try:
            prompts.first.wait_for(state='hidden', timeout=3000)
        except Exception:
            # Nothing uncovered to click (J&J's narrow window): moving focus off
            # the search field closes its list.
            page.keyboard.press('Tab')
            prompts.first.wait_for(state='hidden', timeout=3000)
    except Exception:
        pass  # a prompt that stays open is caught by the fill that needs the field


def type_date(page, field, date):
    """Type a saved YYYY-MM date into a Workday month/year spinbutton pair and
    verify both parts. Focusing the year box directly bounces to the month box, so
    the whole date is typed from the month box, which advances to the year itself."""
    if not date:
        return False
    year, month = date.split('-')[:2]
    boxes = field.evaluate_handle("""e => { const w = e.closest('[data-automation-id=dateInputWrapper]') || e.parentElement;
      return [w.querySelector('[data-automation-id=dateSectionMonth-input]'), w.querySelector('[data-automation-id=dateSectionYear-input]')]; }""")
    month_box, year_box = boxes.get_property('0').as_element(), boxes.get_property('1').as_element()
    def correct():
        return ((month_box is None or month_box.input_value().isdigit() and int(month_box.input_value()) == int(month)) and
                (year_box is None or year_box.input_value().isdigit() and int(year_box.input_value()) == int(year)))
    if correct():
        return True
    first = month_box or year_box
    first.focus()
    page.keyboard.type((month.zfill(2) if month_box else '') + (year if year_box else ''))
    page.keyboard.press('Tab')
    return correct()


SELECTED_ITEMS = """e => { const f = e.closest('[data-automation-id^=formField]') || e.parentElement;
  return [...f.querySelectorAll('[data-automation-id=selectedItem] [data-automation-id=promptOption], [data-automation-id=selectedItem]')]
    .map(o => o.textContent.trim()).filter(Boolean); }"""


# Scroll a virtualized options list one screen down; false once it cannot move.
SCROLL_LIST = """e => {
  const up = []; for (let p = e.parentElement, i = 0; p && i < 4; p = p.parentElement, i++) up.push(p);
  const s = [e, ...e.querySelectorAll('*'), ...up].find(n => n.scrollHeight > n.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(n).overflowY));
  if (!s) return false;
  const before = s.scrollTop;
  s.scrollTop += Math.max(40, s.clientHeight * 0.8);
  return s.scrollTop !== before;
}"""


def search_any(page, field, choices):
    """Pick the first saved choice a search prompt offers, unless one is already selected.
    Every choice is checked against the selected items first (a saved draft's chip can
    render a moment after the page), so an earlier preference's search cannot toggle off
    a later preference that is already picked."""
    if isinstance(choices, dict) and "add_all" in choices:
        return add_all(page, field, choices["add_all"], choices.get("max", 20))
    choices = choices if isinstance(choices, list) else [choices]
    for attempt in range(2):
        selected = field.evaluate(SELECTED_ITEMS)
        if any(_wanted(choice)(item) for choice in choices for item in selected):
            return True
        if attempt == 0:
            page.wait_for_timeout(1000)
    for choice in choices:
        # Recheck all preferences before each search: a late-rendered chip from
        # the previous pick must stop the fallback list, even on multi-selects.
        if any(_wanted(saved)(item) for saved in choices for item in field.evaluate(SELECTED_ITEMS)):
            close_prompts(page)
            return True
        if fill_search(page, field, choice):
            return True
    return False


def add_all(page, field, choices, limit=20):
    """Multi-select prompts that take many picks (Workday Skills): add every saved choice the site
    offers, in the given order (the job's keywords first), up to `limit`. Missing ones are skipped."""
    added = 0
    for choice in choices:
        if added >= limit:
            break
        if any(_wanted(choice)(item) for item in field.evaluate(SELECTED_ITEMS)):
            added += 1
            continue
        try:
            if fill_search(page, field, choice):
                added += 1
        except Exception:
            close_prompts(page)  # this site has no such skill: move on
    close_prompts(page)
    return added > 0


def _category_aware(base):
    # A pick made inside a category list (KLA: Social Network > Linkedin) shows as the chip
    # "Social Network: Linkedin"; it is the saved "LinkedIn" once the prefix is dropped.
    return lambda text: base(text) or (": " in text and base(text.split(": ", 1)[1]))


def _wanted(value):
    pattern = re.compile(value["matches"], re.I) if isinstance(value, dict) and value.get("matches") else None
    return _category_aware((lambda text: bool(pattern.search(text))) if pattern else (lambda text: search_key(text) == search_key(str(value))))


def fill_search(page, field, value):
    # Search broadly enough to find campus names, then select only the exact
    # saved campus. Never choose the first result (often a different school).
    selected = field.evaluate(SELECTED_ITEMS)
    pattern = re.compile(value["matches"], re.I) if isinstance(value, dict) and value.get("matches") else None
    wanted = _wanted(value)
    if any(wanted(item) for item in selected):
        return True
    query = value.get("search", "") if pattern else re.split(r'\s*[-–—]\s*', str(value), maxsplit=1)[0]
    field.fill(query)
    field.press('Enter')
    # QTX names the results listbox "Options Expanded"; Cloudforce renders a plain
    # click-outside popup. Either holds the options for this search.
    popup = page.locator('[role=listbox][aria-label="Options Expanded"]:visible, '
                         '[data-behavior-click-outside-close]:visible:has([data-automation-id*="promptOption"])').first
    try:
        popup.wait_for(state='visible', timeout=5000)

        def visible_matches():
            options = popup.locator('[role="option"],[role="radio"],[data-automation-id*="promptOption"]').all()
            if not options:
                options = popup.get_by_text(re.compile(r'.+'), exact=False).all()
            found = [o for o in options if o.is_visible() and wanted(o.inner_text().strip())
                     and not o.evaluate("e => !!e.closest('[data-automation-id=selectedItem]')")]
            # An option wrapper and its inner promptOption are one choice: keep the innermost.
            return [o for o in found if not any(other != o and o.evaluate("(e, x) => e !== x && e.contains(x)", other.element_handle()) for other in found)]

        matches = visible_matches()
        # Long lists (field of study on several tenants) render only the rows in view
        # and typing does not filter them: scroll through looking for the saved choice.
        for _ in range(80):
            if matches or not popup.evaluate(SCROLL_LIST):
                break
            page.wait_for_timeout(150)
            matches = visible_matches()
        if len(matches) != 1:
            raise ValueError('saved choice is unavailable or ambiguous')
        target = matches[-1]
        if target.evaluate("e => { const o = e.closest('[role=option]'); const l = (o && o.getAttribute('aria-label')) || ''; "
                           "return /\\bchecked$/i.test(l) && !/not checked$/i.test(l); }"):
            close_prompts(page)  # already ticked: clicking again would untick it
            return True
        category = target.evaluate("e => !!e.closest('[data-uxi-multiselectlistitem-hassidecharm=true]')")
        name = search_key(target.inner_text())
        target.click(timeout=3000)
        if category:
            # A category (Athene "Website") opens its own list instead of being picked:
            # choose the one entry inside it that matches, else give up on this answer.
            page.wait_for_timeout(1000)
            leaves = [o for o in page.locator('[data-automation-id=promptOption]:visible').all()
                      if wanted(o.inner_text().strip()) and search_key(o.inner_text()) != name
                      and not o.evaluate("e => !!e.closest('[data-automation-id=selectedItem]')"
                                         " || !!e.closest('[data-uxi-multiselectlistitem-hassidecharm=true]')")]
            if len(leaves) != 1:
                raise ValueError('no matching choice inside the category')
            leaves[0].click(timeout=3000)
        # Multiselect prompts stay open after a pick and cover later fields.
        close_prompts(page)
        # Read back: the pick counts only once the field shows it as selected
        # (Meridian Bank renders the chip a second or more after the click, several
        # seconds while other applications run in parallel).
        for _ in range(30):
            page.wait_for_timeout(200)
            if any(wanted(item) for item in field.evaluate(SELECTED_ITEMS)):
                return True
        return False
    except Exception:
        close_prompts(page)
        # Some tenants (NVIDIA's source prompt) select a lone search hit on Enter and show no list:
        # the pick counts if the field now shows the saved choice.
        return any(wanted(item) for item in field.evaluate(SELECTED_ITEMS))


ROW_SELECTOR = '[data-automation-id="editableListItem"],[data-job-agent-entry]'
DATE_GROUP = """e=>{
  for(let p=e.parentElement;p && !p.hasAttribute('data-job-agent-record');p=p.parentElement){
    const text=(p.getAttribute('aria-label')||p.innerText||'').trim();
    const m=text.match(/^(From|Start(?: Date)?|To|End(?: Date)?)(?:\\s|\\*|$)/i);
    if(m) return m[1].split(/\\s/)[0].toLowerCase();
  } return '';
}"""


def fill_calendars(page, row, record, stop):
    gaps = []
    for button in row.query_selector_all('button'):
        if stop():
            break
        name = (button.get_attribute('aria-label') or button.inner_text()).strip()
        if name.lower() != 'calendar' or not button.is_visible():
            continue
        group = button.evaluate(DATE_GROUP)
        date = record.get('start_date') if group in {'from', 'start'} else record.get('end_date') if group in {'to', 'end'} else None
        if not date:
            continue
        year, month = map(int, date.split('-')[:2])
        button.click(timeout=5000)
        picker = page.get_by_role('list', name='Month Picker', exact=True)
        try:
            picker.wait_for(state='visible', timeout=3000)
            for _ in range(31):
                if stop():
                    break
                target = picker.get_by_role('button', name=re.compile(r'^(?:Selected )?' + calendar.month_name[month] + ' ' + str(year) + '$'))
                if target.count() == 1:
                    target.click(timeout=3000)
                    break
                labels = picker.get_by_role('button').evaluate_all("es=>es.map(e=>e.getAttribute('aria-label')||e.textContent)")
                years = {int(m.group()) for s in labels if (m := re.search(r'\b\d{4}\b', s))}
                if len(years) != 1:
                    raise ValueError('ambiguous calendar year')
                current = years.pop()
                nav = page.get_by_role('button', name='Previous Year' if current > year else 'Next Year', exact=True)
                if nav.count() != 1:
                    raise ValueError('ambiguous calendar navigation')
                nav.click(timeout=3000)
            else:
                raise ValueError('calendar navigation limit')
        except Exception:
            page.keyboard.press('Escape')
            gaps.append('Calendar could not set saved ' + group + ' date ' + date)
    if not gaps:
        row.evaluate("e=>e.setAttribute('data-job-agent-calendar-filled','true')")
    return gaps
DISCOVER_ROWS = """([section,title]) => {
  for (const h of section.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]')) {
    if (!new RegExp('^'+title+' [0-9]+$').test(h.textContent.trim())) continue;
    for(let p=h.parentElement;p && p!==section;p=p.parentElement) {
      if(p.querySelector('input,textarea,select')) {
        const numbered=[...p.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]')].filter(x=>new RegExp('^'+title+' [0-9]+$').test(x.textContent.trim()));
        if(numbered.length===1) p.setAttribute('data-job-agent-entry','true');
        break;
      }
    }
  }
  return section.querySelectorAll('[data-automation-id="editableListItem"],[data-job-agent-entry]').length;
}"""


def section_for(page, names):
    """(section, heading text) for the one heading named like names, e.g. Cloudforce's
    "Work History (Optional)" or RBC's "Your Education"; (None, None) when absent or ambiguous."""
    pattern = re.compile(r'^(?:(?:your|my) )?(?:' + '|'.join(map(re.escape, names)) + r')(?: \((?:optional|required)\))?$', re.I)
    headings = page.get_by_role('heading', name=pattern)
    if headings.count() != 1:
        return None, None
    title = headings.inner_text().strip()
    node = headings.element_handle()
    handle = node.evaluate_handle("""e => {
      for(let p=e.parentElement, i=0;p && i<5;p=p.parentElement,i++) {
        const add=[...p.querySelectorAll('button')].filter(b=>/^(add|add another)$/i.test(b.textContent.trim()));
        const foreign=[...p.querySelectorAll('h2,h3,h4')].filter(h=>h!==e && !h.textContent.trim().startsWith(e.textContent.trim()));
        if(add.length===1 && foreign.length===0) return p;
      } return null;
    }""")
    return handle.as_element(), title


def url_key(url):
    """Compare links loosely: scheme, www and a trailing slash do not matter."""
    return re.sub(r'^https?://(?:www\.)?', '', (url or '').strip().lower()).rstrip('/')


def row_url(row):
    fields = [f for f in row.query_selector_all('input') if f.is_visible() and f.get_attribute('type') not in {'hidden', 'checkbox', 'radio'}]
    return fields[0].input_value().strip() if fields else ''


# Proficiency dropdowns on a Workday language row ("Listening", "Oral/Speaking", "Overall", ...).
LANGUAGE_LEVELS = {'listening', 'oral/speaking', 'speaking', 'overall', 'reading', 'writing', 'comprehension',
                   'reading/writing', 'proficiency', 'proficiency level', 'level', 'language proficiency', 'fluency'}


LEVEL_ORDER = [r'^native\b|\bnative (?:language|speaker|or bilingual|/ ?bilingual)|^\d+\s*-\s*native', r'bilingual',
               r'^fluent\b|^\d+\s*-\s*fluent|\bfluent$', r'^expert\b|^\d+\s*-\s*expert', r'^advanced\b|^\d+\s*-\s*advanced',
               r'full professional', r'^professional\b', r'^proficient\b|^\d+\s*-\s*proficient']
LANGUAGE_WORDS = re.compile(r'^(?:english|chinese|mandarin|spanish|french|german|arabic|afrikaans|albanian)\b', re.I)


def best_level(labels):
    """Index of the highest proficiency offered; never a test or certification option."""
    usable = [(i, normalize(t)) for i, t in enumerate(labels) if not re.search(r'test|passed|certif', t, re.I)]
    for pattern in LEVEL_ORDER:
        hits = [i for i, t in usable if re.search(pattern, t)]
        if hits:
            return hits[0]
    return None


def fill_language_dropdown(page, button, record, title, index):
    """One dropdown of a language row: the language list gets the saved language, a proficiency
    list gets the highest level, a Yes/No list says whether the label names a high level."""
    label = normalize(button.evaluate(FIELD_LABEL))
    current = normalize(button.inner_text())
    if current not in {'select one', ''}:
        return []  # already chosen (a saved draft or an earlier pass)
    try:
        button.click(timeout=5000)
        popup = page.locator('[role=listbox]:visible:not([data-automation-id=selectedItemList])')
        popup.first.wait_for(timeout=5000)
        options = popup.first.locator('[role="option"],[role="radio"],[data-automation-id*="promptOption"]').all()
        labels = [o.inner_text().strip() for o in options]
        if sum(1 for t in labels if LANGUAGE_WORDS.search(t)) >= 2:
            chosen = option_match([{'label': t, 'value': str(i)} for i, t in enumerate(labels)], record.get('names') or record.get('language'))
            chosen = None if chosen is None else int(chosen)
        elif {normalize(t) for t in labels} <= {'yes', 'no'}:
            want = 'yes' if re.search(r'fluent|native|proficient|advanced|professional', label) else 'no'
            chosen = next((i for i, t in enumerate(labels) if normalize(t) == want), None)
        else:
            chosen = best_level(labels)
        if chosen is None:
            raise ValueError('no usable option')
        options[chosen].click(timeout=5000)
        return []
    except Exception:
        page.keyboard.press('Escape')
        return [f'{title}: entry {index + 1}: no usable option for {label or "a dropdown"}']


def record_answers(record, kind):
    if kind == 'languages':
        answers = {'language': record.get('names') or record.get('language'),
                   'i am fluent in this language': bool(record.get('fluent'))}
        answers.update({label: record.get('level') for label in LANGUAGE_LEVELS})
        return answers
    if kind == 'websites':
        return {'url': record['url'], 'website': record['url'],
                'website url': record['url'], 'web address': record['url']}
    if kind == 'experience':
        return {'job title': record.get('title'), 'company': record.get('company'),
                'company name': record.get('company'), 'location': record.get('location'),
                'role description': '\n'.join(record.get('bullets', [])),
                'description': '\n'.join(record.get('bullets', [])),
                'i currently work here': bool(record.get('current'))}
    return {'school or university': record.get('school_synonyms') or record.get('school'), 'school': record.get('school_synonyms') or record.get('school'),
            # Approved synonyms (ordered) cover sites that word the same degree/major differently.
            'university': record.get('school_synonyms') or record.get('school'), 'degree': record.get('degree_synonyms') or record.get('degree'),
            'field of study': record.get('major_synonyms') or record.get('major'), 'major': record.get('major_synonyms') or record.get('major'),
            'overall result (gpa)': record.get('gpa'), 'gpa': record.get('gpa')}


def fill_repeated(page, data, stop=lambda: False, emit=lambda text: None):
    """Only supports identifiable Workday editable-list items; reports unknown markup."""
    missing = []
    kinds = [(('Work Experience', 'Work History', 'Your Experience'), 'experience'), (('Education',), 'education'),
             (('Websites', 'Website'), 'websites')]
    # Language rows stay off until their markup is captured: tenants label them inconsistently
    # and long language lists render lazily (2026-09-24 regression).
    if data.get('facts', {}).get('languages_rows'):
        kinds.append((('Languages', 'Language'), 'languages'))
    for names, kind in kinds:
        records = data.get('facts', {}).get(kind, [])
        if kind == 'websites':
            links = data.get('facts', {}).get('links', {})
            records = [{'url': links[key]} for key in ('github', 'linkedin', 'portfolio')
                       if isinstance(links.get(key), str) and links[key].strip()]
            records = list({record['url']: record for record in records}.values())
        if not records:
            continue
        section, title = section_for(page, names)
        if section is None:
            continue
        page.evaluate(DISCOVER_ROWS, [section, re.escape(title)])
        rows = section.query_selector_all(ROW_SELECTOR)
        if kind == 'websites' and rows:
            # Saved links may already be present in any order (GM listed LinkedIn
            # first): line records up with the rows that already hold them.
            present = [url_key(v) for v in (row_url(r) for r in rows) if v]
            by_key = {url_key(r['url']): r for r in records}
            if present and all(k in by_key for k in present):
                ordered = [by_key[k] for k in present]
                records = ordered + [r for r in records if r not in ordered]
        if not rows and section.query_selector('input,textarea,select'):
            missing.append(title + ': existing entry has unsupported markup; preserving it')
            continue
        if not rows and section.get_attribute('data-job-agent-unrecognized-entry'):
            missing.append(title + ': added entry has unsupported markup; preserving it')
            continue
        if rows and len(rows) > len(records):
            missing.append(title + ': more existing entries than saved records; preserve for review')
            continue
        for index, record in enumerate(records):
            if stop():
                return missing
            rows = section.query_selector_all(ROW_SELECTOR)
            if index >= len(rows):
                buttons = [b for b in section.query_selector_all('button') if re.fullmatch(r'add(?: another)?', b.inner_text().strip(), re.I)]
                if len(buttons) != 1:
                    missing.append(title + ': no unique Add control')
                    break
                if buttons[0].evaluate("e=>!!e.form && e.type!=='button'"):
                    missing.append(title + ': Add control could submit form')
                    break
                buttons[0].click(timeout=5000)
                try:
                    page.wait_for_function("([section,title,n])=>(" + DISCOVER_ROWS + ")([section,title])>n", arg=[section, re.escape(title), index], timeout=5000)
                except Exception:
                    section.evaluate("e=>e.setAttribute('data-job-agent-unrecognized-entry','true')")
                    missing.append(title + ': added entry has unsupported markup; not adding again')
                    break
                rows = section.query_selector_all(ROW_SELECTOR)
            row = rows[index]
            row.evaluate("e=>e.setAttribute('data-job-agent-record','true')")
            answers = record_answers(record, kind)
            if kind == 'websites':
                # Preserve an existing manually entered link rather than assigning
                # another profile URL to it just because its row index matches.
                existing = [f.input_value().strip() for f in row.query_selector_all('input')
                            if f.is_visible() and f.get_attribute('type') not in {'hidden', 'checkbox', 'radio'}]
                if any(value and url_key(value) != url_key(record['url']) for value in existing):
                    missing.append(title + ': existing URL differs from saved record; preserving it')
                    continue
            # Typed month/year spinbuttons are exact; the calendar popup is only
            # for tenants whose date inputs are read-only.
            if not row.query_selector('input[role=spinbutton][data-automation-id^="dateSection"]:not([readonly])'):
                missing.extend(fill_calendars(page, row, record, stop))
            for field in row.query_selector_all('input,textarea,select'):
                if stop():
                    return missing
                if not field.is_visible() or not field.is_enabled():
                    continue
                label = normalize(field.evaluate(LABEL))
                component = normalize(field.get_attribute('aria-label') or '')
                if component in {'month', 'year'}:
                    label = component
                typ = field.get_attribute('type') or 'text'
                if typ in {'hidden', 'button', 'submit', 'radio', 'file'}:
                    continue
                value = answers.get(label)
                date = None
                if value is None:
                    group = field.evaluate("""e=>{
                      for(let p=e.parentElement;p && !p.hasAttribute('data-job-agent-record');p=p.parentElement){
                        const text=(p.getAttribute('aria-label')||p.innerText||'').trim();
                        const match=text.match(/^(From|Start(?: Date)?|To|End(?: Date)?)(?:\\s|\\*|$)/i);
                        if(match) return match[1].split(/\\s/)[0].toLowerCase();
                      } return '';
                    }""")
                    date = record.get('start_date') if group in {'from','start'} else record.get('end_date') if group in {'to','end'} else None
                    if date and label in {'month', 'year'}:
                        value = date.split('-')[1 if label == 'month' else 0]
                if value is not None:
                    if field.get_attribute('placeholder') == 'Search' and label in {'school or university', 'school', 'university', 'field of study', 'major', 'language'}:
                        if not search_any(page, field, value):
                            missing.append(title + ': saved search choice unavailable for ' + label)
                    elif typ == 'checkbox':
                        field.set_checked(bool(value))
                    elif field.evaluate('e=>e.tagName') == 'SELECT':
                        options = field.query_selector_all('option')
                        # Synonym lists (school, degree): the first name this select offers.
                        names = [v for v in value if isinstance(v, str)] if isinstance(value, list) else [str(value)]
                        matches = next(([o for o in options if normalize(o.inner_text()) == normalize(name)] for name in names
                                        if any(normalize(o.inner_text()) == normalize(name) for o in options)), [])
                        if len(matches) == 1:
                            field.select_option(value=matches[0].get_attribute('value'))
                        else:
                            missing.append(title + ': unsupported option for ' + label)
                    elif label in {'month', 'year'} and field.get_attribute('role') == 'spinbutton' and field.get_attribute('readonly') is None:
                        if not type_date(page, field, date):
                            missing.append(title + ': entry ' + str(index + 1) + ' date did not stick: ' + label)
                    elif isinstance(value, list) and not (value := next((v for v in value if isinstance(v, str)), None)):
                        missing.append(title + ': no text value for ' + label)
                    elif field.input_value() != str(value):
                        if label in {'month', 'year'} and field.input_value().isdigit() and str(value).isdigit() and int(field.input_value()) == int(value):
                            pass
                        elif field.get_attribute('readonly') is not None:
                            missing.append(title + ': calendar-only field needs verification: ' + label)
                        else:
                            field.fill(str(value))
                            field.press('Tab')
                calendar_filled = row.get_attribute('data-job-agent-calendar-filled') == 'true'
                if field.evaluate("e=>e.required && !e.checkValidity()") and not (calendar_filled and label in {'month', 'year'}):
                    missing.append(title + ': entry ' + str(index + 1) + ' needs ' + label)
            if kind == 'websites':
                url_fields = [f for f in row.query_selector_all('input,textarea')
                              if f.is_visible() and normalize(f.evaluate(LABEL)) in answers]
                if not url_fields or any(url_key(f.input_value()) != url_key(record['url']) for f in url_fields):
                    missing.append(title + ': URL did not stick or no supported URL field')
            for button in row.query_selector_all('button[aria-haspopup=listbox]'):
                if not button.is_visible():
                    continue
                label = normalize(button.evaluate(FIELD_LABEL))
                value = answers.get(label)
                if kind == 'languages':
                    # Tenants label these rows inconsistently (the "Language" label often sits on a
                    # proficiency list): decide from the options themselves.
                    missing.extend(fill_language_dropdown(page, button, record, title, index))
                    continue
                if value is None:
                    if normalize(button.inner_text()) in {'select one', ''}:
                        missing.append(title + ': entry ' + str(index + 1) + ' needs custom option ' + label)
                    continue
                if normalize(button.inner_text()) == normalize(str(value)):
                    continue
                if button.evaluate("e=>!!e.form && e.type!=='button'"):
                    missing.append(title + ': unsafe dropdown trigger')
                    continue
                try:
                    button.click(timeout=5000)
                except Exception:
                    # Something (an open prompt) intercepted the click: close it, retry once.
                    close_prompts(page)
                    try:
                        button.click(timeout=5000)
                    except Exception:
                        missing.append(title + ': dropdown blocked for ' + label)
                        continue
                # Selected multiselect pills are also role=listbox; they are not popups.
                popup = page.locator('[role=listbox]:visible:not([data-automation-id=selectedItemList])')
                try:
                    popup.first.wait_for(timeout=5000)
                    if popup.count() != 1:
                        raise ValueError('ambiguous options')
                    options = popup.locator('[role="option"],[role="radio"],[data-automation-id*="promptOption"]').all()
                    if not options:
                        options = popup.get_by_text(re.compile(r'.+'), exact=False).all()
                    chosen = option_match([{'label': o.inner_text(), 'value': str(i)} for i,o in enumerate(options)], value)
                    if chosen is None and label == 'degree' and normalize(str(value)) in {"bachelor's", 'bachelor’s'}:
                        chosen = option_match([{'label': o.inner_text(), 'value': str(i)} for i,o in enumerate(options)], 'Bachelors')
                    if chosen is None:
                        raise ValueError('option unavailable')
                    options[int(chosen)].click(timeout=5000)
                except Exception:
                    page.keyboard.press('Escape')
                    missing.append(title + ': saved option unavailable for ' + label)
            emit(title + ': processed saved entry ' + str(index + 1))
    return sorted(set(missing))
