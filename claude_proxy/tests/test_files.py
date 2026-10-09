import asyncio
import base64
import unittest

from claude_proxy.files import FileHandler

PDF = base64.b64encode(b"%PDF-1.7 fake").decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def request():
    return {"messages": [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG},
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "xem file"}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": PDF}}]},
            {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "plain text"}}]},
    ]}


class FileHandlerTests(unittest.TestCase):
    def run_handler(self, handler, body):
        return asyncio.run(handler.process(body))

    def test_block_replaces_every_binary_file_and_keeps_cache_control(self):
        body = request()
        outcomes = self.run_handler(FileHandler(None, "block"), body)
        image = body["messages"][0]["content"][0]
        self.assertEqual(image["type"], "text")
        self.assertIn("removed", image["text"])
        self.assertEqual(image["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["messages"][1]["content"][0]["content"][0]["type"], "text")
        self.assertEqual(body["messages"][1]["content"][1]["source"]["data"], "plain text")  # text docs untouched
        self.assertEqual(dict(outcomes), {"image:blocked": 1, "document:blocked": 1})

    def test_extract_replaces_file_with_text_and_caches(self):
        calls = []

        async def extract(data):
            calls.append(data)
            if data.startswith(b"%PDF"):
                return {"status": "ok", "segments": [{"text": "SĐT 0912345678"}, {"text": "trang 2"}]}
            return {"status": "blocked_unsupported", "segments": []}

        handler = FileHandler(extract, "extract")
        body = request()
        self.run_handler(handler, body)
        pdf_text = body["messages"][1]["content"][0]["content"][0]["text"]
        self.assertIn("SĐT 0912345678\n\ntrang 2", pdf_text)
        self.assertIn("blocked_unsupported", body["messages"][0]["content"][0]["text"])
        self.run_handler(handler, request())
        self.assertEqual(len([c for c in calls if c.startswith(b"%PDF")]), 1)

    def test_extractor_failure_drops_the_file(self):
        async def broken(_):
            raise ConnectionError("down")
        body = request()
        self.run_handler(FileHandler(broken, "extract"), body)
        self.assertIn("extractor unavailable", body["messages"][1]["content"][0]["content"][0]["text"])

    def test_pass_leaves_files(self):
        body = request()
        self.run_handler(FileHandler(None, "pass"), body)
        self.assertEqual(body, request())


if __name__ == "__main__":
    unittest.main()
