# Contributor and operator rules

Read README.md, SECURITY.md, and CONTRIBUTING.md before changing or operating this public alpha.

- Never invent personal facts, eligibility, dates, essays, consent, or credentials. Missing or conflicting facts stop the workflow.
- Coding and fixture tests must not contact real employers, create accounts, access inboxes, make paid model calls, or submit applications.
- When operating live forms, leave final approval/submission to the user. Preserve verification, hand-submit, and uncertain-submit browser sessions. Do not restart the shared browser without permission.
- Respect site rules and application limits. Do not bypass CAPTCHA, anti-bot controls, or restrictions on AI assistance. Never take an assessment or recorded interview for someone.
- Private records belong in ignored `me/` or `SPAM_HOME`, never in source, fixtures, issues, logs printed to a shared conversation, or commits. Redact captures before using them as fixture material.
- Read docs/OPERATOR.md and docs/agent/SAFETY.md before live operations; only one operator should control the queue at a time.
- Console-agent submission blocking is an environment/workflow safeguard, not a security sandbox. Standalone CLI submission paths exist. Do not claim the program is incapable of submission.
- Optional hosted-provider features can transmit selected personal data. Local-first storage does not mean all model calls or data processing stay on-device.
- Add focused regression tests for code changes. Use `.venv/bin/python -m pytest -q`; browser fixtures may take many minutes. Optional MLX tests can skip without the optional dependency.

The public repository has fresh history and no real applicant setup. Use a separate test profile and private state; do not copy the maintainer's private setup or assume a live demo is configured.
