"""配置管理 — 从 .env 文件和环境变量加载设置"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """QQ Bridge 配置，从 .env 文件或环境变量加载"""

    bot_app_id: str = ""
    bot_app_secret: str = ""
    db_path: str = "data/bridge.db"
    log_level: str = "INFO"

    model_config = {
        "env_prefix": "QQ_BRIDGE_",
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def db_full_path(self) -> Path:
        p = Path(self.db_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def validate(self) -> list[str]:
        """验证必要配置项，返回缺失项列表"""
        missing = []
        if not self.bot_app_id:
            missing.append("QQ_BRIDGE_BOT_APP_ID")
        if not self.bot_app_secret:
            missing.append("QQ_BRIDGE_BOT_APP_SECRET")
        return missing


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
