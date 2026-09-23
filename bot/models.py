"""Bot-facing data models kept independent from Feishu SDK types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    text: str
    mentioned_bot: bool = False
    thread_id: str = ""
    reply_to_message_id: str = ""


@dataclass
class ElectionFocus:
    jurisdiction: str = ""
    election_type: str = "county_mayor"
    target_year: int = 2026
    analysis_level: str = "township_district"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction,
            "election_type": self.election_type,
            "target_year": self.target_year,
            "analysis_level": self.analysis_level,
        }

    @classmethod
    def from_dict(cls, value: Optional[Dict[str, Any]]) -> "ElectionFocus":
        value = value or {}
        return cls(
            jurisdiction=str(value.get("jurisdiction") or ""),
            election_type=str(value.get("election_type") or "county_mayor"),
            target_year=int(value.get("target_year") or 2026),
            analysis_level=str(value.get("analysis_level") or "township_district"),
        )


@dataclass
class ParsedRequest:
    intent: str
    text: str
    focus: ElectionFocus
    window_days: Optional[int] = None
    refresh: bool = False


@dataclass
class ConversationState:
    key: str
    focus: ElectionFocus = field(default_factory=ElectionFocus)
    analysis_context: Dict[str, Any] = field(default_factory=dict)
    last_user_text: str = ""


@dataclass
class BotReply:
    text: str
    conversation_key: str
    reply_in_thread: bool = True
