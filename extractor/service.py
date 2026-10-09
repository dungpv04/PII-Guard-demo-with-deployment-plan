from __future__ import annotations

import os
import subprocess

from fastapi import FastAPI, Request

from .backend import marker_extract
from .validation import detect_kind, validate


app = FastAPI(title="Local Marker Extractor", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "backend": os.getenv("EXTRACT_BACKEND", "marker"), "ocr": os.getenv("EXTRACT_OCR", "off")}


@app.post("/extract")
async def extract(request: Request) -> dict:
    data = await request.body()
    try:
        kind, suffix = validate(data)
        if os.getenv("EXTRACT_BACKEND", "marker") != "marker":
            raise ValueError("blocked_unsupported")
        segments = marker_extract(data, suffix, float(os.getenv("EXTRACT_TIMEOUT_S", "120")))
        return {"status": "ok", "kind": kind, "segments": segments}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "kind": detect_kind(data)[0] if detect_kind(data) else None, "segments": []}
    except ValueError as exc:
        status = str(exc)
        if not status.startswith("blocked_"):
            status = "blocked_malformed"
        return {"status": status, "kind": detect_kind(data)[0] if detect_kind(data) else None, "segments": []}
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return {"status": "blocked_malformed", "kind": detect_kind(data)[0] if detect_kind(data) else None, "segments": []}
