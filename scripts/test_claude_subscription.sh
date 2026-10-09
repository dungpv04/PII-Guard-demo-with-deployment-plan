#!/usr/bin/env bash
# Gate for the Claude Code SUBSCRIPTION path (README: Kiểm thử). Key-free and Anthropic-free:
# runs the real `claude` CLI in OAuth mode with a FAKE token against the mock upstream recorder,
# once directly (baseline) and once through LiteLLM, for TWO turns each (turn 2 uses --continue,
# which adds per-message fields such as output_config), then compares what reached "Anthropic".
#
# Prereq: ./scripts/up.sh test
# Checks per turn: OAuth token forwarded | gateway key not leaked | anthropic-beta identical |
#   body + per-message fields identical | thinking/output_config unchanged | system shape kept |
#   PII masked | placeholders well-formed
# Plus: a streamed answer whose placeholder is split across deltas, in text and in tool_use input,
# comes back rehydrated (claude_proxy/unmask.py).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
KEY="${PII_GUARD_KEY:-${LITELLM_MASTER_KEY:-}}"

GW="${GATEWAY_URL:-http://localhost:4000}"
MOCK="${MOCK_URL:-http://localhost:8089}"
CAP=logs/captured-requests
command -v claude >/dev/null || { echo "claude CLI not found"; exit 1; }
curl -sf -o /dev/null "$GW/__health" || { echo "gateway not up: ./scripts/up.sh test"; exit 1; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
PROMPT='Email tôi là an.nguyen@example.com, SĐT 0912345678. Trả lời OK.'

run_claude() {  # $1 = base URL, $2 = extra custom headers (may be empty), $3 = isolated config dir
  # Isolated config dir + fake OAuth token: the developer's real login is never touched or sent.
  local turn
  for turn in 1 2; do
    local cont=(); [ "$turn" = 2 ] && cont=(--continue)
    (cd "$WORK" && env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
      CLAUDE_CONFIG_DIR="$3" CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-FAKE-not-a-real-token \
      ANTHROPIC_BASE_URL="$1" ANTHROPIC_CUSTOM_HEADERS="$2" CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
      timeout 120 claude -p "$PROMPT" "${cont[@]}" --model claude-sonnet-5-5 >/dev/null 2>&1) || true
  done
}
collect() {  # move this run's captures (oldest first) to $WORK/$1-<n>.json
  local n=0 f
  for f in $(ls -tr "$CAP"/*.json 2>/dev/null); do n=$((n + 1)); mv "$f" "$WORK/$1-$n.json"; done
  [ "$n" -gt 0 ] || { echo "FAIL: no request captured for $1"; exit 1; }
}

mkdir -p "$CAP"; rm -f "$CAP"/*.json
run_claude "$MOCK" "" "$WORK/cfg-direct"; collect baseline
run_claude "$GW" "x-pii-guard-key: Bearer ${KEY}" "$WORK/cfg-gw"; collect via

python3 - "$WORK" <<'PY'
import glob, json, re, sys

def load(kind):
    paths = sorted(glob.glob(f"{sys.argv[1]}/{kind}-*.json"), key=lambda p: int(p.rsplit("-", 1)[1][:-5]))
    reqs = [json.load(open(p)) for p in paths]
    # Main-conversation requests only (side requests such as titles don't carry the prompt).
    return [r for r in reqs if r["jsonBody"] and "Trả lời OK" in json.dumps(r["jsonBody"]["messages"], ensure_ascii=False)]

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)

betas = lambda r: set(filter(None, (r["headers"].get("anthropic-beta") or "").split(",")))
msg_keys = lambda r: [sorted(m) for m in r["jsonBody"]["messages"]]

def run_checks(base, via):
    check(via["auth"]["authorization"] == "claude-oauth", "OAuth token forwarded upstream (no API key substituted)")
    check(via["auth"]["x-api-key"] is None, "no x-api-key sent upstream")
    check(via["auth"].get("x-pii-guard-key") is None and via["auth"].get("x-litellm-api-key") is None, "gateway key not leaked upstream")
    check(via["headers"].get("user-agent") == base["headers"].get("user-agent"), "user-agent unchanged")
    check(via["headers"].get("anthropic-beta") == base["headers"].get("anthropic-beta"), "anthropic-beta byte-identical (order kept)")
    check(via["url"] == base["url"], f"path and query unchanged ({via['url']})")
    for field in ("tools", "safeguards"):
        same = json.dumps(base["jsonBody"].get(field)).replace("cfg-direct", "X") == json.dumps(via["jsonBody"].get(field)).replace("cfg-gw", "X")
        check(same, f"`{field}` forwarded unchanged")
    missing = betas(base) - betas(via)
    check(not missing, f"anthropic-beta forwarded verbatim (missing: {sorted(missing) or 'none'})")
    dropped = set(base["jsonBody"]) - set(via["jsonBody"])
    check(not dropped, f"request body fields preserved (dropped: {sorted(dropped) or 'none'})")
    check(msg_keys(base) == msg_keys(via), "per-message fields preserved (e.g. output_config)")
    for field in ("thinking", "output_config", "context_management"):
        check(base["jsonBody"].get(field) == via["jsonBody"].get(field), f"`{field}` forwarded unchanged")
    check([b.get("text", "")[:40] for b in base["jsonBody"]["system"]] == [b.get("text", "")[:40] for b in via["jsonBody"]["system"]],
          "system array order/shape unchanged (attribution block stays first)")
    sent = json.dumps(via["jsonBody"]["messages"], ensure_ascii=False)
    check("an.nguyen@example.com" not in sent and "0912345678" not in sent, "PII masked before leaving the gateway")
    tokens = re.findall(r"<[A-Z_]+_\d+>", sent)
    # Overlapping findings corrupt the numbered rewriter into e.g. <PHONE_NUMBER_4>4>_4>.
    corrupt = re.findall(r"<[A-Z_]+_\d+>\d*>?_?\d+>", sent)
    check(bool(tokens) and not corrupt, f"placeholders well-formed: {sorted(set(tokens))} corrupt={corrupt}")

bases, vias = load("baseline"), load("via")
check(len(bases) >= 2 and len(vias) == len(bases), f"both turns captured (baseline {len(bases)}, gateway {len(vias)})")
for turn, (base, via) in enumerate(zip(bases, vias), start=1):
    print(f"-- turn {turn}")
    run_checks(base, via)
sys.exit(1 if fails else 0)
PY

# Stream rehydration: the mock echoes the placeholder split across two deltas (MOCK_ECHO).
ECHO="$(curl -sN "$GW/v1/messages" -H "x-pii-guard-key: Bearer ${KEY}" \
  -H 'Authorization: Bearer sk-ant-oat01-FAKE-not-a-real-token' -H 'anthropic-version: 2023-06-01' \
  -H 'anthropic-beta: oauth-2025-04-20' -H 'content-type: application/json' \
  -d '{"model":"claude-sonnet-5-5","max_tokens":50,"stream":true,"messages":[{"role":"user","content":"MOCK_ECHO SĐT 0912345678"}]}')"
python3 - "$ECHO" <<'PY'
import json, sys
text, tool = "", ""
for line in sys.argv[1].splitlines():
    if line.startswith("data:"):
        event = json.loads(line[5:])
        delta = event.get("delta") or {}
        text += delta.get("text", "")
        tool += delta.get("partial_json", "")
ok = text == "ECHO 0912345678 END" and json.loads(tool or "{}").get("command") == "echo 0912345678"
print(("PASS" if ok else "FAIL") + f" streamed placeholders rehydrated in text and tool_use input (text={text!r}, tool={tool!r})")
sys.exit(0 if ok else 1)
PY
