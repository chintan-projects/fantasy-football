#!/usr/bin/env bash
# Format and auto-fix after every edit. Never fails the turn.
set -uo pipefail
payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null)
[ -z "$path" ] || [ ! -f "$path" ] && exit 0

case "$path" in
  *.py)
    command -v ruff  >/dev/null 2>&1 && ruff format "$path"            >/dev/null 2>&1
    command -v ruff  >/dev/null 2>&1 && ruff check --fix --quiet "$path" >/dev/null 2>&1
    ;;
  *.ts|*.tsx|*.js|*.jsx|*.json|*.md|*.css)
    command -v npx >/dev/null 2>&1 && npx --no-install prettier --write "$path" >/dev/null 2>&1
    ;;
esac
exit 0
