# Sharing what SPAM learned (for the user's AI agent)

Every SPAM user makes the shared question library better. When a form asks a known question in new words and the
user maps it to a core fact (`job-agent answer "Q" x --fact KEY`, plus `--invert` when the wording asks the
opposite), SPAM saves a private rule marked `shareable`: the question's wording and the fact's *name*, nothing else.

## Contributing (recommended, after a batch of applications)

```sh
.venv/bin/job-agent share-library            # writes me/share/library-YYYYMMDD.json to read first
.venv/bin/job-agent share-library --pr       # opens a pull request adding them to library/community.json
```

Read every field of the preview file with the user before `--pr`, including rule IDs and patterns. The intended
payload is question wording and fact names, not answers. Filtering is best-effort: private values that are absent
from the profile, transformed, or too short to match may remain, as may employer names or dates. Remove these
manually before sharing. `--pr` needs the GitHub CLI signed in (`gh auth login`, once) and write access to the
repository; external contributors should use a fork and ordinary pull request instead. The helper works in a
temporary copy and does not edit the user's checkout or `me/`.

The maintainer reviews and merges; everyone gets the new wordings with `git pull`.

## What must never be committed or shared as a library contribution
Answers, essays, `me/profile.json`, `me/accounts.json`, `me/state/` (queue, runs, bug captures with page HTML).
This is a repository-sharing rule, not an on-device-processing guarantee. Live employer forms receive entered
information, and optional hosted AI or mailbox integrations may transmit selected data; see `SECURITY.md`.
If a site needs a code fix, describe the question and the site's options in a GitHub issue instead of attaching
a capture, or replace every personal value in it first.

## Maintainer
`.venv/bin/job-agent merge-library FILE` for a file someone sent instead of a pull request.
