#!/usr/bin/env bash
# Block dangerous or footgun commands before they run.
set -uo pipefail
payload=$(cat)
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""' 2>/dev/null)
[ -z "$cmd" ] && exit 0

deny() { printf '{"decision":"block","reason":"%s"}\n' "$1"; exit 0; }

case "$cmd" in
  *"rm -rf /"*|*"rm -rf ~"*)            deny "Refusing destructive recursive delete." ;;
  *"git push --force"*|*"git push -f"*) deny "Force push is not allowed. Use --force-with-lease after review." ;;
  *"git checkout ."*|*"git reset --hard"*) deny "This discards uncommitted work. Commit or stash first." ;;
  *"FF_WRITE_ENABLED=true"*)            deny "Do not enable live Yahoo writes from an ad-hoc shell command. Writes go through the approval flow (CLAUDE.md §5)." ;;
  *"pip install"*)                      deny "Use 'uv add' / 'uv sync' so the lockfile stays authoritative." ;;
  *".env"*">"*)                         deny "Refusing to overwrite .env from the shell." ;;
esac
exit 0
