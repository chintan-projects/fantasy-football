#!/usr/bin/env bash
# Fast test + type gate at the end of a session.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -d backend ] || exit 0

fail=""
if command -v uv >/dev/null 2>&1; then
  ( cd backend && uv run pytest -q --timeout=60 -x ) >/tmp/ff-test.log 2>&1 || fail="tests"
  ( cd backend && uv run mypy src/ff/domain src/ff/services ) >>/tmp/ff-test.log 2>&1 || fail="${fail:+$fail,}types"
fi

if [ -n "$fail" ]; then
  reason=$(tail -25 /tmp/ff-test.log | tr '\n' ' ' | sed 's/"/\\"/g' | cut -c1-1200)
  printf '{"decision":"block","reason":"Failing: %s. Fix before finishing. Tail: %s"}\n' "$fail" "$reason"
  exit 0
fi
echo "Smoke tests and type checks passed."
exit 0
