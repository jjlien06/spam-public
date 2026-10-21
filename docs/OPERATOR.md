# SPAM operator guide (for the chat console's agent)

You operate SPAM, the user's local internship-application agent, from the SPAM Console chat.
The user talks to you instead of to a coding session. Stay inside this project: run SPAM,
answer its questions from verified facts, and report back. Do not edit code under `job_agent/`
or `tests/` unless the user asks for a small fix; anything bigger is for a development session,
so say so. All commands run from the repository root; `job-agent` below means `.venv/bin/job-agent`.
The user's own files live in `me/` (git-ignored): `me/profile.json`, `me/accounts.json`, and `me/state/`.
Below, `profile.json` and `state/...` mean those files in `me/`.

## First thing in every conversation

`job-agent health` shows live workers, the browser, jobs waiting on a security code, queue
counts, and the latest status changes.
- Workers you did not start mean another session or an Approve is running: do not start fills
  until they finish (the CDP connection chokes past ~8 concurrent workers).
- Status changes you did not make were another session or the user's Approve. Read them before
  acting, so you never redo or contradict its work.
- `job-agent review --brief` lists what is waiting for the user's yes.

## Daily loop

1. **Pull**: `job-agent pull` (the console also pulls every hour). Simplify + speedyapply SWE/AI
   lists; targeting then skips non-CS roles, closed postings, and capped companies.
2. **Fill** to Review: `job-agent fill --count 20 --parallel 6` (never above 8 in total, one batch
   at a time). Jobs a private extension's mode claims are never in a plain fill: run each installed mode too,
   `job-agent fill --mode NAME --count 10 --parallel 4` (modes are the `MODE` names in `me/extensions/*.py`; the
   company cap applies in every mode). Refills and submits of those jobs need `SPAM_MODE=NAME` (Approve does this).
3. **Check** every job at Review: see *Reading a Review page*. Anything wrong:
   `job-agent hold ID "reason"` (takes it off the Approve list), fix the cause, refill.
4. **Ask for approval** (format below). The user ticks jobs and presses **Approve selected**; that
   is the permitted submit path for this console-agent workflow. `SPAM_CHAT_AGENT=1` blocks CLI `--submit`;
   standalone submission-capable code still exists. Never bypass the agent restriction or treat it as a security sandbox.
5. **After Approve**, run `job-agent health` and handle each outcome (see *After Approve*). Security
   codes are entered automatically (see *Gmail*).
6. **Unblock stuck jobs** with `job-agent deck` (see *Answering questions*), then refill them.
7. **Evidence**: the console records confirmation emails every 30 minutes (see *Gmail*); check
   `ambiguous` rows by hand (see *Evidence*).

Refill specific jobs: `job-agent batch --parallel 4 --ids ID1 ID2 ID3`. Type the IDs as separate
words: in zsh a `$VAR` holding several IDs is passed as one argument. Empty `--ids` is an error.
For a mode's jobs, prefix with `SPAM_MODE=NAME`. Refill only jobs that stopped before
submitting; never change a status by hand to make a job selectable.

## Reading a Review page

`job-agent review ID` prints the answer check, then every line the site will receive. The header
says `mode=` and how many times the user has already applied at that company. A job is
ready only when all of these hold:
- `CHECK: clean`. Any listed problem means hold.
- **Documents**: the application attaches the profile's resume (a mode may attach others). If a file is
  missing or wrong, hold.
- **Education**: school, degree, major and graduation date are present and match `facts.education`.
  Blank school or degree fields on Workday are a known failure: hold and refill.
- **Work experience (Workday)**: every saved entry is present with its title and dates. An empty
  section means hold.
- **GPA**: the profile GPA (`facts.education`), or the one the job's mode sets. GPA-range and GPA-cutoff answers must agree with the GPA used.
- **Term**: the season in `facts.availability`. If the posting is another term or a co-op, check the user's
  saved decisions and say so in the approval message.
- **Location**: the answer names a location the posting offers. Relocation agrees with `facts.willing_to_relocate`.
- **Work authorization**: authorization and sponsorship agree with `facts.work_authorization`.
- **Essays and free text**: every claim must trace to `facts`, `story_bank`, and
  `drafting_guardrails` in profile.json (full source: the summary.md named in profile `source`).
  Follow the user's own `drafting_guardrails` exactly. In general: never overstate impact, never present a
  team's work as the user's own, and never state a number the user's material doesn't give.
- **Demographics and identity**: they match the saved answers; nothing new is invented.
- **"Previously applied / worked here"**: consistent with the queue (SPAM injects company history).
- **Anything unusual**: SSN, date of birth, references, a required portfolio or writing sample, an
  assessment. Hold it and tell the user.

The snapshot is `state/runs/<id>.review.txt`. Trust it only while the job's status is `review`.

## Asking for approval

Send one message: a numbered list of `Company — Role — notes` for every clean job. Useful notes:
- "3rd application here"
- winter/spring co-op
- filled in a mode (name it)
- anything else the user would want to know

Then tell the user to tick them and press Approve selected. Never describe anything as submitted
before `health` shows it.

## After Approve

The console submits in a background batch (log in `state/batch-submit-ui-*.out`). Outcomes:
- `submitted`: done. Record evidence later.
- **Waiting on a code** (listed under `health` → `waiting_code`): entered automatically within a
  minute (see *Gmail*); run `job-agent codes` to do it now.
- **`needs_input` with a site error**: read `state/runs/<id>.json` first. Only if its note says
  "Submission not attempted" was nothing sent (otherwise treat it like `submit_uncertain`).
  Workday submit re-enters from the posting and refills every page, and a page can come back
  blank (seen: the Self-Identify Date and checkbox). Refill the job, check it, and ask for
  approval again.
- `submit_uncertain`: possibly sent. Never retry. Look for a confirmation email; tell the user.
- `hand_submit` (every Ashby job): Ashby blocks automated submits, so Approve fills the form again, runs
  both Review checks and leaves the window open; the user presses Submit there. `confirmations` turns the job
  `submitted` when the employer's email arrives. Until then it is not counted as applied. Never retry it.
- `spam_flagged`: the user finishes in the open window. Never retry or work around it.

## Gmail (codes, account verification, confirmations)

The console reads Gmail itself, through the user's Claude Code login (claude.ai Gmail connector,
read-only search). It needs no agent and works the same when you are ChatGPT/Codex:
- every minute while a submit waits on a Greenhouse security code, it enters the fresh codes;
- every 5 minutes while a Workday job waits on account verification, it opens the "Verify your
  candidate account" link and refills those jobs;
- every 30 minutes it records employer confirmation emails and rebuilds the ledger.

The **Check Gmail** button runs all three now. Its result shows next to the button; the log is
`state/mail-ui.out`. The same steps as commands (use these instead of reading Gmail yourself):
- `job-agent codes`: for each job waiting on a code, types only codes for that company sent after
  its submit, newest first, each at most once (`state/codes_used.json`). Results per job:
  `submitted`, `code rejected` (expired: refill and re-approve), `no open code window` (refill and
  re-approve), `no code yet` (the email hasn't arrived; wait a minute).
  The search covers the last 3 days. If Gmail was read through another connected client,
  `job-agent codes --messages-stdin` accepts its search results as a JSON array on standard input;
  each message needs `id`, local `date`, `subject`, and `snippet`. Keep code values out of command
  arguments and logs.
- `job-agent verify-accounts`: opens the link for each `email_verification_required` job whose
  email names the job's Workday host. `no_email` means the email hasn't arrived or is older than 2
  days. Refill the `verified` jobs (the watcher does this itself). An "Invalid Token" page is normal.
- `job-agent confirmations [--days 3]`: matches "thank you for applying" emails to submitted jobs
  with no evidence yet (company words, then role words). Identical emails that don't name the role
  are paired in submit order and labelled `[matched by order]`. `ambiguous` rows are left for you.
- `job-agent mail "GMAIL QUERY" [--limit N]`: any other search (subject, sender, snippet). For
  example a Workday one-time passcode: `job-agent mail 'from:otp.workday.com newer_than:1h'`.

The inbox connected to the user's Claude login is always searched; other inboxes (for example a personal
address some Workday tenants use) are searched too once the user has run `job-agent gmail-login` with a Google app password
(listed in `state/mailboxes.json`). If it isn't listed, ask the user to paste what you need. Never click password-reset links, never create a second account,
and never print passwords or anything from accounts.json. If Gmail fails ("connector connected?"),
tell the user to open Claude Code and check `/mcp` for the claude.ai Gmail connector.

## Answering questions

`job-agent deck` groups every open question with the jobs it blocks and the options each site
offered. For each question:
1. **A fact already in profile.json**: save it. Examples: contact, school, degree, graduation,
   GPA, citizenship, sponsorship, age, relocation, dates, saved demographics.
2. **Something the user already decided**: `job-agent decisions "regex"` lists every answer the
   user made (with dates and wording). Search by topic words, not the whole question: for
   example `decisions "co-?op|january|full.time"` for availability questions.
   Give the same answer to new wording of the same question, and cite the original decision in
   the note (`--note "per user YYYY-MM-DD: <what they said>"`).
3. **A new fact about the user**: a skill level, a preference, anything about their history, or an
   essay needing new material. Ask the user, batched into one message with the site's options,
   and save exactly what they say.
4. **Never answer**:
   - SSN, date of birth, bank details or passwords: the user does these by hand.
   - Criminal history beyond the user's own statement.
   - Anything you would have to guess.

Save (the result shows `replaced` when an exact answer overwrites an older one: check it kept what worked):
- an exact label: `job-agent answer "Exact question" "Value" --note "user YYYY-MM-DD: ..."`
- a family of choice questions: `job-agent answer "any" '[{"matches": "^option regex$"}]' --pattern 'question regex' --note "..."`

Notes that cite the user ("user YYYY-MM-DD: ...") are what `decisions` finds later. A note
records where the answer came from; never attribute to the user something they did not say.

A pattern applies to every future job, and the first matching rule wins (`--pattern` inserts at
the top). Keep its regex narrow and test it against the questions in `deck`. Then run
`job-agent profile-audit --profile profile.json` and fix any failure before refilling.

## Form controls

- Workday Skills: every verified skill (job_agent/skills.py), the posting's keywords first, up to 20.
- Workday Languages: one row per language in facts.languages, at the saved level.
- Ashby Education History: school, degree, field, dates. An end year the site doesn't offer
  is left blank with "Still Student?" ticked.
- react-datepicker graduation dates.

## Workday navigation and read-back

- Workday saving a step: after "Save and Continue" a tenant can keep the old step on screen
  for seconds with the footer button disabled, then render the next step's button hidden and disabled. SPAM waits
  (up to 30 s) for the footer button to become usable instead of stopping with "No unique supported navigation
  control". If that stop still appears on Workday, refill once; if it repeats, hold the job for a development session.
- Workday dropdown option ending in a period (for example, "I am not a Veteran."): the pick used to be reported as
  "selection did not apply" although it had worked; the read-back now ignores trailing punctuation.
- Workday search prompt that files a pick under a category ("How Did You Hear About Us?": the chip reads
  "Social Network: Linkedin"): the saved plain "LinkedIn" counts as picked, in the fill and in the Review check.
- Workday checkbox group whose question text appears twice in the legend ("What is your major or concentration?"):
  read as one question, so a saved answer ticks the matching box. Save such an answer as the exact option
  ("Computer Science"), never a broad regex: every matching box gets ticked.
- Saving an answer with `job-agent answer` REPLACES that question's whole saved value, including any synonyms.
  For a question that already has rules, add to the list in profile.json instead.

## Platform entry, uploads, and prefills

- Lever (jobs.lever.co) is a fillable platform. Company pages that embed Greenhouse (`?gh_jid=`) are rewritten at
  `pull` to their hosted board. Boards that redirect their job pages back to the company site (Coinbase, Epic Games)
  open Greenhouse's embed form (`/embed/job_app?for=<board>&token=<id>`) instead.
- Greenhouse Employment rows (`company-name-0`, `start-date-month-0` ...) take completed roles from facts.experience,
  in order: row 0 is the most recent role with an end date.
- A Workday upload with the resume's file name but a different size (an older resume left in a draft) is deleted
  and replaced with the current file.
- A preset answer SPAM has no saved answer for now stops the run as `<question> (filled in by the site; no saved
  answer)`. This covers a draft's or resume parser's choice on a real question (ending in "?") and prefilled text
  in a Lever card. A parser can confuse college and school graduation dates, and old drafts can retain incorrect
  eligibility answers. Verify the fact with the user, save the right answer and refill. Never approve the site's guess.
- Lever cards with a single checkbox ("Yes") are answered by the card's question.

## Verification and site-specific stops

- Lever can answer Submit with an hCaptcha challenge. Submission is not confirmed and fields may already have autosaved; the job becomes
  `hand_submit` with the filled window left open. The user solves the CAPTCHA and presses Submit. Never solve one.
- `code ID CODE` also finds Greenhouse code windows opened on the embed form (`…/embed/job_app?…&token=<id>`).
  Two codes from one company can't be told apart by the email; try one, and swap if it's rejected.
- Jane Street (board `janestreet`), Zipline (`flyzipline`), Optiver (`optiverus`), HRT (`wehrtyou`) are Greenhouse
  boards behind company sites; their queue rows point at the board URLs.
- An answer of `[]` (usually a per-posting `variant`) means "leave this optional field blank".
- Lever card questions end with a "✱" required mark; it is stripped before matching answers.
- Oracle Recruiting Cloud (`*.oraclecloud.com/hcmUI/CandidateExperience/...`, and Goldman's higher.gs.com) is platform
  `oracle`: email, terms (require this user's explicit consent; no consent is bundled), the six-digit PIN read from Gmail,
  then the form. Form filling is still being built; check the run before relying on it.
- Cookie banners: "Reject All" is preferred over "Accept All"; banners are recognized by a cookie class/id ancestor.

One-application firms also include HRT ("you can only apply for one role") and TikTok/ByteDance (two positions
across the company and affiliates, globally).

## One-application firms

Some firms take only one application per season or program. Akuna makes every Tech/Quant posting a "top
preference" acknowledgement (Trader roles are exempt). Virtu says "Please only apply to one internship position".
Chicago Trading Company posts the same internships on two boards (chicagotradingcampus and ctccampusboard), so treat
those as duplicates. When a firm allows only one, ask the user which role they prefer. Before filling a trading firm,
check the ledger for an existing application there and read the form for these questions.

## Assessments board

http://127.0.0.1:8770/assessments shows every assessment, game, video interview and screening found in Gmail as cards
(To do / In progress / Done / Expired) or a sortable table. The console rescans Gmail every 30 minutes; `job-agent
assessments scan --days 45` does it now (also `assessments list|add|update`). "Thanks for completing" emails move
cards to Done; the user moves the rest. Track and remind only: never start, answer or complete an assessment or
recorded interview for the user.

## Known blockers (do not burn time on these)

| What | Do |
| --- | --- |
| iCIMS, Taleo, company-custom sites | List for the user to apply by hand |
| Voltra / Akamai "Access Denied", `captcha_required` | User by hand; never bypass bot protection |
| jobright.ai links (intern-list.com) | Not applyable through SPAM |
| Workday tax-credit (WOTC) questionnaire | User by hand |
| Workday "How did you hear" prompt with no captured options | Leave; needs a development session |
| SSN / date of birth (Rowe's) | User by hand |
| Essays with no verified material (e.g. blockchain, a specific AI project) | Ask whether the user wants to write it; otherwise leave |

## Evidence

`job-agent confirmations` does this automatically. By hand (for `ambiguous` rows), match employer confirmation emails ("Thank you for applying", "application
received"/"submitted") to jobs by company and role. Record each one:

`job-agent confirm ID --email "Subject line" --at 2026-09-24T02:27:10`

Applications the user made by hand that SPAM never queued, known only from a confirmation email, go in
`state/email_applications.json` (`[{key, company, role, applied_at, email}]`; source "Email (by hand)"). If the
posting is in the queue, mark that job `applied` instead (with `confirm` evidence), so SPAM never applies twice.
Rejections, assessments, interviews and withdrawals go in `state/outcomes.json` (`{job_id or "email:<key>":
{outcome, at, email}}`) and show in the ledger's `outcome` column. Both inboxes count: search with `job-agent mail`.

Then rebuild the ledger with `.venv/bin/python -m job_agent.ledger me/state`. Report application
counts only from `state/applications.csv`; never count from queue statuses.

## Rules (from the user)

- **Roles**: only the kinds of roles the user chose. `targeting` in their profile (categories, required and
  rejected title patterns) enforces this; before a batch, sanity-check that the picked titles fit it.
- **Company cap**: at most 4 applications per company; companies in `targeting.no_cap_companies`
  has no cap. Different postings of one role are fine; never the same posting twice.
- **Answers**: never invent them. Essays only from verified material and its guardrails.
  Criminal-history answers only from the user's own statement.
- **Modes**: a private extension (`me/extensions/`) can define a mode that changes the profile for the jobs it claims.
  Fill and submit must use the same mode: Approve handles this automatically, and refills need `SPAM_MODE=NAME`.
- **Ashby saves**: Ashby saves every answer to its server as it is entered and checks that copy at Submit. SPAM
  queues those saves one at a time and waits for them before clicking Submit (`job_agent/ashby.py`). A "missing entry
  for required field" on a field the snapshot shows filled, or "We're updating your forms, please try again", means
  a save was lost: refill the job; nothing was sent.
- **Ashby = hand submit**: SPAM gets Ashby jobs all the way to a checked form, but never presses Submit.
  Say so in the approval message ("Ashby: you press Submit").
- **Anti-bot**: never work around Ashby's spam flag or any bot protection.
- **Skip**: jobs the user told you to skip (their `targeting.exclude_companies` and saved decisions), and the
  blockers above.
- **Browser**:
  - Windows open in the background; never bring the browser to the front, and never use headless.
  - Close windows you open, except code and hand-submit windows.
  - Never restart the browser without asking: that closes code and hand-submit windows.
- **Privacy**: never print or copy passwords, accounts.json, or the user's documents.
- **Replies**: keep them short. Say what you did, what's waiting for the user, and what you need
  from them.
