import asyncio
import copy
import json
import unittest

from claude_proxy.mask import Masker, Policy, resolve_overlaps

POLICY = Policy(mask=frozenset({"EMAIL_ADDRESS", "VN_PHONE"}), block=frozenset({"SECRET"}), threshold=0.75)
NEEDLES = (("an@example.com", "EMAIL_ADDRESS", 1.0), ("0912345678", "VN_PHONE", 1.0),
           ("hunter2", "SECRET", 0.9), ("Acme", "VN_PHONE", 0.3))


class MaskerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        async def analyze(text):
            self.calls.append(text)
            found, start = [], 0
            for needle, entity, score in NEEDLES:
                start = text.find(needle)
                while start >= 0:
                    found.append({"entity_type": entity, "start": start, "end": start + len(needle), "score": score})
                    start = text.find(needle, start + 1)
            return found

        self.masker = Masker(analyze, POLICY)

    def mask(self, body):
        return asyncio.run(self.masker.mask(body))

    def request(self):
        return {
            "model": "claude-opus-5-5", "system": [{"type": "text", "text": "x-anthropic-billing-header: cc"},
                                                   {"type": "text", "text": "env: an@example.com", "cache_control": {"type": "ephemeral"}}],
            "tools": [{"name": "Bash", "description": "mail an@example.com", "input_schema": {"$schema": "x"}}],
            "safeguards": [{"type": "dangerous_tool_use", "classifier_context": {"home_dir": "/home/an@example.com"}}],
            "messages": [
                {"role": "user", "content": "call 0912345678 or an@example.com"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "0912345678", "signature": "sig"},
                    {"type": "text", "text": "ok 0912345678"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo 0912345678"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "0912345678"}]},
                    {"type": "text", "text": "Acme"}]},
            ],
        }

    def test_masks_conversation_text_only(self):
        body = self.request()
        original = copy.deepcopy(body)
        result = self.mask(body)
        self.assertEqual(body["system"][1]["text"], "env: <EMAIL_ADDRESS_1>")
        self.assertEqual(body["messages"][0]["content"], "call <VN_PHONE_2> or <EMAIL_ADDRESS_1>")
        assistant = body["messages"][1]["content"]
        self.assertEqual(assistant[0], original["messages"][1]["content"][0])          # thinking untouched
        self.assertEqual(assistant[1]["text"], "ok <VN_PHONE_2>")
        self.assertEqual(assistant[2]["input"], {"command": "echo <VN_PHONE_2>"})
        self.assertEqual(body["messages"][2]["content"][0]["content"][0]["text"], "<VN_PHONE_2>")
        self.assertEqual(body["messages"][2]["content"][1]["text"], "Acme")             # below threshold
        for untouched in ("tools", "safeguards", "model"):
            self.assertEqual(body[untouched], original[untouched])
        self.assertEqual(body["system"][1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(result.tokens, {"<EMAIL_ADDRESS_1>": "an@example.com", "<VN_PHONE_2>": "0912345678"})

    def test_same_history_masks_identically_and_is_cached(self):
        first = self.mask(self.request()).body
        calls = len(self.calls)
        second = self.mask(self.request()).body
        self.assertEqual(json.dumps(first), json.dumps(second))
        self.assertEqual(len(self.calls), calls)

    def test_unmasked_ner_hit_cannot_swallow_a_phone(self):
        found = [{"entity_type": "PERSON", "start": 0, "end": 14, "score": 0.85},
                 {"entity_type": "VN_PHONE", "start": 4, "end": 14, "score": 1.0},
                 {"entity_type": "DATE_TIME", "start": 4, "end": 14, "score": 0.9}]
        self.assertEqual([f["entity_type"] for f in POLICY.relevant(found)], ["VN_PHONE"])

    def test_overlap_keeps_block_entity_then_score(self):
        found = [{"entity_type": "EMAIL_ADDRESS", "start": 0, "end": 40, "score": 1.0},
                 {"entity_type": "SECRET", "start": 10, "end": 20, "score": 0.76},
                 {"entity_type": "VN_PHONE", "start": 50, "end": 60, "score": 0.8},
                 {"entity_type": "EMAIL_ADDRESS", "start": 55, "end": 70, "score": 1.0}]
        self.assertEqual([f["entity_type"] for f in resolve_overlaps(found, frozenset({"SECRET"}))],
                         ["SECRET", "EMAIL_ADDRESS"])

    def test_secret_blocks(self):
        result = self.mask({"messages": [{"role": "user", "content": "pw hunter2"}]})
        self.assertEqual(result.blocked, ["SECRET"])

    def test_no_findings_leaves_body_and_tokens_empty(self):
        body = {"messages": [{"role": "user", "content": "hello"}], "tools": []}
        result = self.mask(body)
        self.assertEqual(result.tokens, {})
        self.assertEqual(body, {"messages": [{"role": "user", "content": "hello"}], "tools": []})

    def test_analyzer_failure_propagates(self):
        async def broken(_):
            raise ConnectionError("down")
        with self.assertRaises(ConnectionError):
            asyncio.run(Masker(broken, POLICY).mask({"messages": [{"role": "user", "content": "hi"}]}))


if __name__ == "__main__":
    unittest.main()
