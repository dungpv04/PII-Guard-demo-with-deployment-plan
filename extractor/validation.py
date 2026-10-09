from __future__ import annotations

import io
import os
import zipfile


def detect_kind(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"%PDF-"):
        return "pdf", ".pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image", ".jpg"
    if data.startswith(b"PK\x03\x04"):
        return "office", ".zip"
    return None


def validate(data: bytes) -> tuple[str, str]:
    max_bytes = int(os.getenv("EXTRACT_MAX_BYTES", str(20 * 1024 * 1024)))
    if not data or len(data) > max_bytes:
        raise ValueError("blocked_limits")
    detected = detect_kind(data)
    if not detected:
        raise ValueError("blocked_unsupported")
    kind, suffix = detected
    if kind == "image" and os.getenv("EXTRACT_OCR", "off") == "off":
        raise ValueError("blocked_unsupported")
    if kind == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("blocked_encrypted")
            if len(reader.pages) > int(os.getenv("EXTRACT_MAX_PAGES", "100")):
                raise ValueError("blocked_limits")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("blocked_malformed") from exc
    if kind == "office":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                unpacked = sum(item.file_size for item in entries)
                if len(entries) > int(os.getenv("EXTRACT_MAX_ZIP_ENTRIES", "2000")):
                    raise ValueError("blocked_limits")
                if unpacked > int(os.getenv("EXTRACT_MAX_UNPACKED_BYTES", str(100 * 1024 * 1024))):
                    raise ValueError("blocked_limits")
                if any(item.filename.lower().endswith((".zip", ".7z", ".rar", ".tar", ".gz")) for item in entries):
                    raise ValueError("blocked_limits")
        except ValueError:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValueError("blocked_malformed") from exc
    return kind, suffix
