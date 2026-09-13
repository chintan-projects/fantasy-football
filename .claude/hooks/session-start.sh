#!/usr/bin/env bash
# Load project state at session start.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

echo "=== Fantasy Football Copilot ==="
echo "Read CLAUDE.md before making changes. Principles are in §2, safety invariants in §5."
echo

if [ -f PROGRESS.yaml ]; then
  echo "--- Current work (PROGRESS.yaml) ---"
  grep -A3 "status: in_progress" PROGRESS.yaml 2>/dev/null | head -20 || echo "(nothing in progress)"
  echo
fi

if [ -f BUGS.yaml ]; then
  open_bugs=$(grep -c "status: open" BUGS.yaml 2>/dev/null || echo 0)
  echo "--- Open bugs: ${open_bugs} ---"
  grep -B1 -A2 "severity: critical" BUGS.yaml 2>/dev/null | head -20
  echo
fi

echo "--- Write safety ---"
echo "FF_WRITE_ENABLED=${FF_WRITE_ENABLED:-false} (dry-run unless explicitly true)"
if [ -f .yahoo_write_status ]; then cat .yahoo_write_status; else
  echo "Yahoo write access: UNVERIFIED. Run 'make probe' before trusting the write layer."
fi
echo

if command -v git >/dev/null 2>&1 && [ -d .git ]; then
  echo "--- Branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null) ---"
  git status --short | head -15
fi
exit 0
