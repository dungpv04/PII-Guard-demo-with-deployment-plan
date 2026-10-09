from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException

from .rules import merge_recognizers
from .slm_gate import drop_allowed, get_additional_findings, merge_findings


app = FastAPI(title="PII Analyzer Plus", version="0.1.0")
CORE_URL = os.getenv("PRESIDIO_CORE_URL", "http://presidio-analyzer-core:3000").rstrip("/")
CORE_TIMEOUT = float(os.getenv("PRESIDIO_CORE_TIMEOUT_S", "15"))
# Never masked. Default: the address Claude Code itself injects into git attribution reminders.
ALLOW_LIST = frozenset(v.strip().lower() for v in os.getenv("ALLOW_LIST", "noreply@anthropic.com").split(",") if v.strip())


@app.get("/health")
async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{CORE_URL}/health")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="presidio core unavailable") from exc
    return {"status": "ok", "slm_enabled": os.getenv("ENABLE_SLM_GATE", "0") == "1"}


@app.post("/analyze")
async def analyze(payload: dict) -> list[dict]:
    text = payload.get("text")
    if not isinstance(text, str):
        raise HTTPException(status_code=422, detail="text must be a string")
    try:
        async with httpx.AsyncClient(timeout=CORE_TIMEOUT) as client:
            response = await client.post(f"{CORE_URL}/analyze", json=merge_recognizers(payload))
            response.raise_for_status()
            findings = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Fail closed: LiteLLM treats analyzer failure as a failed request.
        raise HTTPException(status_code=502, detail="presidio core analysis failed") from exc
    if not isinstance(findings, list):
        raise HTTPException(status_code=502, detail="invalid presidio core response")
    additions = await get_additional_findings(text, findings)
    # Overlaps are kept: only the caller knows its policy (see claude_proxy/mask.py resolve_overlaps).
    return drop_allowed(merge_findings(findings, additions), text, ALLOW_LIST)
