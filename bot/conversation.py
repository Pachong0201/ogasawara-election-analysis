"""Persistent conversation state for group-thread and DM interactions."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import ConversationState, ElectionFocus, InboundMessage


class ConversationStore:
    """SQLite-backed state store.

    DMs share one conversation per chat. Group root messages create isolated
    conversation keys. Replies to bot messages resolve through message_links,
    preventing unrelated group discussions from contaminating one another.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_state (
                    conversation_key TEXT PRIMARY KEY,
                    focus_json TEXT NOT NULL,
                    analysis_context_json TEXT NOT NULL,
                    last_user_text TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS message_links (
                    message_id TEXT PRIMARY KEY,
                    conversation_key TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _linked_key(self, message_id: str) -> Optional[str]:
        if not message_id:
            return None
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT conversation_key FROM message_links WHERE message_id=?",
                (message_id,),
            ).fetchone()
        return str(row["conversation_key"]) if row else None

    def resolve_key(self, message: InboundMessage) -> str:
        if message.chat_type == "p2p":
            return f"dm:{message.chat_id}"
        if message.thread_id:
            return f"thread:{message.chat_id}:{message.thread_id}"
        linked = self._linked_key(message.reply_to_message_id)
        if linked:
            return linked
        # Group roots use the future thread root id so replies carrying\n        # conversation.thread_id resolve to exactly the same key.\n        return f"thread:{message.chat_id}:{message.message_id}"

    def load(self, key: str) -> ConversationState:
        with self._lock, self._connect() as con:
            row = con.execute(
                """
                SELECT focus_json, analysis_context_json, last_user_text
                FROM conversation_state WHERE conversation_key=?
                """,
                (key,),
            ).fetchone()
        if not row:
            return ConversationState(key=key)
        try:
            focus = ElectionFocus.from_dict(json.loads(row["focus_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            focus = ElectionFocus()
        try:
            context = json.loads(row["analysis_context_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            context = {}
        return ConversationState(
            key=key,
            focus=focus,
            analysis_context=context if isinstance(context, dict) else {},
            last_user_text=str(row["last_user_text"] or ""),
        )

    def save(self, state: ConversationState) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO conversation_state(
                    conversation_key, focus_json, analysis_context_json,
                    last_user_text, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(conversation_key) DO UPDATE SET
                    focus_json=excluded.focus_json,
                    analysis_context_json=excluded.analysis_context_json,
                    last_user_text=excluded.last_user_text,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    state.key,
                    json.dumps(state.focus.to_dict(), ensure_ascii=False),
                    json.dumps(state.analysis_context or {}, ensure_ascii=False),
                    state.last_user_text,
                ),
            )

    def link_message(self, message_id: str, conversation_key: str) -> None:
        if not message_id:
            return
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO message_links(message_id, conversation_key, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(message_id) DO UPDATE SET
                    conversation_key=excluded.conversation_key,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (message_id, conversation_key),
            )
