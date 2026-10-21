# Troubleshooting (for the user's AI agent)

| Symptom | Fix |
|---|---|
| `No profile found at me/profile.json` | Run setup (`SETUP.md`). |
| `No unique supported navigation control` | The posting page had no single Apply/Next. Lever and Ashby postings open their form directly now; for other sites open `job-agent inspect ID` and report the site. |
| `needs_input` with a question | `ANSWERING.md`. |
| `(saved answer not among options)` | The saved value doesn't match any option the site offered. Look at the options in the job's report (`me/state/runs/ID.json`) and save a `{"matches": ...}` rule that picks the right one. |
| `review_flagged` | The Review page disagreed with the profile. Read the listed lines; fix the answer or hold the job. Never submit over it. |
| `email_code_required` | A security code was emailed; `job-agent codes` enters it (needs Gmail). |
| Workday "verify your account" | `job-agent verify-accounts` opens the emailed link, then refill. |
| `spam_flagged` / `hand_submit` | Ashby thinks it's a bot. Don't work around it: the window stays open for the user to press Submit. |
| Browser tests fail with `TargetClosedError` | The sandbox blocked Chromium; run tests outside it. Not a real failure. |
| Captcha on submit (Lever) | The user completes it; never solve or bypass a captcha. |
| Everything stuck at one company | `pick` skips companies with a `needs_input` job; answer that one first. |

Failed pages are saved in `me/state/bugs/` (private: they can hold the user's details). Share a fixture only after
replacing every personal value.
