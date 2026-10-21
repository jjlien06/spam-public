# Security and data handling

## Early-alpha warning

This code has not had a comprehensive security audit. Automated secret scans and fixture tests reduce risk but cannot establish the absence of sensitive data or vulnerabilities. Use a dedicated browser profile and test with fictional data before providing real credentials. Local HTTP services are not a multi-user security boundary: any process running as your user may be able to interact with them. Do not expose the UI or Chrome debugging port on a network.

## Data flows

- Profiles, answers, credentials, queue records, cookies, mailbox-derived records, logs, and bug captures normally live under ignored `me/` or `SPAM_HOME`. Legacy root-level private files are also supported.
- Live employer forms receive information as it is filled and can autosave before submission. Account creation, verification, and submission make external changes.
- Optional Claude/Codex integrations can transmit selected saved facts, resume text, prompts, or connected mailbox content to external services using the user's configured CLI authentication. The broader console agent can have more access than the constrained answer-suggestion adapter. Review provider settings and tool permissions before use.
- The Ollama drafting integration targets a local endpoint by default. Optional model runtimes may download checkpoints. These are separate from hosted-provider features.
- Hiring-site account files may contain plaintext credentials protected by filesystem permissions, not encryption. Some mailbox credentials use the system credential store. Browser profiles contain session secrets; diagnostic HTML, JSON, screenshots, and review logs can contain personal data.

Never paste passwords or access tokens into an issue, pull request, shared prompt, or chat. Never commit private records. If a credential is exposed, revoke/rotate it with the provider; deleting a file or rewriting Git history does not revoke the credential.

## Reporting

Do not publish sensitive reports as ordinary issues. Prefer GitHub's **Report a vulnerability** option in this repository's Security tab if it is enabled. Otherwise, open a minimal issue requesting a private reporting channel without including exploit details, personal information, or credentials. Do not send raw captures or private application records.

Only the latest public alpha is currently maintained. No response-time or security-service commitment is implied.
