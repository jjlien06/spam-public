# Turning stops into answers (for the user's AI agent)

When a form asks something SPAM has no answer for, the job stops as `needs_input` with the exact question and the
site's options. Answer from, in this order:

1. **A core fact** (`job-agent facts status`). If the question is a new wording of a fact question (work
   authorization, sponsorship, relocation, demographics...), map it — this also teaches the library:
   `job-agent answer "Exact question text" x --fact work_authorized`
   The rule is private, stores only the wording and the fact name, and is marked shareable (`SHARING.md`).
   **Check the direction.** If the wording asks the opposite of the fact ("Can you work here *without*
   sponsorship?" vs `needs_sponsorship`), add `--invert`. Then re-read the job's Review page to confirm the answer.
2. **Something the user already decided**: `job-agent decisions "topic words"` lists their past answers. Reuse
   and cite: `--note "per user YYYY-MM-DD: ..."`.
3. **A new fact about the user**: ask them (batch several questions into one message, with the site's options),
   then save exactly what they said:
   - one question: `job-agent answer "Exact question" "Their answer" --note "user YYYY-MM-DD"`
   - a family of wordings for a choice control: `job-agent answer "name" '[{"matches": "^yes\\b"}, "Yes"]' --pattern 'regex'`
   - a plain fact for free-text boxes: add `--text` to a `--pattern` rule.
4. **Never answer**: SSN, date of birth (the user types those), criminal history (only the user's own words),
   anything you'd have to guess.

## Essays and free text
Only from the user's own verified material (resume, their stories). Draft, show the user, save only what they
approve. Scope company-specific essays with `only_for` in the profile so they aren't reused elsewhere. Never claim
work "shipped" or numbers the user didn't give.

## Private vs shared
- `me/profile.json` answers, essays, and rules are stored privately on this Mac and must not be committed.
  Live forms receive entered answers, and optional hosted integrations can send selected facts externally; see `SECURITY.md`.
- The shared `library/` only maps wordings to fact names. It never holds an answer.
- Resolution order: private exact answer → private rule → private library rule → shared library → stop and ask.
