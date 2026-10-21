"""Deterministic form actions built from saved answers, never model guesses."""
from dataclasses import dataclass, field
import calendar
import re
from .browser import LABEL
from .profile import normalize


@dataclass
class Action:
    id: str
    description: str
    kind: str
    control: object = field(repr=False)
    value: object = field(default=None, repr=False)


ALIASES = {
    'address line 1': ('street address', 'address', 'street address line 1', 'address 1'),
    'address line 2': ('apartment suite unit', 'apartment', 'address 2', 'street address line 2'),
    'city': ('town', 'city or town'),
    'state': ('state province', 'state / province', 'province', 'region'),
    'postal code': ('zip', 'zip code', 'zip postal code', 'zip / postal code', 'postcode'),
    'country': ('country region', 'country / region'),
    'graduation date': ('expected graduation', 'expected graduation date', 'anticipated graduation date'),
}
ADDRESS_KEYS = {'address line 1': 'line1', 'address line 2': 'line2', 'city': 'city',
                'state': 'state', 'postal code': 'postal_code', 'country': 'country'}
GROUP_LABEL = """e => {
  const group = e.closest('fieldset,[role=group]');
  if (!group) return '';
  if (group.tagName === 'FIELDSET') return group.querySelector('legend')?.textContent.trim() || '';
  return group.getAttribute('aria-label') || (group.getAttribute('aria-labelledby') || '').split(/\\s+/)
    .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
}"""


def saved_value(profile, label, choice=False):
    key = normalize(label)
    for canonical, alternatives in ALIASES.items():
        if key == canonical or key in alternatives:
            names = (canonical, *alternatives)
            # An exact answer beats a choice pattern on another alias (rubrik: a date vs "May 2028" rules).
            exact = [name for name in names if profile.lookup(name, False) is not None]
            values = [profile.lookup(name, choice) for name in (exact or names)]
            known = [value for value in values if value is not None]
            if known and any(value != known[0] for value in known):
                raise ValueError('Conflicting saved answers for ' + canonical)
            if known:
                return known[0]
            address = profile.data.get('facts', {}).get('address', {})
            if canonical in ADDRESS_KEYS and isinstance(address, dict):
                return address.get(ADDRESS_KEYS[canonical])
    return profile.lookup(label, choice)


def graduation_parts(profile):
    value = saved_value(profile, 'graduation date')
    match = re.fullmatch(r'(\d{4})-(\d{2})(?:-(\d{2}))?', str(value or ''))
    if not match:
        return None
    year, month, day = match.groups()
    if not 1 <= int(month) <= 12:
        return None
    if day and not 1 <= int(day) <= calendar.monthrange(int(year), int(month))[1]:
        return None
    return year, month, day


def field_value(profile, label, group='', choice=False):
    value = saved_value(profile, label, choice)
    if value is not None:
        return value
    combined = normalize(group + ' ' + label)
    if 'graduat' not in combined:
        return None
    parts = graduation_parts(profile)
    if not parts:
        return None
    label_text = normalize(label)
    # "Graduation Date (Month/Year)" wants both parts, not just the year (Avis).
    if re.search(r'\bmonth\b', label_text) and re.search(r'\byear\b', label_text) and not re.search(r'\bday\b', label_text):
        return f'{parts[1]}/{parts[0]}'
    for component, value in zip(('year', 'month', 'day'), parts):
        if re.search(r'\b' + component + r'\b', normalize(label)):
            return value
    return None


def option_match(options, value, *, month=False):
    """Return a unique option value; ambiguous aliases are not selected."""
    acceptable = {normalize(str(value))}
    if month and str(value).isdigit() and 1 <= int(value) <= 12:
        number = int(value)
        acceptable.update({str(number), f'{number:02}', calendar.month_name[number].lower(), calendar.month_abbr[number].lower()})
    # Exact visible labels first; numeric option values can be zero-indexed.
    matches = [item for item in options if not item.get('disabled') and normalize(item['label']) in acceptable]
    if not matches:
        matches = [item for item in options if not item.get('disabled') and str(item['value']) == str(value)]
    return matches[0]['value'] if len(matches) == 1 else None


def field_actions(page, profile):
    actions, missing = [], []
    def add(description, kind, control, value=None):
        actions.append(Action('field_' + str(len(actions)), description[:100], kind, control, value))

    controls = page.locator('input,textarea,select,[role=combobox],button[aria-haspopup=dialog]')
    for control in controls.all():
        kind = (control.get_attribute('type') or 'text').lower()
        tag = control.evaluate('e=>e.tagName')
        is_calendar = tag == 'BUTTON' and control.get_attribute('aria-haspopup') == 'dialog'
        if kind in {'hidden', 'reset', 'password'} or (kind == 'submit' and not is_calendar):
            continue
        if not control.is_visible() or not control.is_enabled():
            continue
        if control.get_attribute('data-automation-id') == 'beecatcher':
            continue
        role = control.get_attribute('role')
        is_calendar = tag == 'BUTTON' and control.get_attribute('aria-haspopup') == 'dialog'
        if (kind == 'button' or tag == 'BUTTON') and role != 'combobox' and not is_calendar:
            continue
        label = control.evaluate(LABEL)
        group = control.evaluate(GROUP_LABEL)
        required = control.get_attribute('required') is not None or control.get_attribute('aria-required') == 'true'
        value = field_value(profile, label, group)
        is_month = 'graduat' in normalize(group + ' ' + label) and 'month' in normalize(label)

        if is_calendar:
            if 'graduat' not in normalize(label):
                if required:
                    missing.append(label + ' (unsupported calendar)')
                continue
            parts = graduation_parts(profile)
            if not parts:
                missing.append(label + ' (saved date required)')
                continue
            # Calendar adapters require an explicitly associated dialog.
            dialog_id = control.get_attribute('aria-controls')
            if not dialog_id:
                missing.append(label + ' (calendar has no associated dialog)')
                continue
            dialog = page.locator('[role=dialog]').filter(visible=True)
            matching = [item for item in dialog.all() if item.get_attribute('id') == dialog_id]
            if not matching:
                # A displayed month/year can complete a month-precision question.
                displayed = normalize(control.inner_text())
                expected = normalize(calendar.month_name[int(parts[1])] + ' ' + parts[0])
                if displayed == expected or displayed == f'{parts[0]}-{parts[1]}':
                    continue
                add('Open graduation date picker', 'click', control)
                continue
            target = matching[0]
            years = target.get_by_role('combobox', name='Year', exact=True)
            months = target.get_by_role('combobox', name='Month', exact=True)
            if years.count() != 1 or months.count() != 1:
                missing.append(label + ' (calendar needs supported month/year selectors)')
                continue
            for picker, selected, month in ((years, parts[0], False), (months, parts[1], True)):
                if picker.evaluate('e=>e.tagName') != 'SELECT':
                    missing.append(label + ' (custom calendar selector unsupported)')
                    continue
                options = picker.locator('option').evaluate_all('(es)=>es.map(e=>({label:e.textContent,value:e.value,disabled:e.disabled}))')
                chosen = option_match(options, selected, month=month)
                if chosen is None:
                    missing.append(label + ' (calendar option unavailable)')
                elif picker.input_value() != chosen:
                    add('Set graduation ' + ('month' if month else 'year'), 'select_value', picker, chosen)
            if actions or missing:
                continue
            if parts[2] is None:
                # Only an explicit month-precision commit is supported without a day.
                apply = target.get_by_role('button', name='Use this month', exact=True)
                if apply.count() == 1 and apply.is_enabled():
                    add('Confirm graduation month and year', 'click', apply)
                else:
                    missing.append(label + ' (calendar requires a day; only month/year is known)')
            else:
                day_name = f'{calendar.month_name[int(parts[1])]} {int(parts[2])}, {parts[0]}'
                days = target.get_by_role('button', name=day_name, exact=True)
                if days.count() == 1 and days.is_enabled():
                    add('Choose saved graduation date', 'click', days)
                else:
                    missing.append(label + ' (unrecognized day selector)')
            continue

        if kind in {'radio', 'file'}:
            if required:
                missing.append(label or kind)
            continue
        if value is None:
            if required:
                missing.append(label or 'Unlabelled required field')
            continue
        if role == 'combobox' and tag != 'SELECT':
            current = control.input_value() if tag == 'INPUT' else control.inner_text()
            if normalize(current) == normalize(str(value)) and control.get_attribute('aria-expanded') != 'true':
                continue
            owner = control.get_attribute('aria-controls') or control.get_attribute('aria-owns')
            lists = [item for item in page.locator('[role=listbox]:visible').all() if item.get_attribute('id') in (owner or '').split()]
            if control.get_attribute('aria-expanded') != 'true':
                add('Open saved option: ' + label, 'click', control)
            elif len(lists) == 1:
                options = lists[0].get_by_role('option').all()
                texts = [{'label': item.inner_text(), 'value': str(index), 'disabled': item.get_attribute('aria-disabled') == 'true'} for index, item in enumerate(options)]
                chosen = option_match(texts, value, month=is_month)
                if chosen is None:
                    missing.append(label + ' (saved option unavailable)')
                else:
                    add('Choose saved option: ' + label, 'click', options[int(chosen)])
            else:
                missing.append(label + ' (dropdown has no unique associated listbox)')
            continue
        if kind == 'checkbox':
            if not isinstance(value, bool):
                missing.append(label + ' (boolean required)')
                continue
            if control.is_checked() != value:
                add('Fill saved answer: ' + label, 'checkbox', control, value)
        elif tag == 'SELECT':
            options = control.locator('option').evaluate_all('(es)=>es.map(e=>({label:e.textContent,value:e.value,disabled:e.disabled}))')
            chosen = option_match(options, value, month=is_month)
            if chosen is None:
                missing.append(label + ' (saved option unavailable)')
            elif control.input_value() != chosen:
                add('Choose saved answer: ' + label, 'select_value', control, chosen)
        else:
            if kind == 'date' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
                missing.append(label + ' (full date required; no day inferred)')
                continue
            if kind == 'month' and not re.fullmatch(r'\d{4}-\d{2}', str(value)):
                missing.append(label + ' (month/year required)')
                continue
            if kind not in {'text', 'email', 'tel', 'url', 'number', 'date', 'month', 'search'} and tag != 'TEXTAREA':
                if required:
                    missing.append(label + ' (unsupported control)')
                continue
            if control.input_value() != str(value):
                add('Fill saved answer: ' + label, 'fill', control, value)
        if not actions and not control.evaluate('e=>e.checkValidity()'):
            missing.append(label + ' (validation failed)')
    return actions, sorted(set(missing))
