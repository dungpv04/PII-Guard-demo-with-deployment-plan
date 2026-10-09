#!/usr/bin/env bash
# Wait until every running service with a healthcheck is healthy (timeout HEALTH_TIMEOUT, default 240s).
set -euo pipefail
cd "$(dirname "$0")/.."
deadline=$(( $(date +%s) + ${HEALTH_TIMEOUT:-240} ))
while true; do
  states="$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy"')"
  [ -z "$states" ] && { echo "all healthy."; exit 0; }
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "TIMEOUT waiting for: $states" >&2
    docker compose logs --tail 40 >&2 || true
    exit 1
  fi
  sleep 3
done
