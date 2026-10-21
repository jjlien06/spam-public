# Daily loop (for the user's AI agent)

The full operator guide is `docs/OPERATOR.md`; this is the short version. Commands are `.venv/bin/job-agent ...`.

1. `health` — live workers, the browser, queue counts, recent changes. Anything you didn't do was the user or another session.
2. `pull` — new postings (Simplify, speedyapply, intern-list.com) and targeting. The console also does this hourly.
3. `fill --count 12 --parallel 6` — fills the next jobs to Review in background windows (never submits).
   Keep total workers ≤ 8.
4. `review --brief` — every job at Review with its answer check. Read each job's questions and answers yourself
   (`review ID`). Anything wrong: fix the answer (`ANSWERING.md`), `hold ID "reason"`, and refill later.
5. Ask the user **one** yes/no for the whole checked batch, listing company — role. They press **Approve** in the
   console. Console-agent `--submit` is blocked by `SPAM_CHAT_AGENT=1`; never bypass this workflow restriction.
   Standalone submission code exists, so this is not an unbypassable security boundary.
6. `deck` — stuck jobs grouped by question. Answer what the facts cover; batch the rest into one message to the user.
7. Security codes / verification links arrive by Gmail: `codes`, `verify-accounts` (the console runs these every
   few minutes). Confirmations: `confirmations`.
8. After a batch, if the user mapped new wordings to facts: preview and contribute them to everyone's library
   (`SHARING.md`: `share-library`, then `share-library --pr`).
9. Assessments (coding tests, games, video interviews) are tracked on the console's Assessments board. SPAM never
   takes them.

Close every browser window you open. Never restart the browser without asking: it closes windows waiting on the user.
