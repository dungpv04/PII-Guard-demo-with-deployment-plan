#!/usr/bin/env bash
# Unit tests (no Docker needed).
set -euo pipefail
cd "$(dirname "$0")/.."
for suite in analyzer_plus copilot_proxy claude_proxy extractor; do
  python3 -m unittest discover -s "$suite/tests" -v
done
