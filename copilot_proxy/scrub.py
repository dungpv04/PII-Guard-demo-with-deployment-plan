"""Mask PII in Copilot request bodies with the same analyzer-plus + Presidio anonymizer as LiteLLM.

Copilot's wire format is not a public API, so instead of parsing each endpoint we walk the JSON
and scan every string under a text-bearing key. That covers OpenAI chat (`messages[].content`),
legacy completions (`prompt`, `suffix`), Responses (`input`, `instructions`) and Anthropic
messages (`system`, `content[].text`). Masking is one-way (`<EMAIL_ADDRESS>`): Copilot responses
are not rehydrated, so a masked value never comes back to the editor.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

TEXT_KEYS = frozenset({"content", "text", "prompt", "suffix", "input", "instructions", "system"})


def iter_text_slots(node: Any, key: str | None = None):
    """Yield (container, index) for every string stored under a text-bearing key."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                if k in TEXT_KEYS:
                    yield node, k
            else:
                yield from iter_text_slots(v, k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                if key in TEXT_KEYS:
                    yield node, i
            else:
                yield from iter_text_slots(v, key)


def _entities(name: str, default: str) -> frozenset[str]:
    return frozenset(e.strip() for e in os.getenv(name, default).split(",") if e.strip())


@dataclass(frozen=True)
class Policy:
    """Same entity policy as litellm/config.yaml (pii_entities_config)."""

    mask: frozenset[str]
    block: frozenset[str]
    threshold: float

    @classmethod
    def from_env(cls) -> "Policy":
        return cls(
            mask=_entities("COPILOT_MASK_ENTITIES", "PHONE_NUMBER,EMAIL_ADDRESS,CREDIT_CARD,VN_PHONE,VN_CCCD,VN_TAX_ID,"
                                                    "INTERNAL_TERM,ADDRESS,BANK_ACCOUNT"),
            block=_entities("COPILOT_BLOCK_ENTITIES", "SECRET"),
            threshold=float(os.getenv("COPILOT_SCORE_THRESHOLD", "0.75")),
        )

    def relevant(self, findings: list[dict]) -> list[dict]:
        return [f for f in findings
                if f["entity_type"] in self.mask | self.block and float(f.get("score", 0)) >= self.threshold]


@dataclass
class ScrubResult:
    body: Any
    masked: Counter = field(default_factory=Counter)
    blocked: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.masked)


class Scrubber:
    def __init__(self, analyze: Callable[[str], list[dict]], anonymize: Callable[[str, list[dict]], str],
                 policy: Policy, cache_size: int = 4096):
        self.analyze, self.anonymize, self.policy = analyze, anonymize, policy
        # Chat history is resent every turn; cache per text so only new content hits the analyzer.
        self._cache: OrderedDict[str, tuple[str, list[dict]]] = OrderedDict()
        self._cache_size = cache_size

    def _scan(self, text: str) -> tuple[str, list[dict]]:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        findings = self.policy.relevant(self.analyze(text))
        masked = self.anonymize(text, findings) if findings else text
        self._cache[key] = (masked, findings)
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return masked, findings

    def scrub(self, body: Any) -> ScrubResult:
        result = ScrubResult(body=body)
        for container, index in list(iter_text_slots(body)):
            text = container[index]
            if not text.strip():
                continue
            masked, findings = self._scan(text)
            blocked = sorted({f["entity_type"] for f in findings if f["entity_type"] in self.policy.block})
            if blocked:
                result.blocked = sorted(set(result.blocked) | set(blocked))
                continue
            if findings:
                container[index] = masked
                result.masked.update(f["entity_type"] for f in findings)
        return result


def _post_json(url: str, payload: dict, timeout: float) -> Any:
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def presidio_scrubber() -> Scrubber:
    analyzer = os.getenv("ANALYZER_URL", "http://presidio-analyzer:3000").rstrip("/")
    anonymizer = os.getenv("ANONYMIZER_URL", "http://presidio-anonymizer:3000").rstrip("/")
    timeout = float(os.getenv("COPILOT_ANALYZE_TIMEOUT_S", "10"))

    def analyze(text: str) -> list[dict]:
        findings = _post_json(f"{analyzer}/analyze", {"text": text, "language": "en"}, timeout)
        if not isinstance(findings, list):
            raise ValueError("invalid analyzer response")
        return findings

    def anonymize(text: str, findings: list[dict]) -> str:
        response = _post_json(f"{anonymizer}/anonymize", {
            "text": text, "analyzer_results": findings, "anonymizers": {"DEFAULT": {"type": "replace"}},
        }, timeout)
        return response["text"]

    return Scrubber(analyze, anonymize, Policy.from_env())
