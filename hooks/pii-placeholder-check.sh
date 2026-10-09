#!/usr/bin/env bash
# PostToolUse hook (Edit|Write): detect Presidio PII placeholders that leaked into a file on disk.
# This automates the T1 fail criterion: any placeholder written to disk = the guardrail is
# corrupting code, not protecting it.
#
# Modes:
#   default            -> WARN: log the hit, print one line, exit 0 (do NOT steer the model, or it
#                         would "fix" the placeholder and contaminate the measurement).
#   PII_HOOK_BLOCK=1   -> BLOCK/STEER: exit 2 (stderr fed back to the model) to demo the mitigation exists.
#
# Reads the PostToolUse JSON payload on stdin. The log path is portable: it defaults to this repo's
# logs/ dir (resolved from the script's own location) and can be overridden with $PII_HOOK_LOG.
# The hook fires from inside the TARGET repo, so it always writes to an absolute path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${PII_HOOK_LOG:-$SCRIPT_DIR/../logs/hook-violations.log}"

# Curated pattern (NOT bare <...> which would match generics / HTML / JSX).
PAT='<(PERSON|EMAIL_ADDRESS|PHONE_NUMBER|CREDIT_CARD|IP_ADDRESS|US_SSN|LOCATION|DATE_TIME|NRP|URL|VN_PHONE|VN_CCCD|VN_TAX_ID|INTERNAL_TERM|ADDRESS|BANK_ACCOUNT)(_[0-9]+)?>'

payload="$(cat)"

# Extract the edited file path from the tool input. jq if available, else a grep fallback.
if command -v jq >/dev/null 2>&1; then
  file="$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)"
else
  file="$(printf '%s' "$payload" | grep -oE '"file_path"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*:"([^"]+)"/\1/' || true)"
fi

# Nothing to check (non-file tool, or missing/deleted path) -> succeed fast.
[ -z "${file:-}" ] && exit 0
[ -f "$file" ] || exit 0

if matches="$(grep -nE "$PAT" "$file" 2>/dev/null)"; then
  mkdir -p "$(dirname "$LOG")"
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  {
    printf '%s | %s\n' "$ts" "$file"
    printf '%s\n' "$matches" | sed 's/^/    /'
  } >> "$LOG"

  if [ "${PII_HOOK_BLOCK:-0}" = "1" ]; then
    echo "PII placeholder leaked into $file (see $LOG). Blocked per PII_HOOK_BLOCK=1." >&2
    exit 2
  fi
  echo "[pii-hook] WARN: placeholder(s) in $file -> logged to $LOG" >&2
  exit 0
fi

exit 0
