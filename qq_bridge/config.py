"""Runtime configuration for the QQ bridge."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _split_csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


class Settings(BaseSettings):
    bot_app_id: str = ""
    bot_app_secret: str = ""

    db_path: str = "data/bridge.db"
    log_level: str = "INFO"
    log_path: str = "logs/qq_bridge.log"

    allowed_users: str = ""
    allowed_groups: str = ""
    message_chunk_size: int = Field(default=1800, ge=200, le=4000)
    markdown_enabled: bool = True
    markdown_fallback_to_text: bool = True
    progress_enabled: bool = True
    progress_ack_enabled: bool = True
    progress_active_ttl_seconds: int = Field(default=7200, ge=60, le=86400)
    progress_max_length: int = Field(default=500, ge=120, le=2000)
    progress_reply_to_source: bool = False
    channel_offline_reply: str = (
        "Claude Code channel is not connected. Start the existing Claude Code session "
        "with the QQ channel plugin enabled."
    )

    model_config = {
        "env_prefix": "QQ_BRIDGE_",
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def db_full_path(self) -> Path:
        path = Path(self.db_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def log_full_path(self) -> Path:
        path = Path(self.log_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def allowed_user_ids(self) -> set[str]:
        return _split_csv(self.allowed_users)

    @property
    def allowed_group_ids(self) -> set[str]:
        return _split_csv(self.allowed_groups)

    def validate(self) -> list[str]:
        missing: list[str] = []
        if not self.bot_app_id:
            missing.append("QQ_BRIDGE_BOT_APP_ID")
        if not self.bot_app_secret:
            missing.append("QQ_BRIDGE_BOT_APP_SECRET")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
