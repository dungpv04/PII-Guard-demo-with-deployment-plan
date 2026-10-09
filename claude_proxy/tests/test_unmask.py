import json
import unittest

from claude_proxy.unmask import SSEUnmasker, rehydrate_message

TOKENS = {"<VN_PHONE_1>": "0912345678", "<EMAIL_ADDRESS_2>": 'a"b@example.com', "<VN_PHONE_10>": "0900000010"}


def sse(event):
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


def delta(index, kind, value):
    field = "text" if kind == "text_delta" else "partial_json"
    return sse({"type": "content_block_delta", "index": index, "delta": {"type": kind, field: value}})


def collect(raw: bytes):
    """Concatenate delta payloads per block from an SSE byte stream."""
    out = {}
    for block in raw.decode().split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                event = json.loads(line[5:])
                if event["type"] == "content_block_delta":
                    d = event["delta"]
                    out[event["index"]] = out.get(event["index"], "") + d.get("text", d.get("partial_json", ""))
    return out


class SSEUnmaskerTests(unittest.TestCase):
    def run_stream(self, events, chunk_size=None):
        stream = b"".join(events)
        unmasker = SSEUnmasker(TOKENS)
        if chunk_size:
            pieces = [stream[i:i + chunk_size] for i in range(0, len(stream), chunk_size)]
        else:
            pieces = [stream]
        return b"".join(unmasker.feed(p) for p in pieces) + unmasker.flush()

    def test_placeholder_split_across_text_deltas(self):
        out = self.run_stream([delta(0, "text_delta", "SĐT: <VN_"), delta(0, "text_delta", "PHONE_1> xong"),
                               sse({"type": "content_block_stop", "index": 0})])
        self.assertEqual(collect(out)[0], "SĐT: 0912345678 xong")

    def test_tool_input_is_rehydrated_and_json_escaped(self):
        out = self.run_stream([delta(1, "input_json_delta", '{"command": "echo <EMAIL_ADD'),
                               delta(1, "input_json_delta", 'RESS_2>"}'), sse({"type": "content_block_stop", "index": 1})])
        self.assertEqual(json.loads(collect(out)[1]), {"command": 'echo a"b@example.com'})

    def test_tail_flushed_at_block_stop(self):
        out = self.run_stream([delta(0, "text_delta", "a < b and <VN_PH"), sse({"type": "content_block_stop", "index": 0})])
        self.assertEqual(collect(out)[0], "a < b and <VN_PH")

    def test_arbitrary_byte_chunking(self):
        events = [delta(0, "text_delta", "x <VN_PHONE_1"), delta(0, "text_delta", "0> y <VN_PHONE_1>"),
                  sse({"type": "content_block_stop", "index": 0})]
        for size in (1, 7, 64):
            self.assertEqual(collect(self.run_stream(events, size))[0], "x 0900000010 y 0912345678")

    def test_thinking_and_other_events_pass_through(self):
        thinking = sse({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "<VN_PHONE_1>"}})
        self.assertEqual(self.run_stream([thinking]), thinking)

    def test_non_streaming_message(self):
        msg = {"type": "message", "content": [
            {"type": "thinking", "thinking": "<VN_PHONE_1>", "signature": "s"},
            {"type": "text", "text": "call <VN_PHONE_1>"},
            {"type": "tool_use", "input": {"command": "echo <VN_PHONE_1>"}}]}
        rehydrate_message(msg, TOKENS)
        self.assertEqual(msg["content"][0]["thinking"], "<VN_PHONE_1>")
        self.assertEqual(msg["content"][1]["text"], "call 0912345678")
        self.assertEqual(msg["content"][2]["input"]["command"], "echo 0912345678")


if __name__ == "__main__":
    unittest.main()
