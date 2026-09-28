import unittest
from types import SimpleNamespace

from bot.feishu_app import send_reply


class FakeChannel:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def send(self, chat_id, payload, options):
        self.calls.append((chat_id, payload, options))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def inbound():
    return SimpleNamespace(
        message_id="message-1",
        chat_id="chat-1",
        chat_type="group",
    )


class TestFeishuReplyFallbacks(unittest.IsolatedAsyncioTestCase):
    async def test_docx_send_exception_falls_back_to_markdown(self):
        channel = FakeChannel([
            RuntimeError("file upload unavailable"),
            SimpleNamespace(success=True, message_id="markdown-1"),
        ])

        result = await send_reply(channel, inbound(), "x" * 500)

        self.assertTrue(result.success)
        self.assertEqual(len(channel.calls), 2)
        self.assertIn("file", channel.calls[0][1])
        self.assertIn("markdown", channel.calls[1][1])

    async def test_markdown_send_exception_falls_back_to_plain_text(self):
        channel = FakeChannel([
            RuntimeError("file upload unavailable"),
            RuntimeError("markdown unavailable"),
            SimpleNamespace(success=True, message_id="text-1"),
        ])

        result = await send_reply(channel, inbound(), "x" * 500)

        self.assertTrue(result.success)
        self.assertEqual(len(channel.calls), 3)
        self.assertIn("text", channel.calls[2][1])


if __name__ == "__main__":
    unittest.main()
