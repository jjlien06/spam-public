# Updating (for the user's AI agent)

```sh
git pull
.venv/bin/pip install -e '.[test]'      # only when pyproject.toml changed
```

- `git pull` never touches `me/`: it's git-ignored. If git reports a conflict in a tracked file you didn't mean to
  change, keep the upstream version (`git checkout --theirs FILE` during a merge, or `git stash` your edit and pull).
- Never delete or overwrite `me/profile.json`, `me/accounts.json` or `me/state/`. Before any manual edit of the
  profile, copy it: `cp me/profile.json me/profile.backup.json`.
- New shared library rules arrive with the pull and apply immediately to questions your private answers don't cover.
- Run the fast tests after an update if something looks off: `.venv/bin/python -m pytest -q tests/test_library.py tests/test_facts.py`.
