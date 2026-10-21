"""Local-first internship application assistant."""

# SPAM runs jobs in background tabs, where requestAnimationFrame may not fire; Playwright's
# wait_for_function polls on animation frames by default, so a pick that did apply could
# time out as "selection did not apply". Poll on a 100 ms timer unless a caller says otherwise.
try:
    from playwright.sync_api import Frame as _Frame, Page as _Page

    def _timer_polling(original):
        def wait_for_function(self, expression, *, arg=None, timeout=None, polling=None):
            return original(self, expression, arg=arg, timeout=timeout, polling=100 if polling is None else polling)
        return wait_for_function

    for _cls in (_Frame, _Page):
        if not getattr(_cls.wait_for_function, "_spam_timer_polling", False):
            _cls.wait_for_function = _timer_polling(_cls.wait_for_function)
            _cls.wait_for_function._spam_timer_polling = True
except ImportError:  # playwright not installed (docs/tests without a browser)
    pass
