"""Images and documents in Anthropic Messages requests, handled before text masking.

Claude Code sends files as content blocks: pasted screenshots and Read on an image are `image`
blocks, Read on a PDF is a `document` block (often inside a `tool_result`). Presidio only scans text,
so binary files never reach the provider unscanned. FILE_POLICY decides what happens instead:

  block    (default) replace the file with a short text notice; the file is never sent
  extract  send the bytes to the extractor service (marker-pdf, optional OCR), replace the file with
           the extracted text, which is then masked like any other text; on any extractor failure
           the file is replaced with a notice (fail-closed)
  pass     forward files unchanged (not recommended: unscanned)

Files are replaced rather than rejected with an error because they stay in the conversation
history: an error would block every later turn of the session. Plain-text documents
(`source.type == "text"`) are already text and are masked directly (see mask.py).
"""

from __future__ import annotations

import base64
import hashlib
import os
from collections import Counter, OrderedDict
from typing import Any, Awaitable, Callable

Extract = Callable[[bytes], Awaitable[dict]]
FILE_TYPES = {"image", "document"}


def _file_blocks(content: Any):
    """Yield (list, index) of every image/document block, including inside tool_result."""
    if not isinstance(content, list):
        return
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if block.get("type") in FILE_TYPES and (block.get("source") or {}).get("type") != "text":
            yield content, index
        elif block.get("type") == "tool_result":
            yield from _file_blocks(block.get("content"))


def _notice(block: dict, message: str) -> dict:
    replacement = {"type": "text", "text": message}
    if "cache_control" in block:  # keep Claude Code's cache breakpoints where they were
        replacement["cache_control"] = block["cache_control"]
    return replacement


class FileHandler:
    def __init__(self, extract: Extract | None, policy: str | None = None, cache_size: int = 256):
        self.extract = extract
        self.policy = (policy or os.getenv("FILE_POLICY", "block")).lower()
        if self.policy not in {"block", "extract", "pass"}:
            raise ValueError("FILE_POLICY must be block, extract or pass")
        # History resends the same file every turn: extract each file once.
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size

    async def _replacement_text(self, block: dict) -> tuple[str, str]:
        """Return (text, outcome) for one file block."""
        kind = block.get("type")
        source = block.get("source") or {}
        if self.policy == "block":
            return f"[PII guard: {kind} removed before sending (FILE_POLICY=block)]", "blocked"
        if source.get("type") != "base64" or not isinstance(source.get("data"), str):
            return f"[PII guard: {kind} removed: only inline files can be scanned]", "unsupported"
        data = base64.b64decode(source["data"], validate=False)
        key = hashlib.sha256(data).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key], "cached"
        try:
            result = await self.extract(data) if self.extract else {"status": "blocked_no_extractor"}
        except Exception as exc:  # extractor down: never send the original file
            return f"[PII guard: {kind} removed: extractor unavailable ({type(exc).__name__})]", "error"
        status = result.get("status")
        text = "\n\n".join(s["text"] for s in result.get("segments") or [] if isinstance(s.get("text"), str)).strip()
        if status != "ok" or not text:
            return f"[PII guard: {kind} removed: {status or 'no text extracted'}]", status or "empty"
        text = f"[PII guard: text extracted from {source.get('media_type', kind)}]\n{text}"
        self._cache[key] = text
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return text, "extracted"

    async def process(self, body: dict) -> Counter:
        """Replace file blocks in place according to FILE_POLICY; return outcome counts."""
        outcomes: Counter = Counter()
        if self.policy == "pass":
            return outcomes
        for message in body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            for container, index in list(_file_blocks(message.get("content"))):
                block = container[index]
                text, outcome = await self._replacement_text(block)
                container[index] = _notice(block, text)
                outcomes[f"{block.get('type')}:{outcome}"] += 1
        return outcomes
