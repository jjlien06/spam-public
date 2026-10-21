# Claude Code contributor guidance

Follow AGENTS.md, README.md, SECURITY.md, and CONTRIBUTING.md. Read docs/OPERATOR.md only when doing explicitly authorized live operations.

Use Python 3.11+ with an editable checkout. `.venv/bin/python -m pytest -q` runs the local fixture and mocked integration suite. `.venv/bin/python -m compileall -q job_agent` checks syntax. Do not invoke provider CLIs, live-site navigation, real inboxes, account creation, or submission to validate a code change.

Private profiles, credentials, state, browser sessions, and resumes must remain outside tracked files. Human review is mandatory; unknown facts stay blocked. The source contains submission-capable code, and optional hosted integrations can transmit data externally. Do not describe either boundary as an absolute technical guarantee.

Detailed implementation: queue.py stores state; batch.py coordinates browser workers; runner.py/custom_controls.py/platform modules observe, fill and verify forms; profile.py/facts.py/library.py resolve saved answers; review_check.py checks the site's review output. Preserve duplicate protection, read-back checks, and explicit review gates when fixing integrations.
