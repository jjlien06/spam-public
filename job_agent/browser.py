"""Read-only inspection and explicitly requested profile filling; no submit API yet."""
from playwright.sync_api import sync_playwright
from .queue import platform
from .profile import Profile


LABEL = """e => {
    const labelled = (e.getAttribute('aria-labelledby') || '').split(/\\s+/)
        .filter(Boolean).map(id => e.ownerDocument.getElementById(id)?.textContent || '')
        .join(' ').trim();
    if (labelled) return labelled;
    if (e.getAttribute('aria-label')) return e.getAttribute('aria-label').trim();
    const labelTexts = Array.from(e.labels || []).map(label => {
        const copy = label.cloneNode(true);
        // A wrapping label may contain a select: its options are values, not its name.
        // Lever: drop its ✱ required marker and the location typeahead's status text.
        copy.querySelectorAll('input,select,textarea,button,script,style,.required,.dropdown-container').forEach(n => n.remove());
        return copy.textContent.replace(/\s+/g, ' ').trim();
    }).filter(Boolean);
    // Workday's SMS consent checkbox has both a concise field name ("SMS Opt-in")
    // and a long legal-consent label. The saved answer is keyed by the field name.
    if (e.type === 'checkbox' && labelTexts.length > 1) {
        const concise = labelTexts.filter(text => text.length <= 80).sort((a, b) => a.length - b.length)[0];
        if (concise) return concise;
    }
    const labels = labelTexts.join(' ');
    if (labels) return labels;
    // Lever's custom questions ("cards") name their control in .application-label, outside any label.
    const question = e.closest('li.application-question')?.querySelector('.application-label');
    if (question && e.type !== 'radio' && e.type !== 'checkbox') {
        const copy = question.cloneNode(true);
        copy.querySelectorAll('.required').forEach(n => n.remove());
        if (copy.textContent.trim()) return copy.textContent.replace(/\s+/g, ' ').trim();
    }
    // Workday questionnaires name a lone control by its fieldset legend.
    const fieldset = e.closest('fieldset');
    const legend = fieldset && fieldset.querySelector('legend');
    if (legend && fieldset.querySelectorAll('input:not([type=hidden]),select,textarea').length === 1)
        return legend.textContent.replace(/\*\s*$/, '').trim();
    // Ashby autocomplete: the fieldset's own label names its one control (not the "Start typing..." placeholder).
    const entry = fieldset || e.closest('.ashby-application-form-field-entry');
    const own = entry && entry.querySelector(':scope > label');
    if (own && entry.querySelectorAll('input:not([type=hidden]),select,textarea').length === 1)
        return own.textContent.replace(/\*\s*$/, '').trim();
    return (e.getAttribute('placeholder') || e.name || '').trim();
}"""


def inspect(url, profile, browser_dir, fill=False):
    report = {"platform": platform(url), "fields": [], "status": "needs_review"}
    answers = Profile(profile)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(browser_dir), headless=True)
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            for frame in page.frames:
                for control in frame.locator("input,textarea,select").all():
                    kind = control.get_attribute("type") or "text"
                    if kind in ("hidden", "submit", "button", "password") or not control.is_visible() or not control.is_enabled():
                        continue
                    label = control.evaluate(LABEL)
                    value = answers.lookup(label)
                    item = {"label": label, "type": kind, "state": "missing_answer"}
                    if value is not None:
                        item["state"] = "matched"
                        if fill:
                            if kind in ("checkbox", "radio", "file"):
                                item["state"] = "manual_control"
                            elif control.evaluate("e=>e.tagName") == "SELECT":
                                control.select_option(label=str(value))
                                item["state"] = "filled"
                            else:
                                control.fill(str(value))
                                item["state"] = "filled"
                    report["fields"].append(item)
            report["url"] = page.url
            report["note"] = "No buttons clicked; no submission attempted."
        finally:
            context.close()
    return report
