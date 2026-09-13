#!/usr/bin/env bash
# Enforce CLAUDE.md §2.2: domain/ is pure. No I/O, no network, no clock, no env.
set -uo pipefail
payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null)
case "$path" in *"/ff/domain/"*.py) ;; *) exit 0 ;; esac
[ -f "$path" ] || exit 0

bad=$(grep -nE '^\s*(import|from)\s+(requests|httpx|aiohttp|urllib|os|sqlite3|psycopg|boto3|open)\b|datetime\.now\(\)|time\.time\(\)|os\.environ' "$path" || true)
if [ -n "$bad" ]; then
  reason=$(printf '%s' "$bad" | head -5 | tr '\n' ';' | sed 's/"/\\"/g')
  printf '{"decision":"block","reason":"CLAUDE.md 2.2 violation: domain/ must be pure (no I/O, network, clock or env). Found: %s Pass the value in as an argument instead."}\n' "$reason"
  exit 0
fi
exit 0
