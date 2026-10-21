# Contributing

This is an early public alpha. Small, reproducible fixes and documentation improvements are welcome.

1. Fork and clone the public repository; use Python 3.11+ and the editable installation in README.md.
2. Reproduce a bug with a minimal local HTML fixture or mocked service and fictional profile values.
3. Add a failing regression test, make the narrow fix, and run the affected tests plus the full suite when practical.
4. Review your diff for personal data and credentials before opening a pull request.

Do not attach `me/`, account files, browser profiles, raw application HTML, screenshots, resumes, mailbox exports, or production database/log files. A `.gitignore` is not a sanitizer for files already staged or tracked. Use reserved example addresses and replace identifying values throughout fixtures, filenames, attributes, and comments.

Keep unknown answers blocked, preserve human review and duplicate protection, and do not add bot-protection bypasses. Tests should use local fixtures and mocks, never live applications, accounts, inboxes, paid model requests, or automatic model downloads.

The shared question library stores question patterns and fact names, not personal answers. Inspect exported wording manually before sharing it: automated filters cannot prove that text is anonymous. The `share-library --pr` helper currently requires write access; outside contributors should use their fork and a normal pull request instead.

For security concerns, use the private reporting guidance in SECURITY.md rather than posting exploit details or private records in a public issue.
