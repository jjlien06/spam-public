"""Ashby saves every answer to its server as it is entered (GraphQL ApiSetFormValue) and validates that copy on
Submit. Overlapping saves overwrite each other on the server (Frostline, 2026-09-25: a different set of answers
was "missing" on every try), so SPAM's pages send them one at a time."""
from playwright.sync_api import sync_playwright

from job_agent.ashby import SERIALIZE_SAVES

# Stands in for the browser's own fetch: each save takes a different time, and overlapping ones are counted.
FAKE_FETCH = """(() => {
  window.__inFlight = 0; window.__maxInFlight = 0; window.__order = [];
  window.fetch = (url, init) => new Promise(resolve => {
    const body = (init && init.body) || '';
    const save = /ApiSetFormValue/.test(body);
    if (save) { window.__inFlight++; window.__maxInFlight = Math.max(window.__maxInFlight, window.__inFlight); }
    const n = JSON.parse(body || '{}').n;
    setTimeout(() => { if (save) { window.__inFlight--; window.__order.push(n); }
      resolve(new Response(JSON.stringify({ok: n}))); }, save ? 60 - n * 10 : 5);
  });
})();"""


def test_ashby_saves_are_sent_one_at_a_time_in_order(tmp_path):
    page_file = tmp_path / "form.html"
    page_file.write_text("<!doctype html><html><body>form</body></html>")
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.add_init_script(FAKE_FETCH)
        page.add_init_script(SERIALIZE_SAVES)
        page.goto(page_file.as_uri())
        replies = page.evaluate("""async () => {
          const save = n => fetch('/api/non-user-graphql?op=ApiSetFormValue', {method: 'POST',
            body: JSON.stringify({operationName: 'ApiSetFormValue', n})}).then(r => r.json());
          const other = fetch('/other', {method: 'POST', body: JSON.stringify({operationName: 'ApiJobPosting', n: 9})});
          const results = await Promise.all([save(1), save(2), save(3), save(4), save(5), other]);
          return results.slice(0, 5);
        }""")
        assert replies == [{"ok": n} for n in (1, 2, 3, 4, 5)]
        assert page.evaluate("window.__maxInFlight") == 1
        assert page.evaluate("window.__order") == [1, 2, 3, 4, 5]
    finally:
        browser.close(); p.stop()


def test_pending_saves_are_counted_so_submit_can_wait_for_them(tmp_path):
    page_file = tmp_path / "form.html"
    page_file.write_text("<!doctype html><html><body>form</body></html>")
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.add_init_script(FAKE_FETCH)
        page.add_init_script(SERIALIZE_SAVES)
        page.goto(page_file.as_uri())
        page.evaluate("""() => { for (const n of [1, 2, 3]) fetch('/g', {method: 'POST',
            body: JSON.stringify({operationName: 'ApiSetFormValue', n})}); }""")
        assert page.evaluate("window.__spamPendingSaves") == 3
        from job_agent.ashby import wait_for_saves
        assert wait_for_saves(page) is True
        assert page.evaluate("window.__spamPendingSaves") == 0 and page.evaluate("window.__order") == [1, 2, 3]
    finally:
        browser.close(); p.stop()
