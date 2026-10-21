"""Visible, local-model form preparation. No Codex browser service is used.

Models select opaque action IDs; only this executor owns browser locators and
saved answers. Password/account submission and final application submission are
manual handoffs. Unknown free-text questions can be drafted locally for review.
"""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urljoin
import json
import math

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

from .form_controls import Action, field_actions
from .models import Laya, LocalWriter
from .profile import Profile, normalize
from .queue import platform


@dataclass
class Observation:
    actions: list
    missing: list
    account: bool
    review: bool


def origin(url):
    parsed = urlsplit(url)
    return (parsed.scheme, parsed.netloc)


def observe(page, profile, entry="manual"):
    """Offer only implemented actions on the current top-level document."""
    actions, missing = field_actions(page, profile)
    account = page.locator('input[type=password]:visible,[data-automation-id=createAccountSubmitButton]:visible,[data-automation-id=signInSubmitButton]:visible,[data-automation-id=resetPasswordButton]:visible').count() > 0
    review = False
    # Complete exact saved fields first; never let navigation race a fill.
    if actions:
        return Observation(actions, missing, account, review)

    navigation = []
    for button in page.locator('button,input[type=submit],input[type=button],a,[role=button]').all():
        if not button.is_visible() or not button.is_enabled() or button.get_attribute('aria-hidden') == 'true':
            continue
        text = normalize(button.get_attribute('aria-label') or button.get_attribute('value') or button.inner_text())
        automation = button.get_attribute('data-automation-id')
        if not account and text in {'submit', 'submit application', 'send application'}:
            review = True
        if account:
            # Only the navigation link to signup; never createAccountSubmitButton.
            if entry == 'create-account' and automation == 'createAccountLink':
                navigation.append(Action('open_create_account', 'Open Create Account form', 'click', button))
            continue
        if text == 'apply manually':
            navigation.append(Action('apply_manually', 'Open manual application', 'click', button))
        elif text in {'next', 'continue', 'save and continue'} and not missing:
            navigation.append(Action('next', 'Continue completed page', 'click', button))
        elif platform(page.url) == 'workday' and automation == 'adventureButton':
            destination = urljoin(page.url, button.get_attribute('href') or '')
            if origin(destination) == origin(page.url) and urlsplit(destination).path.endswith('/apply'):
                navigation.append(Action('open_application', 'Open application choices', 'click', button))

    # Workday can render Apply Manually as a div without a role.
    manual = page.get_by_text('Apply Manually', exact=True).filter(visible=True)
    if not account and manual.count() == 1:
        navigation = [Action('apply_manually', 'Open manual application', 'click', manual)]
    elif review:
        navigation = []
    # Ambiguous duplicate actions require a user rather than choosing by DOM order.
    ids = [action.id for action in navigation]
    if len(ids) != len(set(ids)):
        missing.append('Ambiguous navigation controls')
        navigation = []
    return Observation(navigation, missing, account, review)


class LocalPlanner:
    def __init__(self, laya=None, writer=None, min_probability=0.6):
        self.laya = laya if laya is not None else Laya()
        self.writer = writer
        self.min_probability = min_probability

    def choose(self, observation):
        # Small, one-candidate routing keeps Laya's 512-token context bounded.
        candidate = observation.actions[0]
        state = 'Available safe action: ' + candidate.description + '. Goal: prepare the application.'
        prediction = self.laya.predict(state, ['perform', 'pause'])
        answer = prediction.get('answers', {}).get('action', {})
        probability = answer.get('probabilities', {}).get('perform', 0)
        valid_probability = isinstance(probability, (int, float)) and not isinstance(probability, bool) and math.isfinite(probability)
        trace = {'model': 'laya', 'candidate': candidate.id,
                 'choice': answer.get('choice'), 'confidence': answer.get('confidence'),
                 'probability': probability, 'act_probability': answer.get('action', {}).get('act_probability')}
        if answer.get('choice') == 'perform' and valid_probability and self.min_probability <= probability <= 1:
            return candidate, trace
        # A general local LLM can break ties, but it still cannot invent actions.
        if self.writer is not None:
            try:
                approved = self.writer.choose_action(candidate.description)
            except Exception as exc:
                trace['fallback_error'] = type(exc).__name__
                return None, trace
            trace['fallback'] = approved
            if approved == 'perform':
                return candidate, trace
        return None, trace


def execute(action):
    if action.kind == 'fill':
        action.control.fill(str(action.value), timeout=5000)
    elif action.kind == 'select':
        action.control.select_option(label=str(action.value), timeout=5000)
    elif action.kind == 'select_value':
        action.control.select_option(value=str(action.value), timeout=5000)
    elif action.kind == 'checkbox':
        action.control.set_checked(action.value, timeout=5000)
    elif action.kind == 'click':
        action.control.click(timeout=5000)
    else:
        raise ValueError('Unsupported local action')


def step(page, profile, planner, allowed_origin, entry='manual'):
    if origin(page.url) != allowed_origin:
        return {'status': 'needs_input', 'reason': 'Browser left the authorized origin'}
    observation = observe(page, profile, entry)
    if not observation.actions:
        if observation.account:
            return {'status': 'account_handoff', 'reason': 'Complete password/account steps in the visible browser'}
        if observation.review and not observation.missing:
            return {'status': 'ready_for_review', 'reason': 'Application submission remains manual'}
        return {'status': 'needs_input', 'missing': observation.missing,
                'reason': 'Missing saved answers or unsupported controls'}
    action, trace = planner.choose(observation)
    if action is None:
        return {'status': 'model_uncertain', 'decision': trace}
    # Reobserve after inference: user/browser may have changed the live form.
    fresh = observe(page, profile, entry)
    if origin(page.url) != allowed_origin:
        return {'status': 'needs_input', 'reason': 'Browser left the authorized origin'}
    matches = [item for item in fresh.actions if item.id == action.id and
               item.description == action.description and item.kind == action.kind and item.value == action.value]
    if len(matches) != 1:
        return {'status': 'page_changed', 'reason': 'Action no longer matches current page'}
    execute(matches[0])
    return {'status': 'acted', 'action': action.id, 'decision': trace}


def run(url, profile_data, browser_dir, report_path, *, entry='manual', max_steps=30,
        model='qwen3.5:4b', use_writer=False, planner=None, interactive=True):
    if urlsplit(url).scheme != 'https' or urlsplit(url).username or urlsplit(url).password:
        raise ValueError('An HTTPS job URL without embedded credentials is required')
    profile = Profile(profile_data)
    planner = planner if planner is not None else LocalPlanner(writer=LocalWriter(model) if use_writer else None)
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    history = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(browser_dir), headless=False)
        try:
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            print('Visible local browser opened. Laya and the local LLM select bounded actions.', flush=True)
            for index in range(max_steps):
                try:
                    result = step(page, profile, planner, origin(url), entry)
                except Exception as exc:
                    result = {'status': 'needs_input', 'reason': type(exc).__name__}
                history.append(result)
                report = {'status': result['status'], 'history': history,
                          'note': 'No automatic account or application submission'}
                report_path.write_text(json.dumps(report, indent=2))
                report_path.chmod(0o600)
                print(json.dumps(result), flush=True)
                if result['status'] == 'acted':
                    # Yield to browser events, including delayed SPA page changes.
                    page.wait_for_timeout(500)
                    continue
                if not interactive:
                    return report
                try:
                    command = input('Complete any manual step in the visible browser; Enter resumes, q closes: ').strip().lower()
                except EOFError:
                    return report
                if command == 'q':
                    return report
            report = {'status': 'step_limit', 'history': history, 'note': 'No automatic account or application submission'}
            report_path.write_text(json.dumps(report, indent=2))
            report_path.chmod(0o600)
            return report
        finally:
            context.close()
