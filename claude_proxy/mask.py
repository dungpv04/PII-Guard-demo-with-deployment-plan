"""Mask PII in an Anthropic Messages request without reshaping it.

Only string values that carry conversation text are rewritten; every other field (tools, thinking,
safeguards, cache_control, block order) is left exactly as Claude Code sent it. Rewriting anything
else makes Anthropic stall or 429 subscription (OAuth) traffic.

Scanned: `system` (string or text blocks), message `content` strings, `text` blocks, plain-text
`document` blocks, `tool_result`
content and `tool_use` input. History is rehydrated on the way back, so earlier assistant turns and
tool calls carry real values again and must be masked again. `thinking` / `redacted_thinking` are
never touched: their signature must match.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Analyze = Callable[[str], Awaitable[list[dict]]]


def _entities(name: str, default: str) -> frozenset[str]:
    return frozenset(e.strip() for e in os.getenv(name, default).split(",") if e.strip())


@dataclass(frozen=True)
class Policy:
    mask: frozenset[str]
    block: frozenset[str]
    threshold: float

    @classmethod
    def from_env(cls) -> "Policy":
        return cls(
            mask=_entities("MASK_ENTITIES", "PHONE_NUMBER,EMAIL_ADDRESS,CREDIT_CARD,VN_PHONE,VN_CCCD,VN_TAX_ID,"
                                            "INTERNAL_TERM,ADDRESS,BANK_ACCOUNT"),
            block=_entities("BLOCK_ENTITIES", "SECRET"),
            threshold=float(os.getenv("SCORE_THRESHOLD", "0.75")),
        )

    def relevant(self, findings: list[dict]) -> list[dict]:
        """Policy filter first, then one finding per overlapping region. Doing it the other way round
        lets an unmasked NER hit (PERSON, DATE_TIME) swallow a phone number it overlaps."""
        kept = [f for f in findings
                if f["entity_type"] in self.mask | self.block and float(f.get("score", 0)) >= self.threshold]
        return resolve_overlaps(kept, self.block)


def resolve_overlaps(findings: list[dict], priority: frozenset[str]) -> list[dict]:
    """Winner per overlap: a `priority` (BLOCK) entity, then higher score, then longer span."""
    def rank(item: dict) -> tuple:
        return (item["entity_type"] in priority, float(item.get("score", 0)), int(item["end"]) - int(item["start"]))

    kept: list[dict] = []
    for item in sorted(findings, key=rank, reverse=True):
        start, end = int(item["start"]), int(item["end"])
        if all(end <= int(other["start"]) or start >= int(other["end"]) for other in kept):
            kept.append(item)
    return sorted(kept, key=lambda item: int(item["start"]))


def _strings(node: Any):
    """Yield (container, key) for every string inside node (tool_use input, nested tool_result)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                yield node, key
            else:
                yield from _strings(value)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str):
                yield node, index
            else:
                yield from _strings(value)


def _block_slots(block: Any):
    if not isinstance(block, dict):
        return
    kind = block.get("type")
    if kind == "text" and isinstance(block.get("text"), str):
        yield block, "text"
    elif kind == "tool_use" and "input" in block:
        yield from _strings(block["input"])
    elif kind == "document" and isinstance(block.get("source"), dict):
        source = block["source"]
        if source.get("type") == "text" and isinstance(source.get("data"), str):
            yield source, "data"
        elif source.get("type") == "content" and isinstance(source.get("content"), list):
            for inner in source["content"]:
                yield from _block_slots(inner)
    elif kind == "tool_result":
        content = block.get("content")
        if isinstance(content, str):
            yield block, "content"
        elif isinstance(content, list):
            for inner in content:
                yield from _block_slots(inner)


def text_slots(body: dict):
    """Yield (container, key) for each conversation string, in request order."""
    system = body.get("system")
    if isinstance(system, str):
        yield body, "system"
    elif isinstance(system, list):
        for block in system:
            yield from _block_slots(block)
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            yield message, "content"
        elif isinstance(content, list):
            for block in content:
                yield from _block_slots(block)


@dataclass
class MaskResult:
    body: dict
    tokens: dict[str, str] = field(default_factory=dict)  # placeholder -> original
    masked: Counter = field(default_factory=Counter)
    blocked: list[str] = field(default_factory=list)


class Masker:
    def __init__(self, analyze: Analyze, policy: Policy, cache_size: int = 8192, concurrency: int = 8):
        self.analyze, self.policy = analyze, policy
        # The whole history is resent every turn: cache per text so only new content is analyzed.
        self._cache: OrderedDict[str, list[dict]] = OrderedDict()
        self._cache_size = cache_size
        self._limit = asyncio.Semaphore(concurrency)

    async def _findings(self, text: str) -> list[dict]:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        async with self._limit:
            found = self.policy.relevant(await self.analyze(text))
        self._cache[key] = found
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return found

    async def mask(self, body: dict) -> MaskResult:
        result = MaskResult(body=body)
        slots = [(c, k) for c, k in text_slots(body) if c[k].strip()]
        unique = list(dict.fromkeys(c[k] for c, k in slots))
        found = dict(zip(unique, await asyncio.gather(*(self._findings(t) for t in unique))))

        blocked = sorted({f["entity_type"] for fs in found.values() for f in fs if f["entity_type"] in self.policy.block})
        if blocked:
            result.blocked = blocked
            return result

        # Number placeholders by first appearance; one value always maps to one placeholder,
        # so a resent history masks identically every turn (stable prompt-cache prefix).
        by_value: dict[str, str] = {}
        for container, key in slots:
            text = container[key]
            spans = sorted(found[text], key=lambda f: int(f["start"]))
            for f in spans:
                value = text[int(f["start"]):int(f["end"])]
                if value not in by_value:
                    by_value[value] = f"<{f['entity_type']}_{len(by_value) + 1}>"
            for f in reversed(spans):
                start, end = int(f["start"]), int(f["end"])
                text = text[:start] + by_value[text[start:end]] + text[end:]
                result.masked[f["entity_type"]] += 1
            container[key] = text
        result.tokens = {token: value for value, token in by_value.items()}
        return result
