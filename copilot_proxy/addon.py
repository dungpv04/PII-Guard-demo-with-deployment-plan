"""mitmproxy addon: Copilot (normal GitHub login) -> PII scrub -> GitHub Copilot backend.

Run: mitmdump -s copilot_proxy/addon.py --set allow_hosts="$COPILOT_HOSTS"
Only hosts matching COPILOT_HOSTS are decrypted; GitHub login and token exchange
(github.com, api.github.com) are tunnelled untouched. Fail-closed: if the analyzer or
anonymizer fails, the request is answered with 503 and never forwarded.
Logs carry host, path, action and entity counts only; never bodies or headers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys

from mitmproxy import http

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from copilot_proxy.scrub import presidio_scrubber  # noqa: E402

DEFAULT_HOSTS = r"(^|\.)githubcopilot\.com(:\d+)?$|^copilot-proxy\.githubusercontent\.com(:\d+)?$"
log = logging.getLogger("copilot-pii")


def _error(status: int, message: str) -> http.Response:
    # OpenAI-style error envelope; Copilot surfaces `message` to the user.
    body = json.dumps({"error": {"message": message, "type": "pii_guard", "code": status}})
    return http.Response.make(status, body, {"content-type": "application/json"})


class CopilotPIIGuard:
    def __init__(self) -> None:
        self.hosts = re.compile(os.getenv("COPILOT_HOSTS", DEFAULT_HOSTS))
        self.scrubber = presidio_scrubber()

    async def request(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        if req.method != "POST" or not self.hosts.search(req.pretty_host) or not req.content:
            return
        try:
            body = json.loads(req.content)
        except ValueError:
            log.info("copilot-pii host=%s path=%s action=pass reason=non-json", req.pretty_host, req.path)
            return
        try:
            result = await asyncio.to_thread(self.scrubber.scrub, body)
        except Exception as exc:  # analyzer/anonymizer down or invalid -> fail closed
            log.warning("copilot-pii host=%s path=%s action=fail-closed error=%s",
                        req.pretty_host, req.path, type(exc).__name__)
            flow.response = _error(503, "PII guard unavailable; request not sent")
            return
        if result.blocked:
            log.info("copilot-pii host=%s path=%s action=block entities=%s",
                     req.pretty_host, req.path, ",".join(result.blocked))
            flow.response = _error(403, f"Blocked by PII guard: {', '.join(result.blocked)}")
            return
        if result.changed:
            req.content = json.dumps(result.body, ensure_ascii=False).encode()
        log.info("copilot-pii host=%s path=%s action=%s entities=%s", req.pretty_host, req.path,
                 "mask" if result.changed else "pass", dict(result.masked))

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        # Relay completions/chat SSE as it arrives; buffering stalls the editor.
        flow.response.stream = True


addons = [CopilotPIIGuard()]
