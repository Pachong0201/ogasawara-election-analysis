"""Conversation-aware orchestration between Feishu, the Skill and the writer."""

from __future__ import annotations

from typing import Optional

from .config import BotConfig
from .conversation import ConversationStore
from .models import BotReply, ConversationState, InboundMessage
from .report_writer import BaseReportWriter
from .router import (
    CAMPAIGN_UPDATE,
    CONTEXT_QA,
    FULL_ANALYSIS,
    HELP,
    POLL_ANALYSIS,
    SOURCES,
    VERSION,
    IntentRouter,
)
from .skill_service import SkillService


ANALYSIS_INTENTS = {FULL_ANALYSIS, CAMPAIGN_UPDATE}
CONTEXT_INTENTS = {POLL_ANALYSIS, SOURCES, CONTEXT_QA}


class ElectionBotService:
    def __init__(
        self,
        config: BotConfig,
        store: ConversationStore,
        router: IntentRouter,
        skill_service: SkillService,
        writer: BaseReportWriter,
    ):
        self.config = config
        self.store = store
        self.router = router
        self.skill_service = skill_service
        self.writer = writer

    async def handle_message(self, message: InboundMessage) -> Optional[BotReply]:
        if (
            self.config.require_mention
            and message.chat_type in {"group", "topic"}
            and not message.mentioned_bot
        ):
            return None

        key = self.store.resolve_key(message)
        self.store.link_message(message.message_id, key)
        state = self.store.load(key)
        request = self.router.parse(message.text, previous_focus=state.focus)

        if request.intent in {HELP, VERSION}:
            text = await self.writer.write(request, state.analysis_context)
            return BotReply(text=text, conversation_key=key, reply_in_thread=True)

        if request.intent in ANALYSIS_INTENTS:
            if not request.focus.jurisdiction:
                return BotReply(
                    text="请在问题中指定县市，例如：分析高雄选情。",
                    conversation_key=key,
                )
            context = await self.skill_service.run(request.focus)
        elif request.intent in CONTEXT_INTENTS:
            same_focus = (
                state.focus.jurisdiction
                and state.focus.jurisdiction == request.focus.jurisdiction
                and state.focus.election_type == request.focus.election_type
                and state.focus.target_year == request.focus.target_year
            )
            if state.analysis_context and same_focus and not request.refresh:
                context = state.analysis_context
            elif request.focus.jurisdiction:
                context = await self.skill_service.run(request.focus)
            else:
                return BotReply(
                    text="当前线程还没有选情上下文。请先指定县市，例如：分析高雄选情。",
                    conversation_key=key,
                )
        else:
            context = state.analysis_context

        new_state = ConversationState(
            key=key,
            focus=request.focus,
            analysis_context=context or state.analysis_context,
            last_user_text=request.text,
        )
        self.store.save(new_state)
        text = await self.writer.write(request, new_state.analysis_context)
        return BotReply(text=text, conversation_key=key, reply_in_thread=True)

    def link_outbound_message(self, message_id: str, conversation_key: str) -> None:
        self.store.link_message(message_id, conversation_key)
