# Gmail (for the user's AI agent)

SPAM reads mail only to: enter emailed security codes into a waiting submit, open hiring-site account-verification
links, match confirmation emails to submitted jobs, and track assessments. It never sends mail.

Two ways in (either or both):

- **Claude connector** (Claude Code users): connect Gmail at claude.ai → Settings → Connectors. SPAM runs a
  headless `claude -p` that calls the connector. Nothing to store.
- **IMAP app password** (everyone; the only way for Codex users): the user creates a Google app password
  (Google Account → Security → 2-Step Verification → App passwords), then types it into
  `.venv/bin/job-agent gmail-login`. It's stored in the macOS Keychain, tested before saving, and listed in
  `me/state/mailboxes.json`. Add a second inbox the same way.

To use IMAP only, set `"gmail": "imap"` in `me/settings.json`. Without Claude Code installed, IMAP is used automatically.

Commands: `codes`, `verify-accounts`, `confirmations --days 7`, `mail "gmail search"`, `assessments scan`.
Never click password-reset links, never create a second account, never print a password.
