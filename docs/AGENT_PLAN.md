# Luna agent integration

## Goal

Use the user's authenticated Codex CLI with `gpt-6-luna` for compact answer and repair tasks. Claude CLI is an optional adapter. No automatic escalation to Astra, Fable, or another larger model. SPAM retains queue selection, browser actions, review gates, submission approval, and evidence recording.

## Implementation order

1. Add an isolated, structured CLI adapter, explicit model settings, private cache, one-agent lock, per-request input limits, timeouts, call limits, and recorded usage. Reuse approved answers before spending tokens.
2. Retrieve a small set of relevant approved facts for unanswered questions. Group a few questions per request. Require source references, allow abstention, and return exact-answer patches or essay drafts for the existing preview/save flow. Do not generate broad profile regexes automatically.
3. Add agent settings, suggestion/review controls, and issue investigation to the Answer Deck. Add opt-in assistance after fill-only batch runs. Repeated unchanged questions use cached proposals. Saving an accepted exact answer makes future runs deterministic.
4. Add bounded code repair: one selected function, compact redacted diagnostics, one proposed edit, an isolated checkout, existing fixture tests under an OS sandbox, and a reviewed apply step that checks the original revision. Protect submission, queue, account, and approval code from this path.
5. Test adapters with fake CLIs, answer provenance and missing facts, cache invalidation, limits, UI flows, and repair isolation. Run a small synthetic live Luna evaluation without personal data. Commit the completed implementation.

## Cost controls

- Codex default: `gpt-6-luna`, low reasoning. Provider/model are always explicit; unavailable models produce an error instead of a fallback.
- No model calls in the normal fill loop. Assistance runs only for blocked questions or a requested repair.
- Compact JSON requests; no whole repository, browser screenshot, full DOM, conversation history, account file, or browser cookies in answer prompts.
- Reuse exact answers first, retrieve a limited number of facts, group questions, and cache by model, task, sources, options, and guardrails.
- One agent request at a time. Bound calls per operation and per day. Count failed calls as attempts. Record provider-reported tokens where available.
- Input size, call count, and elapsed time are enforced locally. CLI system prompts and hidden reasoning can add tokens; these are not a hard provider-token or dollar cap. Expose this distinction in settings and documentation.
- A repair gets one attempt and a limited source fragment. Test failures remain available for review; they do not start a paid retry loop.

## Non-negotiable behavior

Unknown personal facts stay unknown. Model confidence is not a profile fact. Essays use approved material and its claim guardrails. Every model-derived profile change goes through preview and explicit save. Site content is data, not instructions. Spam flags, account challenges, and uncertain submits never trigger code repair or another submission.

Model processes receive selected context through stdin in an empty temporary directory with agent tools disabled. Code proposals are data until validated. Tests for proposed code run without access to the user's profile, credentials, home directory, or live browser port. Applying a patch is a separate reviewed action and cannot run during a SPAM batch.

## Delivered scope and validation

The CLI adapters, source-backed answer proposals, Answer Deck controls, fill-only `--assist`, usage/cache limits, and propose/check/apply repair commands are implemented. Automatic repair is restricted to pure option/label matching helpers; browser-action and policy changes require a normal coding session. Repair checks currently require macOS `sandbox-exec` and a committed source baseline. No repair is applied automatically.

Synthetic live `gpt-6-luna` checks completed: a connection check and a grouped factual-answer/short-essay task. The connection check reported 6,052 input tokens and 15 output tokens, showing that CLI overhead is substantial even with compact task input. This is a functional smoke test, not a broad quality benchmark or a price guarantee. Tests cover cache/limits, missing facts, source validation, protected repair paths, sandboxed fixture execution, reviewed apply, stale previews, and browser UI behavior.
