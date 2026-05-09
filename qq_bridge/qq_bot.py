"""QQ Bot WebSocket 客户端 — 接收消息、轮询发件箱发送回复"""

import asyncio
import json
import logging
import threading

import aiohttp
import botpy
from botpy.message import GroupMessage, C2CMessage

from .config import get_settings
from . import storage

logger = logging.getLogger("qq_bridge.bot")

API_BASE = "https://api.sgroup.qq.com"
_bot_running = False
_bot_error: str | None = None
_access_token: str | None = None


class QQBotClient(botpy.Client):
    """QQ 机器人客户端，在后台线程中运行"""

    async def on_ready(self):
        global _access_token
        _access_token = self.access_token
        logger.info("QQ Bot connected and ready")
        asyncio.create_task(self._poll_outbox())

    async def on_group_at_message_create(self, message: GroupMessage):
        content = self._strip_mentions(message)
        if not content:
            return
        logger.info(
            "Received group@ msg id=%s author=%s content=%s",
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
            "Received c2c msg id=%s author=%s content=%s",
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
            await _send_qq_message(
                chat_type=item['chat_type'],
                target_id=item['target_id'],
                content=item['content'],
                reply_msg_id=item.get('reply_msg_id'),
            )
            storage.mark_outbox_sent(item['id'])
            logger.info("Sent outbox #%d -> %s:%s", item['id'], item['chat_type'], item['target_id'])
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


async def _send_qq_message(*, chat_type: str, target_id: str, content: str,
                           reply_msg_id: str | None = None):
    """直接通过 HTTP API 发送 QQ 消息，绕过 botpy 的 payload 处理"""
    token = _access_token
    if not token:
        raise RuntimeError("Bot not connected, no access token")

    headers = {
        "Authorization": f"QQBot {token}",
        "Content-Type": "application/json",
    }

    if chat_type == 'group':
        url = f"{API_BASE}/v2/groups/{target_id}/messages"
    else:
        url = f"{API_BASE}/v2/users/{target_id}/messages"

    payload = {
        "content": content,
        "msg_type": 0,
    }
    if reply_msg_id:
        payload["msg_id"] = reply_msg_id

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: {body}")

    logger.info("QQ API send OK: %s:%s content_len=%d", chat_type, target_id, len(content))


def _run_bot():
    global _bot_running, _bot_error, _access_token
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
        _access_token = None
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
