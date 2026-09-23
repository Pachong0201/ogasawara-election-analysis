"""Environment-backed bot configuration.

Secrets are never read from repository files. Feishu and OpenAI credentials
must be supplied through environment variables or the deployment secret store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BotConfig:
    repo_root: Path
    lark_app_id: str = ""
    lark_app_secret: str = ""
    openai_api_key: str = ""
    openai_router_model: str = "gpt-5.6-luna"
    openai_writer_model: str = "gpt-5.6-sol"
    skill_mode: str = "online"
    retrieval_provider: str = "gdelt"
    max_article_fetches: int = 6
    require_mention: bool = True
    conversation_db: Path = Path("cache/bot/conversations.sqlite3")
    default_target_year: int = 2026

    @classmethod
    def from_env(cls, require_feishu: bool = False) -> "BotConfig":
        repo_root = Path(
            os.getenv("OGASAWARA_REPO_ROOT") or Path(__file__).resolve().parents[1]
        ).resolve()
        db_raw = os.getenv("FEISHU_BOT_CONVERSATION_DB", "cache/bot/conversations.sqlite3")
        db_path = Path(db_raw)
        if not db_path.is_absolute():
            db_path = repo_root / db_path

        config = cls(
            repo_root=repo_root,
            lark_app_id=os.getenv("LARK_APP_ID", "").strip(),
            lark_app_secret=os.getenv("LARK_APP_SECRET", "").strip(),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_router_model=os.getenv("OPENAI_ROUTER_MODEL", "gpt-5.6-luna").strip(),
            openai_writer_model=os.getenv("OPENAI_WRITER_MODEL", "gpt-5.6-sol").strip(),
            skill_mode=os.getenv("OGASAWARA_BOT_MODE", "online").strip().lower(),
            retrieval_provider=os.getenv("OGASAWARA_BOT_RETRIEVAL", "gdelt").strip().lower(),
            max_article_fetches=int(os.getenv("OGASAWARA_MAX_ARTICLE_FETCHES", "6")),
            require_mention=os.getenv("FEISHU_REQUIRE_MENTION", "true").strip().lower()
            not in {"0", "false", "no", "off"},
            conversation_db=db_path,
            default_target_year=int(os.getenv("OGASAWARA_TARGET_YEAR", "2026")),
        )
        if config.skill_mode not in {"online", "offline", "auto"}:
            raise ValueError("OGASAWARA_BOT_MODE must be online, offline, or auto")
        if config.retrieval_provider not in {"gdelt", "disabled"}:
            raise ValueError("OGASAWARA_BOT_RETRIEVAL must be gdelt or disabled")
        if not 0 <= config.max_article_fetches <= 12:
            raise ValueError("OGASAWARA_MAX_ARTICLE_FETCHES must be between 0 and 12")
        if require_feishu and (not config.lark_app_id or not config.lark_app_secret):
            raise RuntimeError("LARK_APP_ID and LARK_APP_SECRET are required")
        return config
