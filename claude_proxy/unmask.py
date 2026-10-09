"""Rehydrate placeholders in Anthropic `/v1/messages` responses (SSE stream or JSON).

Placeholders can be split across SSE events (`<VN_` + `PHONE_1>`) and appear inside tool_use input
(`input_json_delta`), where Claude Code would otherwise execute or write them literally. The SSE
unmasker reassembles events, holds back a per-content-block tail that could still become a
placeholder, and rehydrates text and tool input. Thinking blocks are never changed (signature).
"""

from __future__ import annotations

import json
from typing import Any

DELTA_FIELDS = {"text_delta": "text", "input_json_delta": "partial_json"}


class SSEUnmasker:
    """Feed raw SSE bytes in any chunking; get rehydrated, complete SSE events back."""

    def __init__(self, tokens: dict[str, str]):
        # Longest first so `<X_10>` is not consumed as `<X_1>` + `0>`.
        self.tokens = sorted(tokens.items(), key=lambda item: len(item[0]), reverse=True)
        self.buffer = b""
        self.pending: dict[int, tuple[str, str]] = {}  # block index -> (delta type, held tail)

    def feed(self, chunk: bytes) -> bytes:
        self.buffer += chunk.replace(b"\r\n", b"\n")
        out = []
        while b"\n\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n\n", 1)
            out.extend(self._event(raw))
        return b"".join(out)

    def flush(self) -> bytes:
        rest, self.buffer = self.buffer, b""
        return rest

    def _rehydrate(self, text: str, json_mode: bool) -> tuple[str, str]:
        """Return (text safe to emit now, tail held back because it may be a partial token)."""
        for token, original in self.tokens:
            if token in text:
                text = text.replace(token, json.dumps(original, ensure_ascii=False)[1:-1] if json_mode else original)
        start = text.rfind("<")
        if start != -1 and any(token.startswith(text[start:]) for token, _ in self.tokens):
            return text[:start], text[start:]
        return text, ""

    def _event(self, raw: bytes) -> list[bytes]:
        passthrough = [raw + b"\n\n"]
        lines = raw.decode("utf-8", errors="strict").split("\n") if raw else []
        data_index = next((i for i, line in enumerate(lines) if line.startswith("data:")), None)
        if data_index is None:
            return passthrough
        try:
            event = json.loads(lines[data_index][5:].strip())
        except json.JSONDecodeError:
            return passthrough
        kind = event.get("type") if isinstance(event, dict) else None

        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            field = DELTA_FIELDS.get(delta.get("type"))
            if not field or not isinstance(delta.get(field), str):
                return passthrough
            index = event.get("index")
            _, held = self.pending.pop(index, ("", ""))
            emit, tail = self._rehydrate(held + delta[field], json_mode=field == "partial_json")
            if tail:
                self.pending[index] = (delta["type"], tail)
            if not emit:
                return []
            delta[field] = emit
            return [self._encode(lines, data_index, event)]

        if kind == "content_block_stop" and event.get("index") in self.pending:
            delta_type, tail = self.pending.pop(event["index"])
            field = DELTA_FIELDS[delta_type]
            flushed = {"type": "content_block_delta", "index": event["index"], "delta": {"type": delta_type, field: tail}}
            return [self._encode(["event: content_block_delta", "data: "], 1, flushed)] + passthrough
        return passthrough

    @staticmethod
    def _encode(lines: list[str], data_index: int, event: dict) -> bytes:
        lines = list(lines)
        lines[data_index] = "data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        return ("\n".join(lines) + "\n\n").encode("utf-8")


def _rehydrate_value(value: Any, tokens: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for token, original in tokens:
            value = value.replace(token, original)
        return value
    if isinstance(value, list):
        return [_rehydrate_value(item, tokens) for item in value]
    if isinstance(value, dict):
        return {key: _rehydrate_value(item, tokens) for key, item in value.items()}
    return value


def rehydrate_message(message: dict, tokens: dict[str, str]) -> dict:
    """Non-streaming Anthropic message: rehydrate text blocks and tool_use input, never thinking."""
    ordered = sorted(tokens.items(), key=lambda item: len(item[0]), reverse=True)
    for block in message.get("content") or []:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            block["text"] = _rehydrate_value(block["text"], ordered)
        elif block.get("type") == "tool_use" and "input" in block:
            block["input"] = _rehydrate_value(block["input"], ordered)
    return message
