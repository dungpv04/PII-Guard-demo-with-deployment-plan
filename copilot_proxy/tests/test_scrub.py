import unittest

from copilot_proxy.scrub import Policy, Scrubber, iter_text_slots

POLICY = Policy(mask=frozenset({"EMAIL_ADDRESS", "VN_PHONE"}), block=frozenset({"SECRET"}), threshold=0.75)


def fake_analyze(text):
    found = []
    for needle, entity, score in (("an@example.com", "EMAIL_ADDRESS", 1.0), ("0912345678", "VN_PHONE", 1.0),
                                  ("hunter2", "SECRET", 0.9), ("Acme", "VN_PHONE", 0.3)):
        start = text.find(needle)
        if start >= 0:
            found.append({"entity_type": entity, "start": start, "end": start + len(needle), "score": score})
    return found


def fake_anonymize(text, findings):
    for f in sorted(findings, key=lambda f: f["start"], reverse=True):
        text = text[:f["start"]] + f"<{f['entity_type']}>" + text[f["end"]:]
    return text


class ScrubTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def analyze(text):
            self.calls.append(text)
            return fake_analyze(text)

        self.scrubber = Scrubber(analyze, fake_anonymize, POLICY)

    def test_chat_completions_messages_are_masked(self):
        body = {"model": "gpt", "messages": [
            {"role": "system", "content": "You are Copilot"},
            {"role": "user", "content": [{"type": "text", "text": "mail an@example.com, Acme"}]},
        ]}
        result = self.scrubber.scrub(body)
        self.assertEqual(body["messages"][1]["content"][0]["text"], "mail <EMAIL_ADDRESS>, Acme")
        self.assertEqual(body["model"], "gpt")
        self.assertEqual(dict(result.masked), {"EMAIL_ADDRESS": 1})

    def test_inline_completion_prompt_and_suffix_are_masked(self):
        body = {"prompt": "# call 0912345678\n", "suffix": "x = 'an@example.com'", "extra": {"language": "python"}}
        self.scrubber.scrub(body)
        self.assertEqual(body["prompt"], "# call <VN_PHONE>\n")
        self.assertEqual(body["suffix"], "x = '<EMAIL_ADDRESS>'")
        self.assertEqual(body["extra"]["language"], "python")

    def test_secret_blocks_request(self):
        result = self.scrubber.scrub({"input": [{"content": [{"type": "input_text", "text": "pw hunter2"}]}]})
        self.assertEqual(result.blocked, ["SECRET"])

    def test_identifiers_and_non_text_keys_are_not_scanned(self):
        slots = list(iter_text_slots({"model": "an@example.com", "id": "x", "prompt": ["a", "b"]}))
        self.assertEqual([index for _, index in slots], [0, 1])

    def test_repeated_history_is_analyzed_once(self):
        for _ in range(3):
            self.scrubber.scrub({"messages": [{"role": "user", "content": "mail an@example.com"}]})
        self.assertEqual(len(self.calls), 1)

    def test_analyzer_failure_propagates_for_fail_closed(self):
        def broken(_):
            raise ConnectionError("down")
        with self.assertRaises(ConnectionError):
            Scrubber(broken, fake_anonymize, POLICY).scrub({"prompt": "hello"})


if __name__ == "__main__":
    unittest.main()
