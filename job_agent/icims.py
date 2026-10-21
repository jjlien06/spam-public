"""iCIMS (Rivian, AMD, Susquehanna, Atlassian ...): the tenant's /jobs/<id>/login page asks for the email and a
privacy acknowledgement inside an iframe, behind hCaptcha. A challenge, or the central login.icims.com sign-in
(Google/LinkedIn/password), is the user's step: the run stops with the window open. Once signed in, the application
steps (profile, questions, EEO) are ordinary forms in that iframe that `runner.prepare` fills."""
import re
import urllib.request
from urllib.parse import parse_qsl, urlsplit

LOGIN_LINK = r"https://[\w.-]+\.icims\.com/jobs/{job}/login"
CAPTCHA_NOTE = "iCIMS hCaptcha: solve it in the open window (the page continues by itself), then refill this job"
SIGNIN_NOTE = "iCIMS sign-in: sign in in the open window (Continue with Google, or your iCIMS password), then refill this job"


def is_icims(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    return host == "icims.com" or host.endswith(".icims.com") or dict(parse_qsl(parts.query)).get("icims") == "1"


def _job_number(url):
    match = re.search(r"/jobs/(\d+)", urlsplit(url).path)
    return match.group(1) if match else None


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def login_url(url, fetch=_fetch):
    """The tenant's application entry for a posting: built on an iCIMS host, read from the page on a company site."""
    job = _job_number(url)
    if not job:
        return None
    host = urlsplit(url).hostname or ""
    if host.endswith(".icims.com"):
        return f"https://{host}/jobs/{job}/login"
    try:
        match = re.search(LOGIN_LINK.format(job=job), fetch(url))
    except OSError:
        return None
    return match.group(0) if match else None


def signin_gate(url):
    if (urlsplit(url).hostname or "") == "login.icims.com":
        return {"status": "account_signin_required", "history": [], "missing": [SIGNIN_NOTE]}
    return None


def form_frame(page):
    """iCIMS renders each step in an iframe marked in_iframe=1; the page itself on tenants that do not."""
    return next((f for f in page.frames if "in_iframe=1" in f.url), page.main_frame)


def _challenge_open(frame):
    return frame.evaluate("""() => [...document.querySelectorAll('iframe')].some(i =>
        /hcaptcha/i.test(i.src + ' ' + (i.title || '')) && i.offsetWidth > 50 && i.offsetHeight > 50)""")


def pass_email_step(frame, page, email, wait_ms=12000):
    """Fill the email and privacy acknowledgement and press Next. Returns a stop report for a CAPTCHA or the central
    sign-in, else None once the step has gone."""
    frame.fill("#email", email)
    privacy = frame.locator("#accept_privacy")
    if privacy.count() and not privacy.is_checked():
        privacy.check()
    frame.locator("#enterEmailSubmitButton").evaluate("e => e.click()")  # the iframe is taller than the window
    for _ in range(max(1, wait_ms // 500)):
        page.wait_for_timeout(500)
        gate = signin_gate(page.url)
        if gate:
            return gate
        current = form_frame(page)
        try:
            if _challenge_open(current):
                return {"status": "captcha_required", "history": [], "missing": [CAPTCHA_NOTE]}
            if not current.locator("#enterEmailSubmitButton").count():
                return None
        except Exception:
            continue  # the frame navigated mid-check
    return {"status": "needs_input", "history": [], "missing": ["iCIMS email step did not continue"]}


def open_application(page, url, email, on_event=lambda text: None):
    """Reach the first application step. None when the form is showing, else a stop report."""
    entry = login_url(url)
    if not entry:
        return {"status": "needs_input", "history": [], "missing": ["No iCIMS application link found on the posting"]}
    for frame in page.frames:
        # A half-filled step sets pageDirtyFlag, and leaving it raises a "Leave this page?" dialog.
        try:
            frame.evaluate("() => { window.onbeforeunload = null; window.pageDirtyFlag = false; }")
        except Exception:
            pass
    page.goto(entry, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(3000)
    gate = signin_gate(page.url)
    if gate:
        return gate
    frame = form_frame(page)
    if frame.locator("#email").count():
        if not email:
            return {"status": "needs_input", "history": [], "missing": ["iCIMS sign-in needs the default email in accounts.json"]}
        on_event("iCIMS: entering the email")
        gate = pass_email_step(frame, page, email)
        if gate:
            return gate
        page.wait_for_timeout(2000)
    return signin_gate(page.url)


STEP_POSITION = """() => {
  const dl = (window.dataLayer || []).find(d => d && d.totalStep);
  if (dl && dl.currentStep) return [dl.currentStep, dl.totalStep];
  const box = document.querySelector('.iCIMS_Steps');
  if (!box) return null;
  const steps = [...box.innerText.matchAll(/Step\\s+(\\d+)\\s+of\\s+(\\d+)\\.[^()]*\\((Current|Completed) Step\\)/g)];
  const current = steps.find(m => m[3] === 'Current');
  return current ? [+current[1], +current[2]] : null;
}"""


def step_position(frame):
    """(current, total) from iCIMS's step list, or None on pages without one."""
    try:
        position = frame.evaluate(STEP_POSITION)
    except Exception:
        return None
    return tuple(position) if position else None


def on_final_step(frame):
    """iCIMS labels the last step's button "Next", but pressing it sends the application."""
    position = step_position(frame)
    return bool(position) and position[0] >= position[1]


def packet_has_next(frame):
    """A portal form packet (AMD's self-identification forms) names its next form in a hidden "next" field: until
    the last form, its Submit only moves to that form."""
    try:
        return bool(frame.evaluate("() => [...document.querySelectorAll('input[type=hidden][name=next]')].some(e => e.value.trim())"))
    except Exception:
        return False


def before_final_step(frame):
    """An earlier step's button can read "Submit" (AMD) while it only saves that step and moves on."""
    position = step_position(frame)
    return (bool(position) and position[0] < position[1]) or packet_has_next(frame)


# The standard VEVRAA wording of each unlabeled radio, so the saved answer is matched like any other choice.
VETERAN_OPTIONS = [{"label": "I am not a protected veteran", "value": "NotProtectedVeteran"},
                   {"label": "I identify as one or more of the classifications of protected veteran", "value": "ProtectedVeteran"},
                   {"label": "I don't wish to answer", "value": "optout"}]


DISABILITY_OPTIONS = [{"label": "Yes, I have a disability, or have had one in the past", "value": "Yes"},
                      {"label": "No, I do not have a disability and have not had one in the past", "value": "No"},
                      {"label": "I do not want to answer", "value": "OptOut"}]
# Self-identification radios iCIMS renders without labels: group name -> (saved answer, standard wording by value).
SELF_ID_GROUPS = {"icims_f_Veteran": ("Veteran Status", None), "icims_f_Disability": ("Disability Status", DISABILITY_OPTIONS)}


def fill_veteran_form(frame, profile):
    """iCIMS's VEVRAA self-identification form (a "Portal Specific Form"): its three radios carry no labels, only the
    values ProtectedVeteran / NotProtectedVeteran / optout, so the saved "Veteran Status" answer picks one. The
    signature box is the e-signature for this voluntary form (the name and date are prefilled)."""
    from .custom_controls import option_match
    signature = frame.locator("#icims_f_signature")
    groups = [name for name in SELF_ID_GROUPS if frame.locator(f"input[type=radio][name={name}]").count()]
    if not groups and not signature.count():
        return 0, []
    filled = 0
    for name in groups:
        key, options = SELF_ID_GROUPS[name]
        saved = profile.lookup(key, choice=True)
        value = option_match(options or VETERAN_OPTIONS, saved) if saved is not None else None
        if not value:
            return 0, [f"{key.split()[0]} status (iCIMS self-identification form)"]
        choice = frame.locator(f"input[type=radio][name={name}][value={value}]")
        if not choice.is_checked():
            choice.check()
        filled += 1
    # Each self-identification page (veteran, disability) carries the same e-signature box.
    if signature.count():
        if not signature.is_checked():
            signature.check()
        filled += 1
    return filled, []
