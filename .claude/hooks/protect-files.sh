#!/usr/bin/env bash
# Protect secrets, lockfiles and the charter from casual edits.
set -uo pipefail
payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""' 2>/dev/null)
[ -z "$path" ] && exit 0

deny() { printf '{"decision":"block","reason":"%s"}\n' "$1"; exit 0; }

case "$path" in
  *.env|*.env.*|*oauth2.json|*/secrets/*|*/.tokens/*)
    deny "Secret file. Edit it by hand outside the session; never write credentials through a tool." ;;
  *uv.lock|*package-lock.json|*pnpm-lock.yaml)
    deny "Lockfile. Regenerate it with the package manager instead of editing it." ;;
esac
exit 0
