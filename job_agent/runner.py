"""Bounded preparation loop. Submission and account mutations are not implemented."""
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, Error as BrowserError, TimeoutError as BrowserTimeout
from .browser import LABEL
from .custom_controls import REQUIRED, TOKEN_FILLED, fill_custom_controls, option_match
from .form_controls import field_value
from .profile import Profile, normalize


NEXT = {"next", "continue", "save and continue", "update profile"}  # iCIMS saves its profile step with "Update Profile"
SUBMIT = {"submit", "submit application", "send application", "apply"}
CONTROL_COUNT = "() => document.querySelectorAll('input,select,textarea,button,[role=option]').length"
# Form controls proper (header menus excluded): an empty page is not ready to act on.
FORM_CONTROL_COUNT = "() => document.querySelectorAll('input:not([type=hidden]),select,textarea,button[aria-haspopup=listbox]').length"
# Loading placeholders: aria-busy regions, spinners, or a leaf whose text is just
# "Loading". Presence is enough; skeleton screens hide that text visually.
LOADING = """() => !!document.querySelector('[aria-busy=true],[data-automation-id*="loading" i],[data-automation-id*="spinner" i]') ||
  [...document.querySelectorAll('div,span,p,li')].some(e => e.childElementCount === 0 && /^loading(\\.\\.\\.|…)?$/i.test(e.textContent.trim()))"""
from .workday import TRANSIENT_ERROR, open_application


def live_frames(page):
    """Frames that can still be read: not detached by a navigation, not a captcha widget."""
    return [f for f in page.frames if not f.is_detached() and not re.search(r"recaptcha|hcaptcha|challenges\.cloudflare", f.url or "")]


# Forward controls ("Save and Continue", "Next", "Submit"): is one usable, is one only pending?
# Workday disables its footer button while it saves a step (seconds, on NVIDIA's My Experience)
# and renders the next step's button hidden and disabled before enabling it.
FORWARD_STATE = """() => {
  const forward = /^(next|continue|save and continue|submit|submit application)$/;
  const buttons = [...document.querySelectorAll('button,input[type=submit],input[type=button],[role=button]')]
    .filter(b => forward.test((b.getAttribute('aria-label') || b.value || b.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase()));
  const usable = b => !b.disabled && b.getAttribute('aria-disabled') !== 'true' && !!(b.offsetWidth || b.offsetHeight || b.getClientRects().length);
  return buttons.some(usable) ? 'ready' : buttons.length ? 'pending' : 'none';
}"""


def wait_for_forward_control(page, timeout_ms=30000):
    """While every forward control on the page is disabled or hidden, wait (bounded) for one to become usable.

    Returns True only when it had to wait and a usable control then appeared."""
    deadline = time.monotonic() + timeout_ms / 1000
    waited = False
    while True:
        try:
            states = [frame.evaluate(FORWARD_STATE) for frame in live_frames(page)]
        except BrowserError:
            states = ["pending"]  # a frame went away mid-read: the page is changing
        if "ready" in states:
            return waited
        if "pending" not in states or time.monotonic() >= deadline:
            return False
        waited = True
        page.wait_for_timeout(250)


def settle(page, quiet_ms=600, timeout_ms=45000, empty_grace_ms=3000, loading_hold_ms=20000):
    """Wait until no form controls have been added or removed for quiet_ms.

    Hiring sites stream fields in after the step heading; observing early
    misses fields and lets index-based locators drift as nodes are inserted.
    A page with no form controls at all is given empty_grace_ms to render some.
    """
    started = time.monotonic()
    deadline = started + timeout_ms / 1000
    quiet_since, last = started, None
    while time.monotonic() < deadline:
        frames = live_frames(page)
        try:
            counts = [frame.evaluate(CONTROL_COUNT) for frame in frames]
            # A permanent hidden spinner template must not hold the page forever.
            loading = time.monotonic() - started < loading_hold_ms / 1000 and any(frame.evaluate(LOADING) for frame in frames)
            empty = not any(frame.evaluate(FORM_CONTROL_COUNT) for frame in frames)
        except BrowserError:
            page.wait_for_timeout(250)  # a frame went away mid-read (navigation); look again
            continue
        if counts != last or loading or (empty and time.monotonic() - started < empty_grace_ms / 1000):
            last, quiet_since = counts, time.monotonic()
        elif time.monotonic() - quiet_since >= quiet_ms / 1000:
            return True
        page.wait_for_timeout(100)
    return False


# Labels a file input can be known by, plus its section heading and whether the
# enclosing field is marked required. Drop-zone inputs are hidden, so the
# accessible name usually lives on the container rather than the input.
FILE_CONTEXT = """e => {
  const clean = t => (t || '').replace(/\\s+/g, ' ').replace(/\\s*\\*+\\s*$/, '').trim();
  const doc = e.ownerDocument;
  const byIds = ids => (ids || '').split(/\\s+/).filter(Boolean).map(id => doc.getElementById(id)?.textContent || '').join(' ');
  const container = e.closest('[aria-labelledby],[data-automation-id^="formField"],fieldset');
  // Greenhouse's new boards label every file box "Attach" and name it in "<id>-label"
  // ("Undergraduate Transcript*", Verkana).
  const idLabel = e.id ? doc.getElementById(e.id + '-label')?.textContent : '';
  const raw = [idLabel, byIds(e.getAttribute('aria-labelledby')), e.getAttribute('aria-label'),
               [...(e.labels || [])].map(l => l.textContent).join(' '),
               container && byIds(container.getAttribute('aria-labelledby')),
               container && container.querySelector('legend,label')?.textContent];
  let heading = '';
  for (let node = e; node && !heading; node = node.parentElement) {
    for (let prev = node.previousElementSibling; prev && !heading; prev = prev.previousElementSibling) {
      const found = prev.matches('h1,h2,h3,h4,h5,h6') ? prev : [...prev.querySelectorAll('h1,h2,h3,h4,h5,h6')].pop();
      if (found) heading = clean(found.textContent);
    }
  }
  const scope = e.closest('[data-automation-id^="formField"],fieldset,form') || e.parentElement;
  // The box's own name (its label/aria), before any container or heading text.
  const own = raw.slice(0, 4).map(clean).find(Boolean) || '';
  return {labels: raw.map(clean).filter(Boolean), heading, own,
          required: e.required || e.getAttribute('aria-required') === 'true' || raw.some(t => /\\*\\s*$/.test((t || '').trim())),
          text: clean(scope ? scope.innerText : ''), files: e.files.length};
}"""
# Field-level errors always count; generic live regions only when they read as
# a problem, since sites also announce successes (uploads) through role=alert.
ALERTS = """() => [...document.querySelectorAll('[data-automation-id=inputAlert],[role=alert],[class*=helper-text--error]')]
  .filter(e => e.offsetWidth || e.offsetHeight || e.getClientRects().length)
  .map(e => [e.matches('[data-automation-id=inputAlert]'), e.innerText.replace(/\\s+/g, ' ').trim()])
  .filter(([field, text]) => text && (field || (!/success/i.test(text) && /error|required|invalid|must |cannot|unable|fail/i.test(text))))
  .map(([, text]) => text.replace(/^error\\s*[:\\-]\\s*/i, ''))"""


def required_transcript(page):
    """Recognize a required transcript upload without mistaking optional text.

    Hidden file inputs are normal for drop zones; use their local labels rather
    than the whole page, which may mention transcripts only as an option.
    """
    for frame in live_frames(page):
        for control in frame.locator('input[type=file]:not(:disabled)').all():
            context = control.evaluate(FILE_CONTEXT)
            names = context["labels"] + [context["heading"]]
            if context["required"] and any(re.search(r"\btranscripts?\b", name, re.I) for name in names):
                return True
    return False


# Workday lists an uploaded file with its size ("55.57 KB"); a same-named file of another
# size is an older version of the document, so delete it before uploading the current one.
DELETE_STALE_UPLOAD = """(e, [name, size]) => {
  const scope = e.closest('[data-automation-id^="formField"],fieldset,form') || e.parentElement;
  for (const item of scope.querySelectorAll('[data-automation-id=file-upload-item]')) {
    if (item.querySelector('[data-automation-id=file-upload-item-name]')?.textContent.trim() !== name) continue;
    const shown = item.innerText.match(/([\\d.]+)\\s*(KB|MB)\\b/);
    const kb = shown && parseFloat(shown[1]) * (shown[2] === 'MB' ? 1024 : 1);
    const remove = item.querySelector('[data-automation-id=delete-file]');
    if (kb === null || Math.abs(kb - size / 1024) < (shown[2] === 'MB' ? 10.24 : 0.01) || !remove) return false;
    remove.click();
    return true;
  }
  return false;
}"""


# Workday keeps earlier uploads in the field: after the current file is attached, an older resume under another
# name (Resume_Old.pdf beside Resume.pdf) would go out too.
DELETE_OTHER_UPLOADS = """(e, name) => {
  const scope = e.closest('[data-automation-id^="formField"],fieldset,form') || e.parentElement;
  let removed = 0;
  for (const item of scope.querySelectorAll('[data-automation-id=file-upload-item]')) {
    const shown = item.querySelector('[data-automation-id=file-upload-item-name]')?.textContent.trim();
    const remove = item.querySelector('[data-automation-id=delete-file]');
    if (shown && shown !== name && remove) { remove.click(); removed++; }
  }
  return removed;
}"""


def _drop_other_uploads(control, name):
    if not control.evaluate(DELETE_OTHER_UPLOADS, name):
        return None
    others = "(e, name) => [...(e.closest('[data-automation-id^=formField],fieldset,form') || e.parentElement)" \
             ".querySelectorAll('[data-automation-id=file-upload-item-name]')].some(n => n.textContent.trim() !== name)"
    deadline = time.monotonic() + 15
    while control.evaluate(others, name):
        if time.monotonic() > deadline:
            return "older upload could not be removed"
        time.sleep(0.25)
    return None


def upload_document(control, documents):
    """Attach the saved document for a file input. Returns (filled, missing_note)."""
    context = control.evaluate(FILE_CONTEXT)
    candidates = context["labels"] + ([context["heading"]] if context["heading"] else [])
    document = next((documents[normalize(text)] for text in candidates if normalize(text) in documents), None)
    # A generic "Upload a file" slot gets the resume only when nothing names another
    # document (e.g. a "Cover Letter" heading) or the slot itself says resume/CV.
    # A box whose own label names another document (Ashby's "Cover Letter" under an
    # "Autofill from resume" heading) never takes the resume.
    own = context.get("own") or ""
    if normalize(own) not in documents and re.search(r"cover|letter|transcript|portfolio|writing sample|reference|photo|certificat", own, re.IGNORECASE) \
            and not re.search(r"resume|\bcv\b", own, re.IGNORECASE):
        if context["required"] and context["files"] == 0:
            return 0, f"{own} (no saved document)"
        return 0, None
    names_resume = any(re.search(r"resume|\bcv\b", text, re.IGNORECASE) for text in candidates)
    names_other = any(re.search(r"cover|letter|transcript|portfolio|writing sample|reference|photo|certificat", text, re.IGNORECASE) for text in candidates)
    # A generic "Upload a file" slot takes the resume unless its label or heading
    # names another document (a heading can be just a note about file names).
    generic_only = any(re.search(r"upload a file", text, re.IGNORECASE) for text in context["labels"])
    # A slot that names the resume takes it even when it also mentions a cover
    # letter ("Resume/Cover Letter"); only an other-document-only slot is refused.
    if document is None and (names_resume or (generic_only and not names_other)):
        document = documents.get("resume")
    shown = candidates[0] if candidates else "Unlabelled file"
    if document is None:
        if context["required"] and context["files"] == 0:
            section = context["heading"] or shown
            return 0, f"{shown} (no saved document for {section})"
        return 0, None
    if not document.is_file():
        return 0, shown + " (file unavailable)"
    if document.name in context["text"] and control.evaluate(DELETE_STALE_UPLOAD, [document.name, document.stat().st_size]):
        gone = "(e, name) => ![...(e.closest('[data-automation-id^=formField],fieldset,form') || e.parentElement)" \
               ".querySelectorAll('[data-automation-id=file-upload-item-name]')].some(n => n.textContent.trim() === name)"
        deadline = time.monotonic() + 15
        while not control.evaluate(gone, document.name):
            if time.monotonic() > deadline:
                return 0, shown + " (older upload could not be removed)"
            time.sleep(0.25)
    elif context["files"] or document.name in context["text"]:
        gap = _drop_other_uploads(control, document.name)
        return 0, (shown + " (" + gap + ")") if gap else None
    control.set_input_files(str(document))
    # Give the site time to finish processing before the page is advanced.
    frame = control.owner_frame()
    if frame is not None:
        try:
            frame.wait_for_function("([name, el]) => (el.closest('[data-automation-id^=\"formField\"],fieldset,form') || el.parentElement).innerText.includes(name)",
                                    arg=[document.name, control], timeout=15000)
        except BrowserTimeout:
            pass
    gap = _drop_other_uploads(control, document.name)
    return 1, (shown + " (" + gap + ")") if gap else None


DATE_GROUP = """e => {
  const wrap = e.closest('[data-automation-id=dateInputWrapper]') || e.parentElement;
  const segs = [...wrap.querySelectorAll('input[role=spinbutton][data-automation-id^=dateSection]')];
  const field = e.closest('[data-automation-id^=formField]');
  const title = field ? (field.querySelector('legend,label')?.innerText || '') : '';
  return {first: segs[0] === e, question: title.replace(/\\*\\s*$/, '').trim(),
          parts: segs.map(s => (s.getAttribute('data-automation-id').match(/dateSection(\\w+)-input/) || [])[1]),
          values: segs.map(s => s.value)};
}"""


CHECKBOX_GROUP = """e => {
  // A Lever card names its boxes in .application-label, outside every label; a card with
  // one box ("Yes") still answers the card's question (Palantir's role confirmation).
  const card = e.closest('.application-question.custom-question');
  const scope = e.closest('[data-automation-id^=formField]') || e.closest('fieldset') || card;
  if (!scope) return {count: 0, question: '', required: false, checked: false};
  const boxes = [...scope.querySelectorAll('input[type=checkbox]')];
  const title = scope === card ? card.querySelector('.application-label') :
    scope.querySelector('legend') || [...scope.querySelectorAll('label')].find(l => !l.htmlFor || !boxes.some(b => b.id === l.htmlFor));
  let text = title ? title.innerText.trim() : '';
  // GM's Workday questionnaire repeats the question in two paragraphs (the second starred): that is one question.
  const parts = text.split(/\\n\\s*\\n/).map(p => p.replace(/[\\s*]+$/, '')).filter(Boolean);
  if (parts.length > 1 && parts.every(p => p === parts[0])) text = parts[0] + (/[*]\\s*$/.test(text) ? '*' : '');
  // Lever marks required card questions with a trailing "✱" (after a line break).
  return {count: boxes.length, card: scope === card, question: text.replace(/[\\s*✱]+$/, '').trim(), required: /[*✱]\\s*$/.test(text) || boxes.some(b => b.required || b.getAttribute('aria-required') === 'true'),
          checked: boxes.some(b => b.checked)};
}"""


SIGNATURE_DATES = {"date", "today's date", "todays date", "signature date", "date signed", "current date"}


def signature_date(label, placeholder="", kind="text", today=None):
    """Today's date for a form's signature "Date" box (Lever's disability self-ID), in the box's
    own format; None for any other field."""
    import datetime
    if normalize(label) not in SIGNATURE_DATES:
        return None
    today = today or datetime.date.today()
    if kind == "date" or re.search(r"yyyy-mm-dd", placeholder or "", re.I):
        return today.isoformat()
    if re.search(r"dd/mm/yyyy", placeholder or "", re.I):
        return today.strftime("%d/%m/%Y")
    return today.strftime("%m/%d/%Y")


def fill_date_group(frame, control, profile):
    """Type a saved date into a Workday Month/Day/Year spinbutton group; (filled, missing).

    Workday only accepts typed digits, advances between boxes by itself, and bounces
    direct focus back to the first box, so the whole date is typed from the first.
    """
    group = control.evaluate(DATE_GROUP)
    if not group["first"]:
        return 0, None  # handled with the group's first box
    question = group["question"] or "date"
    value = field_value(profile, question)
    if not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", str(value or "")):
        value = None  # e.g. just the month, for a label that says "(month and year)"
    if value is None and normalize(question) in SIGNATURE_DATES:
        import datetime
        value = datetime.date.today().isoformat()  # a signature date is always today's
    if value is None and re.search(r"graduat|degree completion|completion date|complete your degree", normalize(question)):
        from .form_controls import graduation_parts
        parts = graduation_parts(profile)
        value = "-".join(p for p in parts if p) if parts else None
    match = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", str(value or ""))
    if not match:
        return 0, (question if control.evaluate(REQUIRED) or control.get_attribute("aria-required") == "true" else None)
    year, month, day = match.groups()
    wanted = {"Month": month, "Day": day, "Year": year}
    if any(part == "Day" for part in group["parts"]) and not day:
        return 0, question + " (saved date has no day)"
    digits = [wanted.get(part) for part in group["parts"]]
    def correct():
        values = control.evaluate(DATE_GROUP)["values"]
        return all(v.isdigit() and d and int(v) == int(d) for v, d in zip(values, digits))
    if correct():
        return 0, None
    control.focus()
    frame.page.keyboard.type("".join(d.zfill(4 if part == "Year" else 2) for d, part in zip(digits, group["parts"])))
    frame.page.keyboard.press("Tab")
    return (1, None) if correct() else (0, question + " (date did not stick)")


class _Paused(Exception):
    pass


def fill_native(frame, profile, documents, filled, missing, stop):
    """Fill native inputs/selects/textareas/files in one frame; returns (filled, missing)."""
    # Element handles stay bound to their nodes when conditional fields are inserted.
    for control in frame.locator("input,textarea,select").element_handles():
        if stop():
            raise _Paused()
        kind = control.get_attribute("type") or "text"
        if kind in {"hidden", "button", "submit", "reset", "radio"} or not control.is_enabled():
            continue
        if control.get_attribute("id") == "icims_f_signature":
            continue  # answered with the iCIMS veteran form (icims.fill_veteran_form)
        if kind == "file":
            # Drop-zone inputs are hidden but still accept files.
            try:
                count, gap = upload_document(control, documents)
            except BrowserTimeout:
                count, gap = 0, "file upload timed out"
            filled += count
            if gap:
                missing.append(gap)
            continue
        if not control.is_visible() and not (kind == "checkbox" and control.evaluate(LABEL_SHOWN)):
            continue
        # Search-and-pick comboboxes (Greenhouse) are answered by fill_comboboxes,
        # and their aria-hidden "required" twin is not a field of its own.
        if control.get_attribute("data-qa") == "location-input":
            continue  # Lever's location typeahead: fill_lever_location picks it
        # A native <select> stays here even when marked role=combobox (iCIMS); fill_comboboxes only reads inputs.
        if control.get_attribute("aria-hidden") == "true" or (control.get_attribute("role") == "combobox" and
                control.evaluate("e => e.tagName !== 'SELECT' && !e.closest('[data-automation-id=multiSelectContainer]')")):
            continue
        if control.get_attribute("role") == "spinbutton" and (control.get_attribute("data-automation-id") or "").startswith("dateSection") \
                and not control.evaluate("e=>!!e.closest('[data-job-agent-record]')"):
            count, gap = fill_date_group(frame, control, profile)
            filled += count
            if gap:
                missing.append(gap)
            continue
        label = control.evaluate(LABEL).rstrip("* ").strip()
        if kind == "checkbox" and not isinstance(field_value(profile, label, choice=True), bool):
            group = control.evaluate(CHECKBOX_GROUP)
            if (group["count"] > 1 or group.get("card")) and group["question"]:
                # Boxes labelled "Yes"/"No" or with places answer the group's question.
                answer = field_value(profile, group["question"], choice=True)
                if answer is None:
                    if group["required"] and not group["checked"]:
                        missing.append(group["question"])
                    continue
                want = option_match([{"label": label, "value": "x"}], answer) == "x"
                if control.is_checked() != want:
                    toggle(control)
                if control.is_checked() != want:
                    missing.append(group["question"] + " (did not stick)")
                elif want:
                    filled += 1
                continue
        required = control.get_attribute("required") is not None or control.evaluate(REQUIRED)
        tag = control.evaluate("e=>e.tagName")
        value = field_value(profile, label, choice=kind == "checkbox" or tag == "SELECT")
        from .custom_controls import record_value
        record = record_value(control.get_attribute("id"), profile)  # Greenhouse education years
        if record is not None:
            value = record
        # Repeated records have their own scoped answers, not one global answer
        # for every company, school, or date. Still validate their inputs below.
        if control.evaluate("e=>!!e.closest('[data-job-agent-record]')"):
            value = None
        elif value is None and kind in {"text", "date"} and tag == "INPUT":
            value = signature_date(label, control.get_attribute("placeholder") or "", kind)
        if kind == "password":
            missing.append(label or kind)
            continue
        if tag == "SELECT":
            from .custom_controls import remember_options
            remember_options(profile, label, control.evaluate(
                "e => [...e.options].filter(o => !o.disabled).map(o => o.textContent)"))
        if control.evaluate(TOKEN_FILLED):
            continue
        # Workday search-and-pick prompts: typed text is only a query; an option
        # must be chosen. Preferences (a list) are tried in order.
        if value is not None and control.evaluate("e => !!e.closest('[data-automation-id=multiSelectContainer]')"):
            from .repeated_sections import search_any
            if search_any(frame.page, control, value):
                filled += 1
            else:
                missing.append(label + " (saved answer not among options)")
            continue
        try:
            if value is not None:
                if kind == "checkbox":
                    if not isinstance(value, bool):
                        missing.append(label + " (boolean answer required)")
                        continue
                    if control.is_checked() != value:
                        control.set_checked(value)
                    stuck = control.is_checked() == value
                elif tag == "SELECT":
                    options = control.evaluate("e => [...e.options].map(o => ({label: o.textContent, value: o.value, disabled: o.disabled}))")
                    chosen = option_match(options, value)
                    if chosen is None:
                        missing.append(label + " (answer not an available option)")
                        continue
                    if control.input_value() != chosen:
                        control.select_option(value=chosen)
                    stuck = control.input_value() == chosen
                else:
                    # Preferences (a list) serve choice controls; free text takes the
                    # first plain-text preference.
                    # A later plain-text preference is the fallback for a field that
                    # rejects the first (a numbers-only salary box).
                    texts = [v for v in value if isinstance(v, str)] if isinstance(value, list) else [value]
                    if not texts or isinstance(texts[0], (bool, dict)):
                        missing.append(label + " (no text answer saved)")
                        continue
                    stuck = False
                    for text in texts:
                        if control.input_value() != str(text):
                            control.fill(str(text))
                        stuck = control.input_value() == str(text)
                        if stuck:
                            break
                # Read back: a site can silently reject or reset what was entered.
                if not stuck:
                    missing.append(label + " (did not stick)")
                    continue
                filled += 1
            else:
                populated = control.is_checked() if kind == "checkbox" else bool(control.input_value().strip())
                if required and not populated:
                    missing.append(label or "unlabelled required field")
                elif populated and kind != "checkbox" and label and control.is_visible() and (
                        label.rstrip().endswith("?") or control.evaluate("e => !!e.closest('.custom-question')")):
                    # An employer question (a Lever card, or a label that asks) holding a value
                    # SPAM has no answer for was typed by the site: Lever's resume parser put
                    # the college year in "Year of High School Graduation". Contact fields keep theirs.
                    missing.append(label + " (filled in by the site; no saved answer)")
            if not control.evaluate("e=>e.checkValidity()"):
                missing.append(label + " (validation failed)")
        except BrowserTimeout:
            missing.append(label + " (control timed out)")
    for question in frame.evaluate(EMPTY_REQUIRED_GROUPS):
        if not any(m.startswith(question) for m in missing):
            missing.append(question + " (no saved answer matches its options)")
    return filled, missing


# A checkbox the site hides behind its own styled box still has a visible label.
LABEL_SHOWN = "e => [...(e.labels || [])].some(l => l.offsetWidth || l.offsetHeight)"
# Required checkbox groups (Greenhouse fieldsets) with no box ticked.
EMPTY_REQUIRED_GROUPS = """() => {
  const out = [];
  for (const fs of document.querySelectorAll('fieldset')) {
    const boxes = [...fs.querySelectorAll('input[type=checkbox]')];
    if (boxes.length < 2 || boxes.some(b => b.checked)) continue;
    const legend = fs.querySelector('legend');
    const shown = boxes.some(b => b.offsetWidth || b.offsetHeight || [...(b.labels || [])].some(l => l.offsetWidth || l.offsetHeight));
    const required = boxes.some(b => b.required || b.getAttribute('aria-required') === 'true') || /\*\s*$/.test(legend?.textContent.trim() || '');
    if (legend && shown && required) out.push(legend.textContent.replace(/\s+/g, ' ').replace(/\s*\*+\s*$/, '').trim());
  }
  return out;
}"""


def toggle(control):
    """Tick or untick a checkbox, through its label when the site hides the box itself."""
    if control.is_visible():
        control.click(timeout=5000)
    else:
        control.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e).click()")


# Text a site shows once an application has actually been received.
SUBMITTED = re.compile(r"application (?:has been |was )?(?:submitted|received|sent)|thank you for (?:applying|your application)|successfully (?:submitted|applied)", re.IGNORECASE)

# Reject/decline is tried first: the privacy-preserving choice wins when a banner offers both.
COOKIE_REJECT = re.compile(r"^(?:reject|decline)(?: all)?(?: (?:cookies?|optional cookies?|non-essential cookies?))?$", re.I)
COOKIE_ACTION = re.compile(r"^accept(?: all)? (?:cookies?|optional cookies?)$|^got it$", re.I)


CONTROLS = "() => [...document.querySelectorAll('input:not([type=hidden]),select,textarea,button[aria-haspopup=listbox]')].filter(e => e.offsetWidth || e.offsetHeight || e.getClientRects().length).length"


def visible_controls(page):
    return sum(frame.evaluate(CONTROLS) for frame in live_frames(page))


def revealed_more(page, before, wait_ms=2500):
    """True when more form controls become visible within wait_ms."""
    for _ in range(wait_ms // 250):
        if visible_controls(page) > before:
            return True
        page.wait_for_timeout(250)
    return False


def dismiss_cookie_overlays(page, on_event=None):
    """Dismiss only obvious cookie banners, rejecting optional cookies when the banner allows it;
    never click application/legal consent."""
    dismissed = 0
    for action in (COOKIE_REJECT, COOKIE_ACTION):
        for frame in live_frames(page):
            for button in frame.locator("button,[role=button]").element_handles():
                if not button.is_visible() or not button.is_enabled():
                    continue
                text = re.sub(r"\s+", " ", button.inner_text().strip())
                if not action.fullmatch(text):
                    continue
                # The nearest wrapper can be just the button row (Oracle's cookie-consent__actions); look a few levels up.
                container = button.evaluate("""e => { if (e.closest('[class*=cookie i],[id*=cookie i]')) return 'cookie';
                  let n = e.closest('[role=dialog],section,aside,div'), text = '';
                  for (let i = 0; n && i < 4 && !/cookie|privacy|tracking/i.test(text); i++, n = n.parentElement?.closest('[role=dialog],section,aside,div'))
                    text = n.innerText || ''; return text; }""")
                if not re.search(r"cookie|privacy|tracking", container, re.I):
                    continue
                button.click(timeout=3000)
                dismissed += 1
                if on_event:
                    on_event("Dismissed cookie banner")
        if dismissed:
            break
    return dismissed


def audit_review(page, profile_data):
    """Compare the Review summary with saved facts; return mismatch notes.

    Checks what a wrong fill would silently change: each saved work entry's
    title and MM/YYYY dates, and the degree. Sections the page does not show
    are not judged.
    """
    text = "\n".join(frame.locator("body").inner_text() for frame in live_frames(page))
    flat = re.sub(r"\s+", " ", text)
    notes = []
    records = profile_data.get("facts", {}).get("experience", [])
    titles = [(r.get("title") or "").strip() for r in records]
    section = re.search(r"Work (?:Experience|History)", flat)
    if records and section:
        # Entries appear in saved order inside the section; the job's own title
        # (often the same words) sits above it and is never used as an anchor.
        cursor = section.end()
        for index, record in enumerate(records):
            title = titles[index]
            if not title:
                continue  # nothing to anchor an untitled record on
            at = flat.find(title, cursor)
            if at < 0:
                notes.append(f"Review: work entry {title or index + 1} is missing")
                continue
            cursor = at + len(title)
            following = [flat.find(t, cursor) for t in titles[index + 1:] if t]
            end = min([e for e in following if e >= 0] or [at + 1500])
            entry = flat[at:end]
            for key, word in (("start_date", "start"), ("end_date", "end")):
                if key == "end_date" and record.get("current"):
                    continue
                date = record.get(key)
                if date and len(date.split("-")) >= 2:
                    year, month = date.split("-")[:2]
                    if f"{month}/{year}" not in entry:
                        notes.append(f"Review: {title} shows no {month}/{year} {word} date")
    for record in profile_data.get("facts", {}).get("education", []):
        names = record.get("degree_synonyms") or ([record["degree"]] if record.get("degree") else [])
        # The value is the line after a line that is just "Degree" (a word such as
        # "First-Degree" inside the value must not be taken as the label).
        shown = re.search(r"(?:^|\n)\s*Degree\s*\*?\s*\n\s*([^\n]{1,160})", text)
        # One-page forms (Ashby) keep the degree in a text box, whose value page text does not include.
        boxes = [f.locator("#_systemfield_education_history-degree") for f in live_frames(page)]
        # Ashby autocomplete (Frostline): <fieldset><label>Degree</label><input role=combobox value=…>.
        boxes += [f.locator(":is(fieldset, .ashby-application-form-field-entry):has(> label:text-is('Degree')) "
                            "input.ashby-application-form-input-autocomplete")
                  for f in live_frames(page)]
        box = next((b for b in boxes if b.count()), None)
        if box is not None:
            shown = re.match(r"(.+)", box.first.input_value().strip() or "(blank)")
        if names and shown and not any((isinstance(n, str) and normalize(n) == normalize(shown.group(1)))
                                       or option_match([{"label": shown.group(1), "value": "x"}], n) for n in names):
            notes.append(f"Review: degree shows {shown.group(1).strip()!r}, expected one of {names}")
    return notes


def submit_application(page, frame, button, on_event):
    """Click the single Submit control and confirm the site acknowledged it."""
    on_event("Submitting the application")
    from .ashby import wait_for_saves
    if not wait_for_saves(page):  # Ashby refuses a Submit while answer saves are still pending
        on_event("Answer saves were still pending after 30s; submitting anyway")
    before = frame.locator("body").inner_text()
    try:
        button.click(timeout=10000)
    except BrowserTimeout:
        # iCIMS: the form iframe is taller than the window, so its Submit is "outside of the viewport" for the mouse.
        # Only the one validated Submit of a framed form takes the direct click; anything else re-raises.
        if frame == page.main_frame or not button.is_visible():
            raise
        button.evaluate("e => e.click()")
    try:
        # Greenhouse can spin for most of a minute (reCAPTCHA scoring) before its confirmation page.
        frame.wait_for_function("previous => !document.body || (document.body.innerText !== previous && !document.querySelector('button[type=submit] .spinner, button[type=submit] [class*=spinner], button[type=submit][aria-busy=true]'))",
                                arg=before, timeout=60000)
    except BrowserTimeout:
        pass
    except BrowserError:
        pass  # the form's frame navigated or was replaced (iCIMS portal forms): the settle below reads the result
    settle(page)
    # After Submit navigates, frames of the old page (a reCAPTCHA iframe on Greenhouse)
    # are detached or unreachable, and evaluating in them waits forever (CoVant).
    live = live_frames(page)
    errors, texts = [], []
    for f in live:
        try:
            errors += f.evaluate(ALERTS)
            texts.append(f.locator("body").inner_text(timeout=5000))
        except (BrowserError, BrowserTimeout):
            continue
    text = " ".join(texts)
    # Greenhouse's /confirmation page can carry the company's own copy with no
    # "thank you" (Huddlo: "We can't wait to look over your application").
    match = SUBMITTED.search(text) or (re.search(r"/confirmation\b", page.url) and re.search(r"thank|your application", text, re.I))
    # Ashby's bot filter: nothing was sent; a person must press Submit themselves.
    if re.search(r"flagged as (?:possible )?spam|couldn'?t submit your application", text, re.I):
        from .capture import capture
        saved = capture(page, "flagged as possible spam", None)
        return "spam_flagged", ["The site flagged the automated submit as possible spam; nothing was sent. Submit it yourself in the open window" +
                                (f" (page saved to {saved})" if saved else "")], None
    # Workday guest-apply tenants (FIS) show no message; they redirect to a URL
    # carrying the new application's id.
    application_id = re.search(r"[?&]Job_Application_ID=([0-9a-f]+)", page.url, re.I)
    if application_id and not errors:
        return "submitted", [], "Workday Job_Application_ID=" + application_id.group(1)
    if errors:
        return "needs_input", sorted({"Site error: " + item for item in errors}), None
    # Greenhouse can hold the application until a code emailed to the applicant is entered.
    if not match and re.search(r"(?:security|verification) code|enter the code|code (?:was |has been )?sent to", text, re.I):
        return "email_code_required", ["The site emailed a code that must be entered to finish submitting"], None
    if match:
        start = max(0, match.start() - 80)
        return "submitted", [], text[start:match.end() + 120].strip()
    # Lever (Shield AI, Palantir) can answer Submit with an hCaptcha challenge: nothing was sent, and SPAM
    # never solves one, so the filled window stays open for the user to finish.
    if page.evaluate("""() => [...document.querySelectorAll('iframe')].some(f => /hcaptcha|recaptcha/i.test((f.src || '') + (f.title || ''))
                        && /challenge/i.test((f.src || '') + (f.title || '')) && f.getBoundingClientRect().height > 100)"""):
        from .capture import capture
        saved = capture(page, "captcha after submit", None)
        return "hand_submit", ["The site showed a CAPTCHA after Submit; nothing was sent. Solve it and press Submit in the open window" +
                               (f" (page saved to {saved})" if saved else "")], None
    # Keep what the page showed, so it can be checked by hand before anything is retried.
    from .capture import capture
    saved = capture(page, "submit clicked, no confirmation", None)
    return "submit_uncertain", ["Submit was clicked but no confirmation text was found; check the site" +
                                (f" (page saved to {saved})" if saved else "")], None


def prepare(page, profile_data, max_steps=12, decider=None, should_stop=None, on_event=None, submit=False, bug_dir=None):
    """Fill saved answers page by page. Stops at the Submit control with
    ready_for_review unless submit=True, in which case it submits when nothing is
    missing and reports 'submitted' only after the site acknowledges it."""
    profile = Profile(profile_data)
    profile.seen_options = {}  # choices of unanswered questions, reported for the answer deck
    documents = {normalize(k): Path(v).expanduser().resolve() for k, v in profile_data.get("documents", {}).items() if v}
    history = []
    confirmation = None

    def cannot_fill(exc):
        # Never a crash: save the page for a fixture and stop for a person (the
        # traceback goes to the run log for diagnosis).
        import traceback
        traceback.print_exc()
        from .capture import capture
        saved = capture(page, str(exc).splitlines()[0][:200], bug_dir)
        return result("needs_input", ["Could not fill this page (" + str(exc).splitlines()[0][:120] + ")" +
                                      (f"; saved to {saved}" if saved else "")])

    def result(status, missing=None):
        report = {"status": status, "history": history, "missing": missing or [],
                  "note": "Submission not attempted" if status != "submitted" else "Application submitted"}
        if confirmation:
            report["confirmation"] = confirmation
        if profile.seen_options and report["missing"]:
            from .questions import question_label
            wanted = {question_label(m) for m in report["missing"]}
            report["options"] = {q: o for q, o in profile.seen_options.items() if q in wanted}
        return report

    reloads = 0
    site_refilled = False
    waited_for_page = False
    for step in range(max_steps):
        step_started = time.monotonic()
        if should_stop and should_stop():
            return result("paused")
        settle(page)
        reloaded = False
        # Transient site failures ask for a refresh; do it at most twice per run.
        if reloads < 2 and TRANSIENT_ERROR.search(page.locator("body").inner_text()):
            reloads += 1
            reloaded = True
            if on_event:
                on_event("Site reported a temporary error; reloading the page")
            page.reload(wait_until="domcontentloaded", timeout=45000)
            settle(page)
        stop = should_stop or (lambda: False)
        emit = on_event or (lambda text: None)
        dismiss_cookie_overlays(page, emit)
        if profile_data.get("targeting", {}).get("skip_transcript_required") and required_transcript(page):
            emit("Skipping: this application requires a transcript")
            return result("skipped_transcript", ["Required transcript upload; excluded by your preferences"])
        from .repeated_sections import close_prompts, fill_repeated
        close_prompts(page)  # a prompt left open (earlier step, manual use) covers fields
        try:
            repeated_missing = fill_repeated(page, profile_data, stop, emit)
        except BrowserError as exc:
            return cannot_fill(exc)
        for attempt in range(6):
            before = visible_controls(page)
            missing = list(repeated_missing)
            filled = 0
            stale = False
            try:
                for frame in live_frames(page):
                    from .icims import fill_veteran_form
                    count, gaps = fill_veteran_form(frame, profile)  # iCIMS VEVRAA form: unlabeled radios
                    filled += count
                    missing += gaps
                    # Radio groups and listbox buttons first; they are invisible to the native pass.
                    count, gaps = fill_custom_controls(frame, profile, stop, emit)
                    filled += count
                    missing += gaps
                    filled, missing = fill_native(frame, profile, documents, filled, missing, stop)
            except BrowserError as exc:
                if "not attached" not in str(exc).lower():
                    return cannot_fill(exc)
                # A fill re-rendered later controls; observe the fresh page instead of skipping them.
                stale = True
            except _Paused:
                return result("paused")
            # An answer can reveal a follow-up question after a server round
            # trip (QTX: enrolled -> degree program); fill again if one appears.
            if not stale and not (filled and revealed_more(page, before)):
                break
            settle(page)
        else:
            return result("needs_input", ["Page kept re-rendering while filling"])
        history.append({"step": step + 1, "filled": filled, "fill_seconds": round(time.monotonic() - step_started, 1),
                        **({"reloaded": True} if reloaded else {})})
        if on_event:
            on_event(f"Step {step + 1}: processed {filled} saved answers")
        if missing:
            return result("needs_input", sorted(set(missing)))

        # Repeatable sections are not represented by required inputs until Add
        # is pressed. Surface them instead of silently skipping known history.
        for title, fact_key in (("Work Experience", "experience"), ("Education", "education")):
            if not profile_data.get("facts", {}).get(fact_key):
                continue
            empty_sections = page.evaluate("""title => [...document.querySelectorAll('h2,h3,h4,[role=heading]')]
                .filter(e=>e.textContent.trim()===title).some(heading=>{
                    let node=heading.parentElement;
                    for(let depth=0;node && depth<3;depth++,node=node.parentElement){
                        const buttons=[...node.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Add');
                        if(buttons.length===1){
                            const text=node.innerText.replace(/\\s+/g,' ').trim();
                            return !node.querySelector('input,select,textarea') &&
                              !/\\b(edit|delete|remove)\\b/i.test(text) && text.length<160;
                        }
                    }
                    return false;
                })""", title)
            if empty_sections:
                missing.append(title + " has saved records but its Add section is still empty")
        if missing:
            return result("needs_input", missing)

        next_controls, submit_controls = [], []
        from .icims import before_final_step, on_final_step
        for frame in live_frames(page):
            final = on_final_step(frame)  # iCIMS: on the last step "Next" sends the application
            earlier = before_final_step(frame)  # ...and before it, a "Submit" only saves the step
            for button in frame.locator("button,input[type=submit],input[type=button],a,[role=button]").all():
                if not button.is_visible() or not button.is_enabled():
                    continue
                text = normalize(button.get_attribute("aria-label") or button.get_attribute("value") or button.inner_text())
                if final and text == "next":
                    submit_controls.append((frame, button))
                    continue
                if earlier and text == "submit":
                    next_controls.append((frame, button))
                    continue
                if text in NEXT:
                    next_controls.append((frame, button))
                # A posting's Apply hyperlink enters an application; it is not
                # evidence that a form has been completed or is ready to submit.
                if text in SUBMIT and not button.evaluate("e => e.tagName === 'A' && !!e.getAttribute('href')"):
                    # "Apply" on a posting page (ByteDance) opens the form; it submits
                    # only on a page that has form fields.
                    if text == "apply" and frame.locator("input:not([type=hidden]):not([type=search]):visible, textarea:visible, select:visible").count() < 3:
                        continue
                    submit_controls.append((frame, button))
        if submit_controls:
            # Final safety net: the site's own summary must agree with the profile.
            mismatches = audit_review(page, profile_data)
            if mismatches:
                emit("Review disagrees with your profile; not submitting")
                return result("review_mismatch", mismatches)
            # Every answer the site will receive, checked before an unattended Submit.
            from .review_check import check_review, form_snapshot, is_form_page
            # A Review page shows the answers as text; a one-page form (Greenhouse,
            # Ashby) keeps them in its controls, so read those instead.
            frames = live_frames(page)
            forms = [f for f in frames if is_form_page(f)]
            # When the form lives in a frame (iCIMS), the page around it is the employer's site: its footer
            # ("linkedin", "instagram") is not an answer. Read only the form frames then.
            problems = check_review("\n".join(form_snapshot(f) if f in forms else f.locator("body").inner_text()
                                              for f in (forms if forms and page.main_frame not in forms else frames)), profile_data)
            # Last look right before the click: a required checkbox group an answer
            # was lost from (a re-render after the tick) must not go out empty.
            problems += [q + " (required, nothing ticked)" for f in live_frames(page)
                         for q in f.evaluate(EMPTY_REQUIRED_GROUPS)]
            if problems:
                emit("Review answers need a look; not submitting")
                return result("review_flagged", problems)
            if not submit:
                return result("ready_for_review")
            if len(submit_controls) > 1:
                # Greenhouse keeps its posting's "Apply" button above the form: the
                # form's own Submit is the one that sends the application.
                named = [(f, b) for f, b in submit_controls
                         if normalize(b.get_attribute("aria-label") or b.get_attribute("value") or b.inner_text()) != "apply"]
                submit_controls = named or submit_controls
            if len(submit_controls) > 1:
                # iCIMS forms repeat the same Submit above and below the fields: one control, shown twice.
                keys = {(f, normalize(b.get_attribute("value") or b.inner_text()), b.get_attribute("name")) for f, b in submit_controls}
                if len(keys) == 1:
                    submit_controls = submit_controls[-1:]
            if len(submit_controls) != 1:
                return result("needs_input", ["Ambiguous submit controls"])
            if stop():
                return result("paused")
            status, gaps, confirmation = submit_application(page, submit_controls[0][0], submit_controls[0][1], emit)
            return result(status, gaps)
        if not next_controls and not filled and not waited_for_page and step + 1 < max_steps:
            # The next page had not rendered yet (RBC): wait for it once and redo the step.
            waited_for_page = True
            if wait_for_forward_control(page):
                continue  # its forward button was disabled or hidden (Workday mid-save); it is usable now
            try:
                page.locator("[data-automation-id=pageFooterNextButton], button:has-text('Next'), "
                             "button:has-text('Continue'), button:has-text('Submit')").first.wait_for(timeout=15000)
            except BrowserTimeout:
                pass
            continue
        if len(next_controls) > 1:
            # iCIMS forms repeat the same button above and below the fields: one control, shown twice.
            keys = {(f, normalize(b.get_attribute("value") or b.inner_text()), b.get_attribute("name")) for f, b in next_controls}
            if len(keys) == 1:
                next_controls = next_controls[-1:]
        if not next_controls or len(next_controls) > 3:
            return result("needs_input", ["No unique supported navigation control"])
        waited_for_page = False
        selected = 0
        if decider is not None:
            options = [f"{i + 1}: {normalize(b.get_attribute('aria-label') or b.get_attribute('value') or b.inner_text())}" for i, (_, b) in enumerate(next_controls)]
            options.append("Stop: need help")
            selected = decider.choose("Application fields checked. Choose navigation to the next step.", options)
            history[-1]["decision_engine"] = "laya"
            if selected is None or selected >= len(next_controls):
                if len(next_controls) == 1:
                    # These candidates are already restricted to explicit forward
                    # navigation, after required-field checks. No model judgment
                    # is needed to choose between one supported control and stop.
                    selected = 0
                    history[-1]["decision_engine"] = "rules_fallback"
                    emit("Laya was uncertain; using the single validated Next control")
                else:
                    return result("needs_input", ["Laya could not resolve multiple navigation controls"])
        elif len(next_controls) != 1:
            return result("needs_input", ["Ambiguous navigation controls"])
        frame, button = next_controls[selected]
        if should_stop and should_stop():
            return result("paused")
        if on_event:
            engine = history[-1].get("decision_engine")
            suffix = {"laya": " with Laya", "rules_fallback": " using validated navigation"}.get(engine, "")
            on_event("Advancing to the next page" + suffix)
        # Explicit navigation names only; never model-selected arbitrary selectors.
        before = frame.locator("body").inner_text()
        old_url = frame.url
        from .repeated_sections import close_prompts
        close_prompts(page)  # an open prompt or picker would swallow the click
        try:
            button.click(timeout=5000)
        except BrowserTimeout:
            close_prompts(page)
            try:
                button.click(timeout=5000)
            except BrowserTimeout:
                # iCIMS sizes its form iframe taller than the window, so a mouse click on its Next never lands.
                # This is the already-validated forward control, never a Submit (those take the branch above).
                try:
                    button.evaluate("e => e.click()")
                except BrowserError as exc:
                    return cannot_fill(exc)
            except BrowserError as exc:
                return cannot_fill(exc)
        progressed = True
        try:
            frame.wait_for_function("previous => location.href !== previous.url || document.body.innerText !== previous.text",
                                    arg={"url": old_url, "text": before}, timeout=5000)
        except BrowserTimeout:
            progressed = False
        settle(page)
        # A Workday save in flight keeps the old step on screen with its footer button disabled;
        # filling that stale page again would find no navigation control. Wait for the save.
        # Only Workday's footer: other sites keep Next disabled until the new page is filled.
        try:
            workday_footer = page.locator("[data-automation-id=pageFooterNextButton]").count() > 0
        except BrowserError:
            workday_footer = False
        if workday_footer and wait_for_forward_control(page):
            settle(page)
        # Some Workday tenants finish their client-side step transition well
        # after the first observation timeout. settle() may see the new stable
        # page; otherwise allow one final bounded grace period before treating
        # the click as a no-op.
        if not progressed:
            try:
                progressed = frame.is_detached() or frame.url != old_url or frame.locator("body").inner_text() != before
                if not progressed:
                    frame.wait_for_function("previous => location.href !== previous.url || document.body.innerText !== previous.text",
                                            arg={"url": old_url, "text": before}, timeout=10000)
                    progressed = True
            except BrowserTimeout:
                pass
            except BrowserError:
                progressed = True  # detaching the old frame is itself observable navigation
        # A site that rejects the page shows its own validation errors; report
        # them instead of re-filling the same page until the step limit.
        errors = [text for f in live_frames(page) for text in f.evaluate(ALERTS)]
        if errors:
            # A site can clear an answer after a later choice (QTX). When every
            # error names a required field SPAM has an answer for, refill once.
            named = [re.match(r"The field (.+?) is required", text) for text in errors]
            if not site_refilled and all(named) and all(field_value(profile, m.group(1), choice=True) is not None for m in named):
                site_refilled = True
                emit("Site cleared a saved answer; filling the page again")
                continue
            return result("needs_input", sorted({"Site error: " + text for text in errors}))
        if not progressed:
            if reloads < 2 and TRANSIENT_ERROR.search(page.locator("body").inner_text()):
                continue  # Workday "Something went wrong" after Next: the next step reloads and retries
            return result("needs_input", ["Navigation made no observable progress"])
    return result("needs_input", ["Step limit reached"])


def run(url, profile_data, browser_dir, max_steps=12, decider=None, account_store=None, submit=False):
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(browser_dir), headless=True)
        try:
            page = context.new_page()
            gate = open_application(page, url, account_store)
            if gate is not None:
                return gate
            return prepare(page, profile_data, max_steps, decider, submit=submit)
        finally:
            context.close()
