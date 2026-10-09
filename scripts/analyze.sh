#!/usr/bin/env bash
# Show what the analyzer detects in a text, and what the Claude Code policy would do with it.
#   ./scripts/analyze.sh "SĐT 0912345678, email an@example.com"
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo "usage: $0 \"text to analyze\""; exit 1; }
docker compose exec -T claude-proxy python - "$1" <<'PY'
import json, os, sys, urllib.request
from claude_proxy.mask import Policy
text = sys.argv[1]
policy = Policy.from_env()
request = urllib.request.Request(os.getenv("ANALYZER_URL", "http://presidio-analyzer:3000") + "/analyze",
                                 json.dumps({"text": text, "language": "en"}).encode(), {"content-type": "application/json"})
findings = json.loads(urllib.request.urlopen(request, timeout=60).read())
applied = {(f["start"], f["end"], f["entity_type"]) for f in policy.relevant(findings)}
print(f"threshold={policy.threshold}  (MASK/BLOCK = what the Claude Code proxy does)")
for f in sorted(findings, key=lambda f: (f["start"], f["end"])):
    entity, score = f["entity_type"], float(f.get("score", 0))
    if (f["start"], f["end"], entity) in applied:
        action = "BLOCK" if entity in policy.block else "MASK"
    elif entity not in policy.mask | policy.block:
        action = "ignore (not in policy)"
    elif score < policy.threshold:
        action = "ignore (below threshold)"
    else:
        action = "ignore (overlap lost)"
    print(f"{action:26} {entity:15} {score:.2f}  {text[f['start']:f['end']]!r}")
PY
