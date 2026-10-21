#!/bin/sh
# One-shot read-only snapshot for any agent: health, Review list, stuck questions.
cd "$(dirname "$0")/.." || exit 1
J=.venv/bin/job-agent
echo "=== HEALTH ==="; $J health
echo "=== REVIEW ==="; $J review --brief
echo "=== DECK ===";   $J deck
