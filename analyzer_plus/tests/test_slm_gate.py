import unittest

from analyzer_plus.rules import merge_recognizers
from analyzer_plus.slm_gate import Window, drop_allowed, merge_findings, select_windows, validate_spans


class SLMGateTests(unittest.TestCase):
    def test_hallucinated_and_unknown_spans_are_rejected(self):
        window = Window("Khách hàng Acme dùng mã Project Lotus", 10)
        raw = {"spans": [
            {"text": "Project Lotus", "label": "INTERNAL_TERM"},
            {"text": "not in input", "label": "SECRET"},
            {"text": "Acme", "label": "UNSAFE_LABEL"},
        ]}
        self.assertEqual(validate_spans(raw, window, 8), [{
            "entity_type": "INTERNAL_TERM", "start": 34, "end": 47, "score": 0.76,
            "recognition_metadata": {"recognizer_name": "LocalSLMSecondOpinion"},
        }])

    def test_span_cap_is_enforced(self):
        window = Window("secret secret secret", 0)
        self.assertEqual(len(validate_spans({"spans": [{"text": "secret", "label": "SECRET"}]}, window, 2)), 2)

    def test_windows_are_bounded(self):
        text = "x" * 400 + " password " + "y" * 400
        windows = select_windows(text, [], max_windows=1, radius=40)
        self.assertEqual(len(windows), 1)
        self.assertLessEqual(len(windows[0].text), 80)

    def test_additions_never_remove_existing_findings(self):
        old = [{"entity_type": "EMAIL_ADDRESS", "start": 0, "end": 3, "score": 0.9}]
        new = [{"entity_type": "SECRET", "start": 5, "end": 8, "score": 0.76}]
        merged = merge_findings(old, new)
        self.assertIn(old[0], merged)
        self.assertIn(new[0], merged)

    def test_allow_listed_text_is_never_masked(self):
        text = "Co-Authored-By: Claude <NoReply@anthropic.com>, user an@example.com"
        found = [{"entity_type": "EMAIL_ADDRESS", "start": 24, "end": 45, "score": 1.0},
                 {"entity_type": "EMAIL_ADDRESS", "start": 53, "end": 67, "score": 1.0}]
        kept = drop_allowed(found, text, frozenset({"noreply@anthropic.com"}))
        self.assertEqual([text[f["start"]:f["end"]] for f in kept], ["an@example.com"])

    def test_local_recognizers_are_always_injected(self):
        merged = merge_recognizers({"text": "hello", "ad_hoc_recognizers": []})
        names = {item["name"] for item in merged["ad_hoc_recognizers"]}
        self.assertIn("VNPhoneRecognizer", names)
        self.assertIn("VNCCCDRecognizer", names)


if __name__ == "__main__":
    unittest.main()
