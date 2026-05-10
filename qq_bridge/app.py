"""Application orchestration for the QQ channel bridge."""

from __future__ import annotations

import asyncio
import logging
import sys

from . import storage
from .channel import ChannelPublisher
from .config import Settings
from .qq_bot import IncomingMessage, QQBotService


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(settings.log_full_path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
        force=True,
    )


def validate_or_exit(settings: Settings) -> None:
    missing = settings.validate()
    if missing:
        raise SystemExit(f"Missing config: {', '.join(missing)}")


def channel_meta(message: IncomingMessage) -> dict[str, str]:
    meta = {
        "chat_id": message.chat_id,
        "chat_type": message.chat_type,
        "message_id": message.message_id,
        "user_id": message.author_id,
        "internal_id": str(message.id),
    }
    if message.author_name:
        meta["user"] = message.author_name
    if message.group_openid:
        meta["group_openid"] = message.group_openid
    return meta


class BridgeRuntime:
    def __init__(self, settings: Settings, publisher: ChannelPublisher) -> None:
        self.settings = settings
        self.publisher = publisher
        self.bot = QQBotService(settings, on_message=self.handle_message)

    async def handle_message(self, message: IncomingMessage) -> None:
        await self._handle_channel_message(message)

    async def _handle_channel_message(self, message: IncomingMessage) -> None:
        if not self.publisher or not self.publisher.is_connected:
            storage.update_message_status(message.id, "failed", error_info="Claude Code channel is offline")
            await self.bot.send_text(
                message.chat_id,
                self.settings.channel_offline_reply,
                reply_to=message.message_id,
                source_message_id=message.id,
                message_format="text",
            )
            return

        storage.set_active_chat(
            chat_id=message.chat_id,
            reply_msg_id=message.message_id,
            source_message_id=message.id,
        )
        delivered = await self.publisher.publish(content=message.content, meta=channel_meta(message))
        if delivered:
            storage.update_message_status(message.id, "delivered")
            if self.settings.progress_enabled and self.settings.progress_ack_enabled:
                await self.bot.send_text(
                    message.chat_id,
                    "已收到，正在交给 Claude Code 处理。后续执行步骤会同步到这里。",
                    reply_to=message.message_id,
                    source_message_id=message.id,
                    message_format="text",
                )
        else:
            storage.clear_active_chat()
            storage.update_message_status(message.id, "failed", error_info="failed to publish channel notification")
            await self.bot.send_text(
                message.chat_id,
                self.settings.channel_offline_reply,
                reply_to=message.message_id,
                source_message_id=message.id,
                message_format="text",
            )

    async def start_bot(self) -> asyncio.Task[None]:
        return asyncio.create_task(self.bot.start(), name="qq-bot")

    async def stop(self) -> None:
        await self.bot.stop()
