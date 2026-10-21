"""Ashby forms save each answer to the server as it is entered (GraphQL ApiSetFormValue / ApiSetFormValueToFile)
and validate that server copy on Submit. Saves that overlap overwrite each other there, so answers SPAM entered
quickly went "missing" at random (Frostline, 2026-09-25). This page script sends those saves one at a time, in
order; every other request is untouched. Installed on every job window before the posting loads (it only acts on
Ashby's save operations, so other sites are unaffected).
"""

SERIALIZE_SAVES = """(() => {
  if (window.__spamSerializedSaves) return;
  window.__spamSerializedSaves = true;
  window.__spamPendingSaves = 0;  // Submit waits for this to reach 0 ("We're updating your forms, please try again")
  const original = window.fetch.bind(window);
  let chain = Promise.resolve();
  const isSave = init => {
    const body = init && init.body;
    return typeof body === 'string' && /"operationName"\\s*:\\s*"ApiSetFormValue(?:ToFile)?"/.test(body);
  };
  window.fetch = function (input, init) {
    if (!isSave(init)) return original(input, init);
    const run = () => original(input, init);
    window.__spamPendingSaves++;
    const result = chain.then(run, run);
    const settled = () => { window.__spamPendingSaves--; };
    chain = result.then(settled, settled);
    return result;
  };
})();"""


def install(page):
    """Queue Ashby's answer saves in this page (and its frames) from the next navigation on."""
    page.add_init_script(SERIALIZE_SAVES)


def wait_for_saves(page, timeout=30000):
    """True once no queued Ashby save is pending in any frame (pages without the queue count as settled)."""
    try:
        for frame in page.frames:
            frame.wait_for_function("() => !window.__spamSerializedSaves || window.__spamPendingSaves === 0", timeout=timeout)
        page.wait_for_timeout(500)  # Ashby re-renders from the last reply before it accepts Submit
        return True
    except Exception:
        return False
