"""Tier-1 recognizers injected into every Presidio analyze request."""

from __future__ import annotations

import os


def _deny_list() -> list[str]:
    return [item.strip() for item in os.getenv("INTERNAL_DENY_LIST", "").split(",") if item.strip()]


def _score(name: str, default: float) -> float:
    """Base score before context boost. At or above the policy threshold (0.75) the entity is
    masked even without a context word such as "sđt" or "cccd"."""
    return float(os.getenv(name, default))


def recognizers() -> list[dict]:
    rules: list[dict] = [
        {
            "name": "VNPhoneRecognizer",
            "supported_language": "en",
            "supported_entity": "VN_PHONE",
            "patterns": [
                {
                    "name": "vn_mobile",
                    "regex": r"(?<!\d)(?:\+84|0)[ .-]?(?:3[2-9]|5[2689]|7[06-9]|8[1-9]|9\d)(?:[ .-]?\d){7}(?!\d)",
                    "score": _score("VN_PHONE_SCORE", 0.72),
                }
            ],
            "context": ["sđt", "sdt", "điện thoại", "dien thoai", "phone", "hotline", "zalo"],
        },
        {
            "name": "VNCCCDRecognizer",
            "supported_language": "en",
            "supported_entity": "VN_CCCD",
            "patterns": [
                {
                    "name": "cccd_12",
                    "regex": r"(?<!\d)0\d{2}[0-3]\d{8}(?!\d)",
                    "score": _score("VN_CCCD_SCORE", 0.55),
                }
            ],
            "context": ["cccd", "cmnd", "căn cước", "can cuoc", "số định danh", "citizen id"],
        },
        {
            "name": "VNTaxIdRecognizer",
            "supported_language": "en",
            "supported_entity": "VN_TAX_ID",
            "patterns": [
                {
                    "name": "vn_tax_id",
                    "regex": r"(?<!\d)\d{10}(?:-\d{3})?(?!\d)",
                    "score": _score("VN_TAX_ID_SCORE", 0.48),
                }
            ],
            "context": ["mst", "mã số thuế", "ma so thue", "tax id"],
        },
    ]
    deny = _deny_list()
    if deny:
        rules.append(
            {
                "name": "InternalDenyListRecognizer",
                "supported_language": "en",
                "supported_entity": "INTERNAL_TERM",
                "deny_list": deny,
            }
        )
    return rules


def merge_recognizers(payload: dict) -> dict:
    """Return a copy with local rules appended without mutating caller data."""
    merged = dict(payload)
    supplied = list(payload.get("ad_hoc_recognizers") or [])
    names = {item.get("name") for item in supplied}
    supplied.extend(rule for rule in recognizers() if rule["name"] not in names)
    merged["ad_hoc_recognizers"] = supplied
    return merged
