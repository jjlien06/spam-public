# Setting SPAM up for a person (for their AI agent)

You are helping someone who may not be technical. Do the typing; ask them only what you cannot know.
Read `SAFETY.md` first. Private files belong in `me/` (git-ignored). Storage is local, but live forms and
optional hosted AI/mailbox integrations transmit data externally; review `SECURITY.md` with the user before enabling them.

## 1. Install (once)

```sh
python3.11 --version || brew install python@3.11
python3.11 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/playwright install chromium
.venv/bin/job-agent setup --no-page      # creates me/ and prints what is still missing
```

Fix each `TODO` line it prints. SPAM needs Claude Code (`claude`) or Codex (`codex`) signed in; you are
probably one of them already. Save which one: the Setup page has a dropdown, or edit `me/settings.json` to
`{"ai": "claude"}` / `{"ai": "codex"}`.

## 2. Facts from the resume

Ask for the path to their resume PDF, then:

```sh
.venv/bin/job-agent facts draft ~/path/to/Resume.pdf
```

This prints a draft and saves nothing (it does remember the resume's path for uploads). Show the draft to the user
as a short list and ask them to correct anything. Then save only what they confirmed:

```sh
.venv/bin/job-agent facts confirm legal_first_name Alex legal_last_name Doe email alex@example.com gpa 3.6 graduation_date 2029-05
```

Values are JSON when they parse (`true`, `3.6`, `["Python","C++"]`), otherwise text.

## 3. Facts a resume can't give

Run `.venv/bin/job-agent facts status`. For every `--` line, ask the user the question in parentheses, one group
at a time, with plain choices. Demographic questions (gender, race, veteran, disability, LGBTQ+, pronouns,
Hispanic/Latino) always include "prefer not to say" → save `decline`. Save each group with `facts confirm`.

Never guess and never fill these from assumptions: work authorization, sponsorship, citizenship, clearance,
criminal history, demographics, conflicts of interest. If the user doesn't want to answer, leave it unconfirmed:
SPAM will stop and ask when a form needs it.

## 4. Targeting

Ask which kinds of roles they want (choices: Software Engineering; Data Science, AI & Machine Learning; Hardware
Engineering; Quantitative Finance; Product Management; Other), whether US-only, a per-company cap (default 4), and
any companies to never apply to:

```sh
.venv/bin/job-agent facts targeting '{"categories": ["Software Engineering"], "us_only": true, "company_cap": 4, "exclude_companies": []}'
```

## 5. Hiring-site login

Workday and similar sites need an account. The user types it themselves (you never see the password). Ask them to run,
in this chat, with the `!` prefix if their agent supports it, or in Terminal:

```sh
.venv/bin/job-agent account
```

## 6. Gmail (optional, recommended)

See `GMAIL.md`. Claude Code users connect Gmail at claude.ai → Settings → Connectors. Everyone else: an app password
with `.venv/bin/job-agent gmail-login` (the user types it).

## 7. First run

```sh
.venv/bin/job-agent pull            # imports postings and applies targeting
.venv/bin/job-agent chat            # the SPAM Console at http://127.0.0.1:8770
```

Then follow `RUN.md`. The first few fills will stop on questions: that's normal; see `ANSWERING.md`.

The same steps exist as a web page: `.venv/bin/job-agent setup` opens it on http://127.0.0.1:8771.
