import tempfile
import unittest
from pathlib import Path

from bot.conversation import ConversationStore
from bot.models import ConversationState, ElectionFocus, InboundMessage


def message(
    message_id="m1",
    chat_id="c1",
    chat_type="group",
    thread_id="",
    reply_to="",
):
    return InboundMessage(
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        sender_id="u1",
        text="test",
        mentioned_bot=True,
        thread_id=thread_id,
        reply_to_message_id=reply_to,
    )


class TestConversationStore(unittest.TestCase):
    def test_group_roots_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "state.sqlite3")
            key1 = store.resolve_key(message(message_id="m1"))
            key2 = store.resolve_key(message(message_id="m2"))
            self.assertNotEqual(key1, key2)

    def test_dm_reuses_one_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "state.sqlite3")
            key1 = store.resolve_key(message(message_id="m1", chat_type="p2p"))
            key2 = store.resolve_key(message(message_id="m2", chat_type="p2p"))
            self.assertEqual(key1, key2)

    def test_reply_to_bot_message_resolves_original_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "state.sqlite3")
            original = message(message_id="user-root")
            key = store.resolve_key(original)
            store.link_message("bot-reply", key)
            followup = message(message_id="follow", reply_to="bot-reply")
            self.assertEqual(store.resolve_key(followup), key)

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "state.sqlite3")
            state = ConversationState(
                key="k",
                focus=ElectionFocus(jurisdiction="高雄市"),
                analysis_context={"analysis_context": {"x": 1}},
                last_user_text="分析高雄",
            )
            store.save(state)
            loaded = store.load("k")
            self.assertEqual(loaded.focus.jurisdiction, "高雄市")
            self.assertEqual(loaded.analysis_context["analysis_context"]["x"], 1)


if __name__ == "__main__":
    unittest.main()
