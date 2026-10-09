#!/usr/bin/env bash
# Install (or remove) the PostToolUse placeholder-leak hook into ~/.claude/settings.json.
# The command path is resolved to this repo's ABSOLUTE location at install time, so the hook fires
# correctly even when Claude Code runs inside a DIFFERENT repo.
#
#   ./scripts/install-hook.sh            install/merge the hook (backs up settings.json first)
#   ./scripts/install-hook.sh --remove   restore the most recent backup
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO_DIR/hooks/pii-placeholder-check.sh"
SETTINGS="$HOME/.claude/settings.json"
BACKUP="$REPO_DIR/logs/settings.json.bak"

mkdir -p "$(dirname "$SETTINGS")" "$REPO_DIR/logs"

if [ "${1:-}" = "--remove" ]; then
  if [ -f "$BACKUP" ]; then
    cp "$BACKUP" "$SETTINGS"
    echo "Restored $SETTINGS from $BACKUP"
  else
    echo "No backup at $BACKUP — nothing to restore. Edit $SETTINGS by hand to remove the hook."
  fi
  exit 0
fi

command -v jq >/dev/null 2>&1 || {
  echo "jq not found. Manual install: merge this into $SETTINGS (command path = $HOOK):"
  sed "s#__POC_DIR__#$REPO_DIR#g" "$REPO_DIR/hooks/settings.hooks.json"
  exit 1
}

# Seed an empty settings file if none exists.
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$BACKUP"
echo "Backed up $SETTINGS -> $BACKUP"

# Merge a PostToolUse (Edit|Write) entry pointing at the absolute hook path. Idempotent:
# drop any existing entry that already points at this hook, then append a fresh one.
tmp="$(mktemp)"
jq --arg cmd "$HOOK" '
  .hooks = (.hooks // {}) |
  .hooks.PostToolUse = ((.hooks.PostToolUse // [])
    | map(select((.hooks // []) | any(.command == $cmd) | not))
    + [ { "matcher": "Edit|Write", "hooks": [ { "type": "command", "command": $cmd } ] } ])
' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"

echo "Installed PostToolUse hook -> $HOOK"
echo "Default = WARN mode (logs to logs/hook-violations.log). Set PII_HOOK_BLOCK=1 to block."
echo "Remove with: ./scripts/install-hook.sh --remove"
