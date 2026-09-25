"""Run the Feishu bot using the official long-connection channel SDK."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from .config import BotConfig
from .conversation import ConversationStore
from .models import InboundMessage
from .report_writer import build_report_writer
from .router import IntentRouter, looks_long_running
from .service import ElectionBotService
from .skill_service import SkillService
from runtime.news_retrieval import build_retrieval_backend


LOG = logging.getLogger("ogasawara.feishu")


def _send_uuid(prefix: str, message_id: str) -> str:
    """Deterministic idempotency key within Feishu's 50-char uuid limit."""
    digest = hashlib.md5(message_id.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


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
    retrieval = build_retrieval_backend(config.retrieval_provider, config.repo_root,
                                        config.skill_mode, config.max_article_fetches)
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


async def run(feishu_channel_cls: Any = None, inbound_config: Any = None) -> None:
    config = BotConfig.from_env(require_feishu=True)

    # Import lazily so the core bot package and tests remain usable without the
    # Feishu SDK installed in lightweight/offline environments.  The class is
    # normally loaded by main() *before* asyncio.run(); importing it while the
    # loop is already running makes the SDK cache the running loop and fail
    # later with "This event loop is already running".
    if feishu_channel_cls is None:
        from lark_channel import FeishuChannel, InboundConfig

        feishu_channel_cls = FeishuChannel
        inbound_config = inbound_config or InboundConfig(emit_raw_events=True)

    service = build_service(config)
    channel = feishu_channel_cls(
        app_id=config.lark_app_id,
        app_secret=config.lark_app_secret,
        inbound=inbound_config,
    )

    async def on_raw(data: Any) -> None:
        LOG.info("feishu raw event: %r", str(data)[:800])

    channel.on("raw", on_raw)

    async def on_raw_message_event(payload: Any) -> None:
        LOG.info("feishu unwrapped im.message.receive_v1: %r", str(payload)[:1200])

    try:
        channel.on_raw_event("im.message.receive_v1", on_raw_message_event)
    except Exception:
        LOG.exception("failed to install unwrapped message-event debug hook")

    async def on_message(message: Any) -> None:
        inbound = _inbound(message)
        LOG.info(
            "feishu inbound event: id=%s chat=%s type=%s mentioned_bot=%s text=%r",
            inbound.message_id,
            inbound.chat_id,
            inbound.chat_type,
            inbound.mentioned_bot,
            (inbound.text or "")[:120],
        )
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
                        "uuid": _send_uuid("ogasawara-ack", inbound.message_id),
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
                    "uuid": _send_uuid("ogasawara-final", inbound.message_id),
                },
            )
            if not result.success:
                LOG.warning(
                    "final reply send failed (%s); retrying as plain text",
                    result.error,
                )
                result = await channel.send(
                    inbound.chat_id,
                    {"text": reply.text},
                    {
                        "reply_to": inbound.message_id,
                        "receive_id_type": "chat_id",
                        "uuid": _send_uuid("ogasawara-final-text", inbound.message_id),
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
    # Load the SDK synchronously so its websocket client captures a loop that
    # is not currently running; then start the asyncio entry point.
    from lark_channel import FeishuChannel, InboundConfig

    asyncio.run(run(FeishuChannel, InboundConfig(emit_raw_events=True)))


if __name__ == "__main__":
    main()
