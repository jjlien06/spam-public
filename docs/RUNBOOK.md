# SPAM daily runbook

Run commands from the repository root with the virtual environment installed. Only one AI session should operate SPAM at a time. Keep live work in the shared visible browser, in background windows, with at most 8 workers at once. Day to day, the SPAM Console (`job-agent chat`) runs this loop; its agent follows `docs/OPERATOR.md`, which is the more current, operator-level version of this page.

## Daily loop

1. **Refresh and choose work.** Import a fresh Simplify Tracked Jobs export when available, then sync and inspect the next targets. The export is needed to prevent another application to a job applied to outside SPAM.

   ```sh
   .venv/bin/job-agent import-tracker /path/to/Simplify_Tracked_Jobs.csv
   .venv/bin/job-agent sync
   .venv/bin/job-agent targets --top 10
   .venv/bin/job-agent profile-audit --profile profile.json
   ```

   Resolve profile audit failures before filling. `sync` and `targets` update targeting statuses; they do not open application sites. Default batch selection takes only `queued` jobs, one per company, and respects the company cap and companies waiting on input. Explicit `--ids` bypasses selection, so check each job's history first.

2. **Fill a small batch to Review.** Start with two jobs. `batch` starts the visible shared browser if needed. Filling can create or autosave a draft and can create an account only when account configuration explicitly permits it.

   ```sh
   .venv/bin/job-agent batch --count 2 --parallel 2 --platforms workday,greenhouse
   ```

   For Ashby, use fill-only runs with `SPAM_KEEP_WINDOW=1` so the user can finish in the filled window. Never work around a spam or bot-detection flag.

   ```sh
   SPAM_KEEP_WINDOW=1 .venv/bin/job-agent batch --count 2 --parallel 2 --platforms ashby
   ```

3. **Read the unanswered questions.** The batch prints a question deck and writes `state/last-batch.json`. Each worker writes `state/runs/<id>.json` and `<id>.log`; the JSON includes the precise runtime status, missing questions, and captured options. Review these locally because reports can contain personal information. A diagnostic such as “Could not fill this page” needs investigation, not a saved answer.

4. **Save verified answers, then refill those jobs.** Open the Answer Deck and choose an option or enter a verified fact. Preview the resulting answers and affected aliases, then select **Save to profile**. The native `job-agent watch` controller also supports exact answers. Profile changes take effect on the next run. Use only the user's verified facts and the resume guardrails in `summary.md` for essays. Never infer a criminal-history answer. Passwords and security codes belong to account handling, not the answer profile.

   ```sh
   .venv/bin/job-agent answer-deck
   # Open the printed local URL. Ctrl-C stops the server.
   ```

   Choice `patterns` use the first matching rule, including an exception that stops the lookup. Free text uses exact answers or `text_patterns` for plain facts. Test a proposed rule against the actual questions and options before saving it, including opposite formulations such as “18 or older” and “under 18.” Recheck existing matches when changing rule order. Add an appropriate `review_checks` rule when correcting an answer that was previously submitted incorrectly.

   **New choice rule** appends a rule and previews its effective results on the captured questions. Earlier rules, exceptions, exact answers, and company history are included. If options differ across employers, every captured set is checked. A changed profile or report invalidates the preview. Review failures and account-code stops are listed separately and cannot be saved as answers. This page does not replace live read-back and Review checks. Keep only one profile editor active.

   ```sh
   .venv/bin/job-agent profile-audit --profile profile.json
   .venv/bin/job-agent batch --parallel 2 --ids JOB_ID_1 JOB_ID_2
   ```

   Retry only jobs known to have stopped before submission. Never reset an applied, uncertain, or hand-submit job to `queued` just to make it selectable.

5. **Review what the site will receive.** Read every `state/runs/<id>.review.txt`, check the resume and all answers, and resolve any mismatch. A prior review file can be stale: use it only when the latest run for that job returned `ready_for_review`. SPAM also reads filled values back and applies two final checks: work/education review auditing and the full answer/review-rule check. A check failure blocks Submit even with `--submit`.

6. **Get one explicit yes for the named batch, then submit only those IDs.** In the SPAM Console the yes is the user's **Approve selected** click, which runs the command below (each job in the mode it was filled in). Present the company, role, and job ID for every proposed submission. A previous approval for another batch is not approval for this one. Keep the approved list fixed; changed answers require review before submission. Ashby jobs reserved for hand submission stay with the user.

   ```sh
   # Only after the user explicitly approves these exact jobs:
   .venv/bin/job-agent batch --submit --parallel 2 --ids APPROVED_ID_1 APPROVED_ID_2
   ```

   Do not use automatic batch selection with `--submit`, or a controller auto-submit checkbox as a substitute for the user's yes. The older `prepare --submit` path must not be used to bypass batch safeguards.

7. **Reconcile the result and update the ledger.** A click is not proof of submission. Require a site acknowledgement or recorded application ID. Preserve uncertain results for manual reconciliation. Finish security-code steps in the retained window; close windows after their work is complete, including leftover blank tabs. Keep only windows the user still needs for a code or hand submission.

   ```sh
   .venv/bin/python -m job_agent.ledger me/state
   ```

   Report counts from this rebuilt ledger. `state/applications.csv` deduplicates postings by `job_key` and includes source, time, status, and evidence. Its total includes the separately labelled `SPAM (unconfirmed)` entries; do not describe those as confirmed submissions. Review duplicate and missing-time warnings.

## Queue statuses and run reports

The queue is `state/jobs.sqlite3`; `status_log` records status/note changes. `job-agent status` prints queue rows and may expose private notes, so keep its output local. A worker report may use a more specific status than the queue. In the current batch path, review failures, account stops, and `email_code_required` are stored as `needs_input` with their runtime status in the note. Older single-job paths may store runtime statuses directly.

| Queue status / runtime status | Meaning and next action |
| --- | --- |
| `queued` | Untouched eligible work; default batch selection may fill it. |
| `review` / `ready_for_review` | Filled and checks passed. Inspect the latest review snapshot and obtain explicit approval. |
| `needs_input` | A missing answer, site error, account issue, or other stop. Read the report's status and `missing` before deciding whether a fill-only retry is safe. |
| `review_flagged`, `review_mismatch` | An answer or review summary failed a check. Correct the cause, refill, and review again. Usually stored as `needs_input` by batch. |
| `email_code_required` | Submission is waiting on an emailed code. Keep the existing window open and finish there; do not start another submission. Usually stored as `needs_input` by batch. |
| `submitted` | Site confirmation recorded. Never resubmit. |
| `applied`, `already_applied` | Applied via Simplify import, elsewhere, or confirmed by the site. Never resubmit. Batch maps `already_applied` to `applied`. |
| `submit_uncertain` | Submit was clicked without a reliable acknowledgement. Treat as possibly sent. Never retry Submit; reconcile site/email evidence manually. |
| `spam_flagged` | The site blocked an automated submission. Leave the window for the user; move to `hand_submit` when recording that handoff. Never retry automatically or evade the flag. |
| `hand_submit` | The user must finish in the existing filled window. SPAM must never resubmit. |
| `manual` | Reserved for the user by targeting preferences. Do not include in automated batches. |
| `skipped` / `skipped_transcript` | Excluded by targeting, duplicate listing, or required transcript policy. Read the reason; do not override without resolving it. |
| `closed` / `posting_closed` | Posting is closed. No retry. |
| `needs_review` | An older inspection command failed. Inspect the error and evidence before any further action. |
| Other runtime stops (for example `paused` or account verification) | No readiness implied. Inspect the latest report and account state before proceeding. |

`batch.NO_RESUBMIT` contains `submitted`, `applied`, `already_applied`, `submit_uncertain`, `spam_flagged`, and `hand_submit`. Both batch submission and `run-one --submit` check the selected row's status. This is not permission to retry a different listing of the same job: check the shared `job_key`, tracker, and status history too. Never re-apply to a company the user has excluded (`targeting.exclude_companies`). Never use a changed URL, another account, or a reset status to bypass these rules.

## Recovering a failed run

1. **Stop and identify the last action.** Read the job's latest JSON report, log, queue note, and status history. A crash or timeout can leave an older JSON/review file behind. Compare timestamps. The batch's “Run crashed or timed out” result does not prove that nothing was sent and may not update the queue.
2. **Protect possible submissions.** If Submit might have been clicked, hold the job for manual reconciliation. Check the existing visible window, portal application history, and matching confirmation email. Guest-apply sites such as FIS may not show “already applied”; absence of that message is not evidence that retrying is safe. Keep the original evidence and never delete history to permit a retry.
3. **Resolve the observed blocker.** For an unanswered question, save an exact verified answer. For rejected answers or slow controls, inspect `state/bugs/<time>-<host>.{html,json,png}` and build a redacted local fixture before changing fill logic. For a vanished frame, preserve the capture/log and test navigation handling locally. Do not disable read-back checks or review gates to get past a failure.
4. **Reconcile account state.** Some Workday tenants require email verification and others do not. Follow the observed state. Pending account creation is reconciled through sign-in; do not repeat registration or password resets after an uncertain result. Keep a Greenhouse code window open. Never bypass bot detection.
5. **Retry only a known pre-submit failure.** Use `batch --parallel 2 --ids ID` without `--submit`, then repeat review and approval. Normal runs re-enter from the posting to recover expired sessions. For a killed worker, close its abandoned window after preserving any needed evidence; do not close the user's pending hand-submit/code windows.
6. **Record evidence and rebuild.** The current CLI has no general reconciliation/mark command. A maintainer can use `Queue.mark(id, status, note)` after verifying evidence, preserving `status_log`; the note must explain the source. Mark a hand-submission as applied only after the user or site confirms it. Store matched confirmation metadata in `state/confirmations.json`, then rebuild the ledger. Do not mark an application submitted from a click alone.

## Development checks and privacy

Read `AGENTS.md`, `README.md`, and the relevant tests before editing. Test the current checkout before committing; use the full suite at a baseline or required gate and targeted fixtures for subsequent changes.

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/test_platforms.py -k combobox
```

The full suite normally takes roughly 15–17 minutes. Browser fixtures are local and headless. On macOS, sandbox errors such as `bootstrap_check_in ... Permission denied` or an immediate `TargetClosedError` require an environment where Chromium can launch; a sandbox failure does not establish a code regression.

Keep `profile.json`, `accounts.json`, browser sessions, resumes, `summary.md`, and everything in `state/` private. Do not print their contents in shared output or commit them. Redact real names, addresses, phone numbers, credentials, and application details from new fixtures. Inspect the staged diff and stage files explicitly rather than using `git add .`.
