"""Claude Code -> PII mask -> Anthropic, forwarding everything else verbatim.

Claude Code points ANTHROPIC_BASE_URL here and stays logged in with the developer's Claude
subscription: the OAuth `Authorization` header, `anthropic-*` headers, user-agent and query string
pass through untouched. The only changes to a request are image/document blocks handled per
FILE_POLICY (see files.py) and masked conversation text (see mask.py); a request without either
is forwarded byte for byte. Responses are relayed as they arrive, with
placeholders rehydrated (see unmask.py). Fail-closed: analyzer errors return 503, nothing is sent.
No retries here (Claude Code retries itself, honouring retry-after). Logs carry no content.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import time

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .files import FileHandler
from .mask import Masker, Policy
from .unmask import SSEUnmasker, rehydrate_message

UPSTREAM = os.getenv("UPSTREAM_BASE", "https://api.anthropic.com").rstrip("/")
ANALYZER = os.getenv("ANALYZER_URL", "http://presidio-analyzer:3000").rstrip("/")
EXTRACTOR = os.getenv("EXTRACTOR_URL", "http://extractor:8000").rstrip("/")
EXTRACT_TIMEOUT = float(os.getenv("EXTRACT_TIMEOUT_S", "120")) + 10
GATEWAY_KEY = os.getenv("PII_GUARD_KEY", "")
KEY_HEADERS = ("x-pii-guard-key", "x-litellm-api-key")  # the latter for configs written for LiteLLM
SCANNED_PATHS = {"v1/messages", "v1/messages/count_tokens"}
HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "te", "trailer",
              "upgrade", "proxy-authorization", "proxy-connection", "accept-encoding", *KEY_HEADERS}
RESPONSE_DROP = {"content-length", "content-encoding", "transfer-encoding", "connection", "keep-alive"}

log = logging.getLogger("claude-proxy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one status line per request is enough
client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=None, write=60, pool=10), follow_redirects=False)


async def analyze(text: str) -> list[dict]:
    response = await client.post(f"{ANALYZER}/analyze", json={"text": text, "language": "en"}, timeout=30)
    response.raise_for_status()
    findings = response.json()
    if not isinstance(findings, list):
        raise ValueError("invalid analyzer response")
    return findings


async def extract(data: bytes) -> dict:
    response = await client.post(f"{EXTRACTOR}/extract", content=data, timeout=EXTRACT_TIMEOUT)
    response.raise_for_status()
    return response.json()


masker = Masker(analyze, Policy.from_env())
files = FileHandler(extract)
app = FastAPI(title="PII Guard Claude proxy", docs_url=None, redoc_url=None, openapi_url=None)


def anthropic_error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse({"type": "error", "error": {"type": kind, "message": message}}, status_code=status,
                        headers={"x-should-retry": "false"})


def authorized(request: Request) -> bool:
    if not GATEWAY_KEY:
        return True
    for name in KEY_HEADERS:
        value = request.headers.get(name, "")
        value = value[7:] if value.lower().startswith("bearer ") else value
        if value and hmac.compare_digest(value.encode(), GATEWAY_KEY.encode()):
            return True
    return False


@app.get("/__health")
async def health() -> dict:
    return {"status": "ok"}


@app.api_route("/{path:path}", methods=["GET", "POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    started = time.monotonic()
    if not authorized(request):
        return anthropic_error(401, "authentication_error", "PII guard: missing or invalid x-pii-guard-key")

    body = await request.body()
    log.info("recv %s /%s bytes=%d", request.method, path, len(body))
    tokens: dict[str, str] = {}
    action = "pass"
    if request.method == "POST" and path in SCANNED_PATHS and body:
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            try:
                file_outcomes = await files.process(payload)
                result = await masker.mask(payload)
            except Exception as exc:  # analyzer down or invalid: never send the original
                log.warning("path=/%s action=fail-closed error=%s", path, type(exc).__name__)
                return anthropic_error(503, "api_error", "PII guard unavailable; request not sent")
            if result.blocked:
                log.info("path=/%s action=block entities=%s", path, ",".join(result.blocked))
                return anthropic_error(400, "invalid_request_error", f"Blocked by PII guard: {', '.join(result.blocked)}")
            log.info("masked /%s in %dms", path, (time.monotonic() - started) * 1000)
            if result.tokens or file_outcomes:
                body = json.dumps(result.body, ensure_ascii=False, separators=(",", ":")).encode()
                tokens = result.tokens
                action = f"mask {dict(result.masked)}" + (f" files {dict(file_outcomes)}" if file_outcomes else "")

    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP]
    headers.append(("accept-encoding", "identity"))  # relay bytes as they arrive; no decoder buffering
    url = f"{UPSTREAM}/{path}" + (f"?{request.url.query}" if request.url.query else "")
    upstream = await client.send(client.build_request(request.method, url, headers=headers, content=body), stream=True)
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in RESPONSE_DROP}
    log.info("%s /%s status=%s action=%s ttfb_ms=%d", request.method, path, upstream.status_code, action,
             (time.monotonic() - started) * 1000)

    content_type = upstream.headers.get("content-type", "")
    if tokens and upstream.status_code == 200 and content_type.startswith("application/json"):
        data = json.loads(await upstream.aread())
        await upstream.aclose()
        if isinstance(data, dict) and data.get("type") == "message":
            rehydrate_message(data, tokens)
        return Response(json.dumps(data, ensure_ascii=False).encode(), status_code=200, headers=out_headers)

    async def relay():
        unmasker = SSEUnmasker(tokens) if tokens and content_type.startswith("text/event-stream") else None
        received = sent = 0
        events: list[str] = []  # event types only (and block types), never content
        outcome = "aborted"  # client went away or the upstream stream stalled until the client gave up
        try:
            async for chunk in upstream.aiter_raw():
                received += len(chunk)
                for name in re.findall(rb"event: ([a-z_]+)|\"content_block\":\{\"type\":\"([a-z_]+)", chunk):
                    events.append((name[0] or name[1]).decode())
                out = unmasker.feed(chunk) if unmasker else chunk
                if out:
                    sent += len(out)
                    yield out
            if unmasker:
                tail = unmasker.flush()
                if tail:
                    sent += len(tail)
                    yield tail
            outcome = "done"
        finally:
            held = (len(unmasker.buffer) + sum(len(t) for _, t in unmasker.pending.values())) if unmasker else 0
            log.info("stream /%s %s received=%d sent=%d held=%d secs=%.1f events=%s", path, outcome, received, sent,
                     held, time.monotonic() - started, ",".join(events[-12:]))

    return StreamingResponse(relay(), status_code=upstream.status_code, headers=out_headers,
                             background=BackgroundTask(upstream.aclose))
