"""Second look at a Review page before Submit.

Every answer the profile knows must agree with what the site shows, and the
profile's `review_checks` (answers that must hold whatever a question's wording,
e.g. "18 or older" is Yes) must pass. A wrong answer blocks Submit instead of
going out (Strykon: an "if you are under 18" clause turned "18 or older?" into No).
"""
import re

from .custom_controls import option_match
from .library import review_checks as library_checks
from .profile import Profile, normalize

QUESTION_END = ("?", "*", ":")
# Guidance printed between a question and its answer (Harlow Allen: "If this role is
# located in the United States, answer 'Yes/No'..."); the answer is on the next line.
INSTRUCTION = re.compile(r"^(?:if (?:this|you|the|your)\b|please\b|note\b|for purposes\b|applicants? (?:living|residing) in\b|applicants? applying for\b|\(?e\.g\.|examples? includes?\b)|\banswer [‘'\"]", re.I)


BULLET = re.compile(r"^[•·▪]\s*")
# Page footer text under a footer link (HCSC: "Privacy Statement" then "© 2026 Workday, Inc."), not an answer.
PAGE_CHROME = re.compile(r"^©|all rights reserved\.?$", re.I)


def looks_like_question(line):
    return line.rstrip().endswith(QUESTION_END)


def _texts(expected):
    if isinstance(expected, list):
        return [v for v in expected if isinstance(v, str)]
    return [expected] if isinstance(expected, str) else []


def _same_text(shown, wanted):
    a, b = normalize(shown), normalize(str(wanted))
    if a == b or (len(b) > 60 and a.startswith(b[:60])):
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", b):  # 2027-06-01 shows as 06/01/2027
        year, month, day = b.split("-")
        return a in {f"{month}/{day}/{year}", f"{int(month)}/{int(day)}/{year}", f"{month}/{year}"}
    # Numbers and phones: "+1 (555) 0100100 (Mobile)" is (555) 010-0100.
    shown_digits = re.sub(r"\D", "", re.sub(r"\([a-z ]+\)$", "", a))
    wanted_digits = re.sub(r"\D", "", b)
    if not shown_digits or not wanted_digits or len(a) > 40:
        return False
    return shown_digits == wanted_digits or (len(wanted_digits) >= 7 and shown_digits in {"1" + wanted_digits})


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]


def agrees(shown, expected):
    """True when the text the Review page shows is an acceptable form of the saved answer."""
    if isinstance(expected, dict) and "add_all" in expected:
        expected = expected["add_all"]  # a Skills list: every shown skill must be one of them
    parts = [part.strip() for part in shown.split("; ") if part.strip()]
    if len(parts) > 1 and not agrees(shown.replace("; ", ";"), expected) and not any(_same_text(shown, t) for t in _texts(expected)):
        return all(agrees(part, expected) for part in parts)  # several boxes ticked: each must be a saved choice
    labels = [shown]
    if m := re.fullmatch(r"(\d{1,2})/(?:\d{1,2}/)?(\d{4})", shown.strip()):
        labels.append(f"{MONTHS[int(m.group(1)) - 1]} {m.group(2)}")  # 05/01/2029 is "May 2029"
    if any(option_match([{"label": label, "value": "x"}], expected) == "x" for label in labels):
        return True
    return any(_same_text(shown, text) for text in _texts(expected))


# Shown per record or per account, not one profile answer: education uses the saved
# synonyms, and the email is the one each site's account uses.
RECORD_FIELDS = {"email address", "email", "school or university", "school", "university",
                 "country",  # Greenhouse's phone-country box shows only "+1"
                 # Work-history entries carry their own values, not the global answers.
                 "company", "company name", "job title", "title", "role description", "description",
                 "from", "to", "month", "year"}
BUTTON_WORDS = {"submit", "submit application", "save and continue", "continue", "next", "back", "add", "add another", "apply"}
# Workday Review section headings: when one follows a label, that field was left empty.
HEADINGS = {"my information", "my experience", "work experience", "education", "certifications", "languages",
            "skills", "resume/cv", "websites", "social network urls", "application questions", "voluntary disclosures",
            "self identify", "personal data statement", "terms and conditions", "review", "legal name", "address"}


def check_review(text, profile_data):
    """Problems with a Review page's answers; an empty list means it can be submitted."""
    profile = Profile(profile_data)
    rules = [(re.compile(r["match"], re.I), re.compile(r["answer"], re.I), [normalize(u) for u in r.get("unless", [])])
             for r in profile_data.get("review_checks", []) + library_checks(profile_data)]
    education = (profile_data.get("facts", {}).get("education") or [{}])[0]
    synonyms = {"field of study": education.get("major_synonyms"), "major": education.get("major_synonyms"),
                "degree": education.get("degree_synonyms")}
    work_locations = {normalize(entry.get("location") or "")
                      for entry in profile_data.get("facts", {}).get("experience") or [] if entry.get("location")}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    problems = []
    # Printworks' optional Website/Portfolio field is mandatory in practice: its
    # label says an application will not be considered without a link or an
    # attached portfolio. Greenhouse's snapshot includes the value of both
    # controls, so accept either a URL or a file selected in the portfolio
    # attachment control.
    for index, label in enumerate(lines[:-1]):
        if (re.search(r"\bportfolio\b", label, re.I)
                and re.search(r"application will not be considered without a portfolio linked here or attached below", label, re.I)):
            value = lines[index + 1]
            if normalize(value) in {"", "no response"}:
                has_attachment = any(
                    re.search(r"\bportfolio\b", upload_label, re.I)
                    and re.search(r"\battach", upload_label, re.I)
                    and normalize(upload_value) not in {"", "no response"}
                    for upload_index, upload_label in enumerate(lines[:-1])
                    for upload_value in [lines[upload_index + 1]]
                    if upload_index != index
                )
                if not has_attachment:
                    problems.append('"Website/Portfolio" is required by its label, but no link or portfolio attachment is present')
    for index, question in enumerate(lines[:-1]):
        key = normalize(question)
        # Questions end with ? * or :, or are labels the profile answers exactly
        # (Field of Study); other lines are answers or disclosure text.
        if not (looks_like_question(question) or key in profile.answers) or key in RECORD_FIELDS:
            continue
        after = index + 1
        while after < len(lines) - 1 and after - index <= 3 and INSTRUCTION.search(lines[after]) and not looks_like_question(lines[after]):
            after += 1
        # A question can continue as "•" conditions and then a list they apply to (HPE's sanctioned countries).
        if BULLET.match(lines[after]):
            while after < len(lines) - 1 and BULLET.match(lines[after]):
                after += 1
            if after < len(lines) - 1 and lines[after].count(",") >= 3:
                after += 1
        shown = lines[after]
        # An answer never asks something: a line with "?" is the next question (a blank field).
        if key == "location" and normalize(shown) in work_locations:
            continue  # a work-history entry's own location (Workday My Experience), not where the user lives
        if shown == "No Response":
            if key in profile.answers and profile.answers[key] != []:  # [] means "leave it blank"
                problems.append(f'"{question[:90]}" has no response despite a saved answer')
            continue
        if (looks_like_question(shown) or "?" in shown or normalize(shown) in profile.answers
                or normalize(shown) in BUTTON_WORDS
                or normalize(shown) in HEADINGS or normalize(shown).startswith("application questions")
                or PAGE_CHROME.search(shown)):
            continue  # nothing answered here (an optional field left blank)
        for pattern, allowed, unless in rules:
            if pattern.search(key) and not any(u in key for u in unless) and not allowed.search(shown):
                problems.append(f'"{question[:90]}" is answered "{shown[:60]}"')
        if key == "signature" and normalize(shown) == "yes":
            continue  # a ticked e-signature checkbox (iCIMS self-identification form), not a typed name
        expected = synonyms.get(key) or profile.lookup(question, choice=True)
        if expected is None or len(shown) > 400:
            continue
        if isinstance(expected, bool) and normalize(shown) not in {"yes", "no", "true", "false"}:
            continue  # a ticked checkbox is listed by its label, not as Yes
        text_answer = profile.lookup(question)  # a free-text box's answer (GPA "3.8" for a question a Yes/No rule also matches)
        middle = profile_data.get("facts", {}).get("middle_name")
        short_name = expected.split() if key == "name" and isinstance(expected, str) else []
        if middle and len(short_name) == 2 and normalize(shown) == normalize(
                f"{short_name[0]} {middle} {short_name[1]}"):
            continue  # a site's full legal name includes the saved middle name
        # A pick inside a category list reads "Social Network: Linkedin"; the saved choice is "LinkedIn".
        unprefixed = shown.split(": ", 1)[1] if ": " in shown else None
        if not agrees(shown, expected) and not (text_answer is not None and agrees(shown, text_answer)) \
                and not (unprefixed and agrees(unprefixed, expected)):
            if len(shown) > 200 and not _texts(expected):
                continue  # a long line after a disclosure is its text, not an answer
            following = lines[index + 2] if index + 2 < len(lines) else ""
            if len(shown) > 60 and re.search(r"\byou(?:r)?\b", shown, re.I) and looks_like_question(following):
                continue  # instructions to the applicant (AmFam), then the question's second line
            problems.append(f'"{question[:90]}" is answered "{shown[:60]}" but the profile says {str(expected)[:60]}')
    # A dedicated cover-letter slot holding the resume file (Ashby's "Autofill
    # from resume" heading once sent it there) is never right. Combined upload
    # fields such as "Resume / CV / Cover Letter / Other Attachments" are valid
    # places for the resume.
    for index, line in enumerate(lines[:-1]):
        combined_upload = re.search(r"\b(?:resume|cv|other attachments?)\b", line, re.I)
        if (re.search(r"cover letter", line, re.I) and not combined_upload
                and re.search(r"resume|\bcv\b", lines[index + 1], re.I)):
            problems.append(f'"{line[:60]}" holds the resume file "{lines[index + 1][:60]}"')
    return list(dict.fromkeys(problems))


# One-page forms (Greenhouse, Ashby) have no Review page: the answers live in
# the controls, not the text. Read each labelled control's current value into
# the same "question / answer" lines a Review page shows.
FORM_SNAPSHOT = """() => {
  const clean = t => (t || '').replace(/\\s+/g, ' ').replace(/\\s*\\*+\\s*$/, '').trim();
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const doc = document;
  const labelFor = e => {
    if (e.id) { const l = doc.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l && clean(l.textContent)) return clean(l.textContent); }
    const ids = (e.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
    if (ids.length) return clean(ids.map(id => doc.getElementById(id)?.textContent || '').join(' '));
    if (e.getAttribute('aria-label')) return clean(e.getAttribute('aria-label'));
    // Ashby autocomplete: the fieldset's own label names its one control.
    const set = e.closest('fieldset, .ashby-application-form-field-entry'), setLabel = set && set.querySelector(':scope > label');
    if (setLabel && set.querySelectorAll('input:not([type=hidden]),select,textarea').length === 1) return clean(setLabel.textContent);
    // Wrapping label (Lever: minus its ✱ marker and the location dropdown's status text).
    const own = e.closest('label') || (e.type !== 'radio' && e.type !== 'checkbox' && lever(e));
    if (!own) return '';
    const copy = own.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button,.required,.dropdown-container').forEach(n => n.remove());
    return clean(copy.textContent);
  };
  // Lever's custom questions: the text sits in .application-label beside the control.
  const lever = e => e.closest('li.application-question')?.querySelector('.application-label');
  const lines = [], seenGroups = new Set();
  // Upload fields (Greenhouse) swap their file input for the chosen file's name.
  const uploads = [...doc.querySelectorAll('.file-upload, [role=group][aria-labelledby*=upload]')];
  for (const g of uploads) {
    const label = clean((g.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => doc.getElementById(id)?.textContent || '').join(' '));
    const input = g.querySelector('input[type=file]');
    const name = clean(g.querySelector('[class*=filename]')?.textContent) || (input && input.files && input.files[0] ? input.files[0].name : '');
    if (label) lines.push(label + ':', name || 'No Response');
  }
  for (const e of doc.querySelectorAll('input, textarea, select')) {
    const type = (e.type || '').toLowerCase();
    if (uploads.some(g => g.contains(e))) continue;
    if (['hidden', 'submit', 'button', 'reset'].includes(type) || e.getAttribute('aria-hidden') === 'true') continue;
    if (type === 'file') {
      const label = labelFor(e) || clean(e.closest('[class*=field],[class*=upload],fieldset')?.querySelector('label,legend')?.textContent);
      const name = e.files && e.files.length ? e.files[0].name : '';
      if (label) lines.push(label + ':', name || 'No Response');
      continue;
    }
    const labelShown = [...(e.labels || [])].some(l => vis(l));
    if (!vis(e) && !((type === 'radio' || type === 'checkbox') && labelShown)) continue;
    if (type === 'radio' || type === 'checkbox') {
      const leverQuestion = lever(e);
      const group = e.closest('fieldset,[role=radiogroup],[role=group]') || (leverQuestion && e.closest('li.application-question'));
      const key = group || e;
      if (seenGroups.has(key)) continue;
      seenGroups.add(key);
      const heading = group && (group.matches('li.application-question') ? leverQuestion : group.querySelector('legend,label,[class*=label]'));
      const headingText = heading ? (() => { const c = heading.cloneNode(true); c.querySelectorAll('.required').forEach(n => n.remove()); return c.textContent; })() : '';
      const question = group ? clean(headingText || group.getAttribute('aria-label')) : labelFor(e);
      const boxes = group ? [...group.querySelectorAll('input[type=' + type + ']')] : [e];
      const picked = boxes.filter(b => b.checked).map(b => labelFor(b)).filter(Boolean);
      // A lone checkbox reads as its own label when ticked ("Still Student?: Still Student?"): say Yes/No.
      if (question) lines.push(question + ':', boxes.length === 1 && type === 'checkbox' ? (picked.length ? 'Yes' : 'No') : (picked.join('; ') || 'No Response'));
      continue;
    }
    const label = labelFor(e);
    if (!label) continue;
    let value = e.value;
    if (e.tagName === 'SELECT') value = e.selectedOptions[0]?.textContent || '';
    if (e.getAttribute('role') === 'combobox') {
      const shell = e.closest('[class*=select__control], [class*=-control]') || e.parentElement;
      value = [...shell.querySelectorAll('[class*=single-value], [class*=multi-value__label]')].map(x => x.textContent.trim()).join('; ') || e.value;
    }
    lines.push(label + ':', clean(value) || 'No Response');
  }
  // Ashby Yes/No questions: a label and toggle buttons (aria-pressed).
  for (const row of doc.querySelectorAll('div:has(> button[aria-pressed])')) {
    const buttons = [...row.querySelectorAll(':scope > button[aria-pressed]')];
    if (buttons.length < 2 || !vis(row)) continue;
    const field = row.closest('[data-field-path], fieldset, .field-wrapper') || row.parentElement;
    const question = clean(field.querySelector('label, legend')?.textContent);
    const pressed = buttons.find(b => b.getAttribute('aria-pressed') === 'true');
    if (question) lines.push(question + ':', pressed ? clean(pressed.innerText) : 'No Response');
  }
  return lines.join('\\n');
}"""


def form_snapshot(frame):
    """Question/answer text for a one-page application form, from its live controls."""
    return frame.evaluate(FORM_SNAPSHOT)


FORM_CONTROLS = """() => [...document.querySelectorAll('input:not([type=hidden]):not([type=file]):not([aria-hidden=true]), textarea, select')]
  .filter(e => e.offsetWidth || e.offsetHeight || [...(e.labels || [])].some(l => l.offsetWidth || l.offsetHeight)).length"""


def is_form_page(frame):
    """True for a page that still holds the editable form (not a read-only Review page).
    Boxes a site hides behind styled ones count when their labels show."""
    # A visible <select> is a live control even on a short page: as text it would read as all of its options.
    return frame.evaluate(FORM_CONTROLS) >= 3 or frame.locator("select:visible").count() > 0
