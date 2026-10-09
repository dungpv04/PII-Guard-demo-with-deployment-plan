import io
import os
import unittest
import zipfile

from extractor.backend import _walk_blocks
from extractor.validation import detect_kind, validate


class ExtractorValidationTests(unittest.TestCase):
    def test_magic_bytes_not_extension_drive_detection(self):
        self.assertEqual(detect_kind(b"%PDF-1.7\n"), ("pdf", ".pdf"))
        self.assertIsNone(detect_kind(b"not a document"))

    def test_images_fail_closed_when_ocr_is_off(self):
        old = os.environ.get("EXTRACT_OCR")
        os.environ["EXTRACT_OCR"] = "off"
        try:
            with self.assertRaisesRegex(ValueError, "blocked_unsupported"):
                validate(b"\x89PNG\r\n\x1a\n" + b"x" * 10)
        finally:
            if old is None:
                os.environ.pop("EXTRACT_OCR", None)
            else:
                os.environ["EXTRACT_OCR"] = old

    def test_nested_archive_is_blocked(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", "safe")
            archive.writestr("word/embedded.zip", "unsafe")
        with self.assertRaisesRegex(ValueError, "blocked_limits"):
            validate(stream.getvalue())


class MarkerOutputTests(unittest.TestCase):
    def test_marker_html_becomes_plain_text(self):
        page = {"page": 0, "children": [
            {"html": "<content-ref src='/page/0/Text/0'></content-ref>"},
            {"html": '<p block-type="Text">SĐT 0912345678 &amp; a &lt; b, <tên></p>', "polygon": [1, 2]}]}
        self.assertEqual([s["text"] for s in _walk_blocks(page)], ["SĐT 0912345678 & a < b, <tên>"])


if __name__ == "__main__":
    unittest.main()
