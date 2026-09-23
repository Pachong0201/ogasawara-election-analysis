"""Run the Feishu bot using the official long-connection channel SDK."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import BotConfig
from .conversation import ConversationStore
from .models import InboundMessage
from .report_writer import build_report_writer
from .router import IntentRouter, looks_long_running
from .service import ElectionBotService
from .skill_service import SkillService
from runtime.gdelt_retrieval import GDELTNewsBackend


LOG = logging.getLogger("ogasawara.feishu")


def _inbound(message: Any) -> InboundMessage:
    conversation = getattr(message, "conversation", None)
    return InboundMessage(
        message_id=str(getattr(message, "message_id", None) or getattr(message, "id", "")),
        chat_id=str(getattr(message, "chat_id", "") or ""),
        chat_type=str(getattr(message, "chat_type", "") or "unknown"),
        sender_id=str(getattr(message, "sender_id", "") or ""),
        text=str(getattr(message, "content_text", "") or ""),
        mentioned_bot=bool(getattr(message, "mentioned_bot", False)),
        thread_id=str(getattr(conversation, "thread_id", "") or ""),
        reply_to_message_id=str(getattr(message, "reply_to_message_id", "") or ""),
    )


def build_service(config: BotConfig) -> ElectionBotService:
    store = ConversationStore(config.conversation_db)
    router = IntentRouter(default_target_year=config.default_target_year)
    retrieval = (GDELTNewsBackend(max_body_fetches=config.max_article_fetches)
                 if config.retrieval_provider == "gdelt" and config.skill_mode != "offline" else None)
    skill = SkillService(repo_root=config.repo_root, mode=config.skill_mode, retrieval_backend=retrieval)
    writer = build_report_writer(
        api_key=config.openai_api_key,
        model=config.openai_writer_model,
    )
    return ElectionBotService(
        config=config,
        store=store,
        router=router,
        skill_service=skill,
        writer=writer,
    )


async def run() -> None:
    config = BotConfig.from_env(require_feishu=True)

    # Import lazily so the core bot package and tests remain usable without the
    # Feishu SDK installed in lightweight/offline environments.
    from lark_channel import FeishuChannel

    service = build_service(config)
    channel = FeishuChannel(
        app_id=config.lark_app_id,
        app_secret=config.lark_app_secret,
        require_mention=config.require_mention,
    )

    async def on_message(message: Any) -> None:
        inbound = _inbound(message)
        if not inbound.message_id or not inbound.chat_id:
            LOG.warning("ignored malformed inbound message")
            return
        if config.require_mention and inbound.chat_type in {"group", "topic"} and not inbound.mentioned_bot:
            return
        try:
            ack_key = service.store.resolve_key(inbound)
            if looks_long_running(inbound.text):
                ack = await channel.send(
                    inbound.chat_id,
                    {"markdown": "收到，正在读取最新选情资料并运行结构分析……"},
                    {
                        "reply_to": inbound.message_id,
                        "reply_in_thread": inbound.chat_type in {"group", "topic"},
                        "receive_id_type": "chat_id",
                        "uuid": f"ogasawara-ack-{inbound.message_id}",
                    },
                )
                ack_id = str(getattr(ack, "message_id", "") or "")
                if ack_id:
                    service.link_outbound_message(ack_id, ack_key)

            reply = await service.handle_message(inbound)
            if reply is None:
                return
            result = await channel.send(
                inbound.chat_id,
                {"markdown": reply.text},
                {
                    "reply_to": inbound.message_id,
                    "reply_in_thread": inbound.chat_type in {"group", "topic"},
                    "receive_id_type": "chat_id",
                    "uuid": f"ogasawara-final-{inbound.message_id}",
                },
            )
            result_id = str(getattr(result, "message_id", "") or "")
            if result_id:
                service.link_outbound_message(result_id, reply.conversation_key)
        except Exception:
            LOG.exception("failed to handle Feishu message")
            try:
                await channel.send(
                    inbound.chat_id,
                    {"text": "本次选情分析执行失败，请稍后重试。"},
                    {"reply_to": inbound.message_id, "receive_id_type": "chat_id"},
                )
            except Exception:
                LOG.exception("failed to send error reply")

    def on_error(error: Any) -> None:
        LOG.error("Feishu channel error: %s", error)

    channel.on("message", on_message)
    channel.on("error", on_error)
    LOG.info("starting Feishu election bot (skill mode=%s)", config.skill_mode)
    await channel.connect()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
