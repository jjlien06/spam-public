#!/bin/zsh
# Double-click to open the three-button REAL application demo.
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'SPAM demo needs this project’s Python environment (.venv).'
  print 'Ask Codex to repair the demo environment before your coffee chat.'
  read '?Press Return to close.'
  exit 1
fi
.venv/bin/python -m job_agent.live_demo "$@"
result=$?
if [[ $result != 0 ]]; then
  print '\nThe demo could not start. Keep this error visible for troubleshooting.'
  read '?Press Return to close.'
fi
exit $result
