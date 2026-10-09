#!/usr/bin/env bash
# Bring up PII Guard after preflight checks.
#   ./scripts/up.sh         gateway (Claude Code) + copilot-proxy, upstream = real Anthropic / GitHub
#   ./scripts/up.sh test    same, but the Claude upstream is a local mock recorder (no real traffic);
#                           then run ./scripts/test_claude_subscription.sh
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "PREFLIGHT FAILED: $1" >&2; echo "FIX: $2" >&2; exit 1; }

[ -f .env ] || fail ".env not found" "cp .env.example .env  (then set PII_GUARD_KEY)"
# Load .env without overriding variables already set in the shell (same precedence as docker compose),
# so one-off overrides work: FILE_POLICY=extract ./scripts/up.sh
while IFS= read -r line || [ -n "$line" ]; do
  [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
  key="${line%%=*}"
  [ -n "${!key+x}" ] || export "$line"
done < .env
MODE="${1:-run}"

command -v docker >/dev/null 2>&1 || fail "docker not found" "install Docker"
docker info >/dev/null 2>&1 || fail "docker daemon not running" "start Docker"
# Gateway key (LITELLM_MASTER_KEY accepted from .env files written for the LiteLLM version).
export PII_GUARD_KEY="${PII_GUARD_KEY:-${LITELLM_MASTER_KEY:-}}"
[ -n "$PII_GUARD_KEY" ] || fail "PII_GUARD_KEY is empty" "set it in .env"

# Another stack (e.g. the old llm-pii-gateway-poc) on the same ports leaves both half-started.
for port in 4000 8080; do
  other="$(docker ps --filter "publish=${port}" --format '{{.Names}}' | grep -v '^pii-guard-' || true)"
  [ -z "$other" ] || fail "port ${port} is used by container(s): ${other}" \
    "stop that stack first (e.g. cd ../llm-pii-gateway-poc && docker compose down)"
done

# FILE_POLICY=extract needs the extractor (profile `ocr`, large image: built on first use).
PROFILES=()
case "${FILE_POLICY:-block}" in
  block|pass) docker compose --profile ocr rm -sf extractor >/dev/null 2>&1 || true ;;
  extract) PROFILES+=(--profile ocr) ;;
  *) fail "FILE_POLICY='${FILE_POLICY}' is invalid" "use block, extract or pass" ;;
esac

# SLM second opinion served by the bundled Ollama container (profile `slm`).
BUNDLED_SLM=0
if [ "${ENABLE_SLM_GATE:-0}" = "1" ] && [[ "${SLM_BASE_URL:-}" == http://ollama:* ]]; then
  BUNDLED_SLM=1; PROFILES+=(--profile slm)
else
  docker compose --profile slm rm -sf ollama >/dev/null 2>&1 || true
fi

mkdir -p logs/captured-requests copilot_proxy/ca
case "$MODE" in
  run)
    docker compose --profile test rm -sf upstream-recorder >/dev/null 2>&1 || true
    UPSTREAM_BASE=https://api.anthropic.com docker compose "${PROFILES[@]}" up -d --build --remove-orphans ;;
  test)
    UPSTREAM_BASE=http://upstream-recorder:8089 docker compose "${PROFILES[@]}" --profile test up -d --build --remove-orphans ;;
  *) fail "unknown mode '$MODE'" "use: ./scripts/up.sh [test]" ;;
esac

./scripts/wait-health.sh
if [ "$BUNDLED_SLM" = "1" ]; then
  echo "-> ensuring SLM model ${SLM_MODEL:-qwen2.5:1.5b} is present in the ollama container (first run downloads it)"
  docker compose --profile slm exec -T ollama ollama pull "${SLM_MODEL:-qwen2.5:1.5b}" >/dev/null
fi
echo
echo "PII Guard is up (mode=${MODE})."
echo "  Claude Code : ANTHROPIC_BASE_URL=http://localhost:4000  (README: Claude Code)"
echo "  Files       : FILE_POLICY=${FILE_POLICY:-block}"
if [ "${ENABLE_SLM_GATE:-0}" = "1" ]; then echo "  SLM gate    : on (${SLM_BASE_URL:-}, model ${SLM_MODEL:-qwen2.5:1.5b})"; else echo "  SLM gate    : off"; fi
echo "  Copilot     : HTTP proxy http://localhost:8080, CA copilot_proxy/ca/mitmproxy-ca-cert.pem  (README: Copilot)"
