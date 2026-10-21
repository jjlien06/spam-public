# Safety rules (non-negotiable, for any agent operating SPAM)

1. **Leave submission to the user, by approved id.** Console-agent CLI submission is blocked by the
   `SPAM_CHAT_AGENT=1` workflow setting; standalone submission paths exist. Do not bypass the restriction. Never
   submit as a side effect of a check. Never resubmit an applied, uncertain, spam-flagged or hand-submit job.
2. **Never invent an answer.** No saved answer or confirmed fact → stop and ask. Essays only from the user's own
   verified material. Criminal history only in the user's own words.
3. **SSN and date of birth are always typed by the user.**
4. **Never work around bot protection**: captchas, Ashby's spam flag, rate limits, "one application per 60 days".
5. **Privacy**: never print, copy or commit `me/` (profile, accounts, state), passwords, or the user's documents.
   Nothing personal goes into tracked files. `tests/test_no_personal_data.py` checks configured private identifier
   rules only when those rules exist; it does not prove that a sanitized checkout is free of personal data.
6. **Respect the user's limits**: per-company cap, excluded companies, targeting.
7. **Browser**: background windows only, close what you open, keep total workers ≤ 8, never restart the browser
   without asking.
8. **One agent session operates SPAM at a time.**
