import tempfile
import unittest
from pathlib import Path

from bot.config import BotConfig
from bot.conversation import ConversationStore
from bot.models import InboundMessage
from bot.router import IntentRouter
from bot.service import ElectionBotService


class FakeSkill:
    def __init__(self):
        self.calls = []

    async def run(self, focus):
        self.calls.append(focus.to_dict())
        return {
            "analysis_context": {
                "task": focus.to_dict(),
                "campaign_state": {"as_of": "2026-09-23T00:00:00+08:00"},
                "polls": [],
                "sources": [{"source_id": "fixture", "source_grade": "A"}],
            },
            "analysis_manifest": {"skill_version": "1.4.0"},
        }


class FakeWriter:
    def __init__(self):
        self.calls = []

    async def write(self, request, context):
        self.calls.append((request.intent, context))
        return f"intent={request.intent};county={request.focus.jurisdiction}"


def inbound(
    text,
    message_id="m1",
    mentioned=True,
    reply_to="",
    chat_type="group",
):
    return InboundMessage(
        message_id=message_id,
        chat_id="chat1",
        chat_type=chat_type,
        sender_id="u1",
        text=text,
        mentioned_bot=mentioned,
        reply_to_message_id=reply_to,
    )


class TestElectionBotService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = BotConfig(
            repo_root=root,
            require_mention=True,
            conversation_db=root / "state.sqlite3",
        )
        self.store = ConversationStore(self.config.conversation_db)
        self.skill = FakeSkill()
        self.writer = FakeWriter()
        self.service = ElectionBotService(
            config=self.config,
            store=self.store,
            router=IntentRouter(),
            skill_service=self.skill,
            writer=self.writer,
        )

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_group_message_without_mention_is_ignored(self):
        reply = await self.service.handle_message(
            inbound("分析高雄选情", mentioned=False)
        )
        self.assertIsNone(reply)
        self.assertEqual(self.skill.calls, [])

    async def test_full_analysis_runs_skill_and_saves_focus(self):
        reply = await self.service.handle_message(inbound("分析高雄选情"))
        self.assertIsNotNone(reply)
        self.assertEqual(len(self.skill.calls), 1)
        state = self.store.load(reply.conversation_key)
        self.assertEqual(state.focus.jurisdiction, "高雄市")
        self.assertTrue(state.analysis_context)

    async def test_followup_reply_reuses_context_without_rerunning_skill(self):
        first = await self.service.handle_message(inbound("分析高雄选情", message_id="m1"))
        self.service.link_outbound_message("bot1", first.conversation_key)
        second = await self.service.handle_message(
            inbound("为什么凤山重要？", message_id="m2", reply_to="bot1")
        )
        self.assertEqual(second.conversation_key, first.conversation_key)
        self.assertEqual(len(self.skill.calls), 1)
        self.assertIn("county=高雄市", second.text)

    async def test_campaign_update_reruns_skill(self):
        first = await self.service.handle_message(inbound("分析高雄选情", message_id="m1"))
        self.service.link_outbound_message("bot1", first.conversation_key)
        await self.service.handle_message(
            inbound("更新一下", message_id="m2", reply_to="bot1")
        )
        self.assertEqual(len(self.skill.calls), 2)

    async def test_sources_reuse_existing_context(self):
        first = await self.service.handle_message(inbound("分析高雄选情", message_id="m1"))
        self.service.link_outbound_message("bot1", first.conversation_key)
        await self.service.handle_message(
            inbound("给我看依据", message_id="m2", reply_to="bot1")
        )
        self.assertEqual(len(self.skill.calls), 1)

    async def test_latest_poll_refreshes_existing_context(self):
        first = await self.service.handle_message(inbound("分析高雄选情", message_id="m1"))
        self.service.link_outbound_message("bot1", first.conversation_key)
        await self.service.handle_message(
            inbound("最新民调怎么看", message_id="m2", reply_to="bot1")
        )
        self.assertEqual(len(self.skill.calls), 2)


if __name__ == "__main__":
    unittest.main()
