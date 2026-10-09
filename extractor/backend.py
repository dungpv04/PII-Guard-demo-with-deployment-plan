from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
from typing import Any


# Marker's JSON carries block HTML. Strip only Marker's own markup so downstream gets plain text;
# a generic `<...>` strip would also eat legitimate text such as "a < b" or "<tên>".
_MARKUP = re.compile(r"</?(?:p|h[1-6]|ul|ol|li|table|thead|tbody|tr|td|th|span|div|b|i|em|strong|br|"
                     r"content-ref|sup|sub|a|img|math|code|pre|blockquote)\b[^>]*>", re.IGNORECASE)


def _plain(text: str) -> str:
    return html.unescape(_MARKUP.sub("", text)).strip()


def _walk_blocks(value: Any, page: int | None = None) -> list[dict]:
    segments: list[dict] = []
    if isinstance(value, dict):
        current_page = value.get("page", value.get("page_id", page))
        text = value.get("text") or value.get("html")
        text = _plain(text) if isinstance(text, str) else None
        if text:
            polygon = value.get("polygon") or value.get("bbox")
            segments.append(
                {
                    "text": text,
                    "page": current_page,
                    "bbox": polygon,
                    "block_type": value.get("block_type") or value.get("block_type_name") or "text",
                }
            )
        for key, child in value.items():
            if key not in {"text", "html", "polygon", "bbox"}:
                segments.extend(_walk_blocks(child, current_page))
    elif isinstance(value, list):
        for child in value:
            segments.extend(_walk_blocks(child, page))
    return segments


def _read_output(output_dir: Path) -> list[dict]:
    json_files = sorted(output_dir.rglob("*.json"))
    for path in json_files:
        try:
            segments = _walk_blocks(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if segments:
            return segments
    markdown_files = sorted(output_dir.rglob("*.md"))
    for path in markdown_files:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return [{"text": text, "page": None, "bbox": None, "block_type": "markdown"}]
    raise RuntimeError("marker returned no parseable output")


def marker_extract(data: bytes, suffix: str, timeout_s: float) -> list[dict]:
    ocr_mode = os.getenv("EXTRACT_OCR", "off")
    if ocr_mode not in {"off", "auto", "force"}:
        raise ValueError("EXTRACT_OCR must be off, auto, or force")
    with tempfile.TemporaryDirectory(prefix="extract-") as temp:
        temp_dir = Path(temp)
        source = temp_dir / f"input{suffix}"
        output = temp_dir / "output"
        output.mkdir()
        source.write_bytes(data)
        command = [
            os.getenv("MARKER_COMMAND", "marker_single"),
            str(source),
            "--output_dir",
            str(output),
            "--output_format",
            "json",
        ]
        if ocr_mode == "off":
            # Marker 2's regular fast mode still starts shared layout/error model servers. For the
            # phase-1 text-layer path, forcing one Text block per page avoids model/network access.
            command.extend(["--disable_ocr", "--mode", "fast", "--force_layout_block", "Text"])
        elif ocr_mode == "force":
            command.append("--force_ocr")
        command.extend(["--disable_multiprocessing", "--disable_tqdm"])
        subprocess.run(
            command,
            check=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        )
        return _read_output(output)
