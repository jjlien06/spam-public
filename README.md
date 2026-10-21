# SPAM — Super Powered Application Machine

A local-first, human-supervised job-application assistant. SPAM imports internship listings into a deduplicated queue, fills browser forms from confirmed answers, checks the resulting values, and stages applications for human review.

**Status: early public alpha.** This is a sanitized source snapshot following private development. It has a fresh Git history; applicant profiles, credentials, browser sessions, application records, and raw captures are not included. Browser integrations are incomplete and can break when hiring sites change. Fixture tests do not establish reliability on live sites.

## What is here

- Listing import, targeting, duplicate detection, and a SQLite application queue.
- Playwright browser integrations for native and custom controls, repeated education/work sections, uploads, and review checks.
- Confirmed-answer lookup, shared question rules, and an Answer Deck with preview-before-save.
- A loopback-only console with human approval controls, application history, and assessment tracking.
- Optional CLI-backed AI assistance, local model integrations, and a constrained code-repair proposal workflow.
- Local HTML fixtures and mocked service regression tests.

## Safety and privacy boundaries

- **Unknown personal facts must stay unknown.** Missing or conflicting saved answers stop the workflow. Review every answer, consent, uploaded document, and eligibility claim yourself.
- **Filling transmits data.** Employer sites can receive or autosave answers before Submit. Signing in, creating an account, following verification links, and submitting are real external actions.
- **Submission code exists.** Console-agent runs use `SPAM_CHAT_AGENT=1` to block CLI submission, and the console has explicit approval controls. Standalone CLI `--submit` paths and the optional desktop controller can submit. These are workflow safeguards, not an unbypassable security boundary. Keep automated submission disabled while evaluating the software.
- **Local-first is not offline-only.** State is stored locally, but live sites receive entered information. Optional Claude/Codex features can send selected profile facts, resume text, prompts, or mailbox content to the configured provider. Read [SECURITY.md](SECURITY.md) before enabling integrations. No claim is made that all personal data stays on-device.
- Account files may contain plaintext passwords with restrictive file permissions. Browser profiles and bug captures can contain sensitive information. Never upload them to issues or pull requests.
- Respect employer rules, application limits, bot protection, and restrictions on AI assistance. Never use this software to bypass a CAPTCHA or complete an assessment or interview on someone else's behalf.

## Quick start

The primary development target is macOS with Python 3.11+. Some integrations require macOS, a supported Chromium build, separately installed provider CLIs, or optional model dependencies. Use an editable source checkout; standalone wheel installation is not supported in this alpha because some shared resources are checkout-relative.

```sh
git clone https://github.com/jjlien06/spam-public.git
cd spam-public
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m playwright install chromium
.venv/bin/job-agent --help
```

Start with the examples and local tests. No real profile or account is bundled. Setup is interactive and can involve an external AI provider; review the data-flow notes before choosing it.

```sh
.venv/bin/job-agent setup   # local setup UI, normally http://127.0.0.1:8771
.venv/bin/job-agent chat    # local console, normally http://127.0.0.1:8770
```

Your normal private layout is:

```text
me/
  profile.json       # confirmed facts, answers, preferences, document paths
  accounts.json      # hiring-site credentials; do not commit
  state/             # queue, application records, browser sessions, captures
```

`SPAM_HOME` overrides the private home. Legacy root-level `profile.json`, `accounts.json`, and `state/` are also supported. All are excluded from Git. Do not expose the local UI or browser debugging port to other machines.

## Development

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q job_agent
```

The browser suite can take many minutes. The optional MLX navigation test skips when its dependency is absent; the macOS sandbox test skips on other platforms. Provider integrations are mocked in tests; a passing suite does not certify live sites, external model access, or a complete security audit.

For browser regression work, reproduce with minimal synthetic markup and fictional answers. Do not commit raw employer pages, screenshots, or application records. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Guides and architecture

- [Setup](docs/agent/SETUP.md), [daily workflow](docs/RUNBOOK.md), [operator reference](docs/OPERATOR.md)
- [Confirmed answers](docs/agent/ANSWERING.md), [safety rules](docs/agent/SAFETY.md), [sharing rules](docs/agent/SHARING.md)
- [Optional live demo setup](docs/DEMO.md) — requires your own private configuration and can contact a real employer
- [Public release scope](docs/PUBLIC_RELEASE.md)

`job_agent/queue.py` handles queue state; `batch.py` coordinates browser workers; `runner.py`, `custom_controls.py`, and platform modules fill and verify forms; `profile.py`, `facts.py`, and `library.py` resolve saved answers; `review_check.py` checks review pages. Local UI, mailbox, AI, and repair modules are separate integrations. The detailed operator guides describe operational policy, not a guarantee that the program cannot perform the underlying actions.

## License

MIT. Copyright © 2026 Jeremy Lien. See [LICENSE](LICENSE). Third-party dependencies, services, models, and websites retain their own licenses and terms; none are relicensed by this repository.
