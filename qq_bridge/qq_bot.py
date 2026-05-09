"""QQ Bot WebSocket 客户端 — 接收消息、轮询发件箱发送回复"""

import asyncio
import logging
import threading
import uuid
from pathlib import Path

import botpy
from botpy.message import GroupMessage, C2CMessage

from .config import get_settings, PROJECT_ROOT
from . import storage

logger = logging.getLogger("qq_bridge.bot")

_bot_running = False
_bot_error: str | None = None


class QQBotClient(botpy.Client):
    """QQ 机器人客户端，在后台线程中运行"""

    async def on_ready(self):
        logger.info("QQ Bot connected and ready")
        asyncio.create_task(self._poll_outbox())

    async def on_group_at_message_create(self, message: GroupMessage):
        content = self._strip_mentions(message)
        if not content:
            return
        logger.info(
            "Received group message id=%s author=%s content=%s",
            message.id, message.author.member_openid, content[:50]
        )
        storage.insert_message(
            message_id=message.id,
            content=content,
            raw_content=getattr(message, 'content', '') or '',
            author_id=message.author.member_openid,
            author_name=None,
            chat_type='group',
            group_openid=message.group_openid,
        )

    async def on_c2c_message_create(self, message: C2CMessage):
        content = getattr(message, 'content', '') or ''
        if not content.strip():
            return
        logger.info(
            "Received c2c message id=%s author=%s content=%s",
            message.id, message.author.user_openid, content[:50]
        )
        storage.insert_message(
            message_id=message.id,
            content=content.strip(),
            raw_content=content,
            author_id=message.author.user_openid,
            author_name=None,
            chat_type='c2c',
        )

    async def _poll_outbox(self):
        logger.info("Outbox poller started")
        while True:
            try:
                items = storage.get_pending_outbox(limit=5)
                for item in items:
                    await self._send_outbox_item(item)
            except Exception as e:
                logger.error("Outbox poll error: %s", e)
            await asyncio.sleep(1)

    async def _send_outbox_item(self, item: dict):
        try:
            msg_id = str(uuid.uuid4())
            if item['chat_type'] == 'group':
                await self.api.post_group_message(
                    group_openid=item['target_id'],
                    content=item['content'],
                    msg_id=msg_id,
                )
            elif item['chat_type'] == 'c2c':
                await self.api.post_c2c_message(
                    openid=item['target_id'],
                    content=item['content'],
                    msg_id=msg_id,
                )
            storage.mark_outbox_sent(item['id'])
            logger.info("Sent outbox #%d to %s:%s", item['id'], item['chat_type'], item['target_id'])
        except Exception as e:
            logger.error("Failed to send outbox #%d: %s", item['id'], e)
            storage.mark_outbox_failed(item['id'], str(e))

    def _strip_mentions(self, message: GroupMessage) -> str:
        content = getattr(message, 'content', '') or ''
        mentions = getattr(message, 'mentions', None)
        if mentions:
            for m in mentions:
                uid = getattr(m, 'id', '') or getattr(m, 'user_id', '') or ''
                if uid:
                    content = content.replace(f"<@!{uid}>", "").replace(f"<@{uid}>", "")
        return content.strip()


def _run_bot():
    global _bot_running, _bot_error
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    settings = get_settings()
    logger.info(
        "Starting QQ Bot with app_id=%s...",
        settings.bot_app_id[:6] + "..." if settings.bot_app_id else "(empty)"
    )
    try:
        intents = botpy.Intents(public_messages=True)
        client = QQBotClient(intents=intents, timeout=30)
        _bot_running = True
        loop.run_until_complete(
            client.start(appid=settings.bot_app_id, secret=settings.bot_app_secret)
        )
    except Exception as e:
        _bot_error = str(e)
        _bot_running = False
        logger.error("Bot failed: %s", e)


def start_bot_thread() -> threading.Thread:
    thread = threading.Thread(target=_run_bot, daemon=True, name="qq-bot-thread")
    thread.start()
    return thread


def get_bot_status() -> dict:
    return {
        "running": _bot_running,
        "error": _bot_error,
    }
