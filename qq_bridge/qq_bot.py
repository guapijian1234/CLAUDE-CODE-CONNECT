"""QQ Bot WebSocket 客户端 — 接收消息存 DB，轮询发件箱发送回复"""

import asyncio
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
    """QQ 机器人 — 只负责收发"""

    async def on_ready(self):
        global _access_token
        self._capture_token()
        logger.info("Bot ready, token=%s", "OK" if _access_token else "MISSING")
        asyncio.create_task(self._poll_outbox())

    def _capture_token(self):
        global _access_token
        if _access_token:
            return
        try:
            http = getattr(self, 'http', None)
            if http:
                tok = getattr(http, '_token', None)
                if tok:
                    _access_token = getattr(tok, 'access_token', None)
        except Exception as e:
            logger.error("Token capture failed: %s", e)

    async def on_group_at_message_create(self, message: GroupMessage):
        content = self._strip_mentions(message)
        if not content:
            return
        logger.info("Group@ msg: %s", content[:50])
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
        self._capture_token()
        content = getattr(message, 'content', '') or ''
        if not content.strip():
            return
        logger.info("C2C msg: %s", content[:50])
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
                    # Mark as sending FIRST to prevent duplicate sends
                    storage.mark_outbox_sending(item['id'])
                    await self._send_item(item)
            except Exception as e:
                logger.error("Outbox poll error: %s", e)
            await asyncio.sleep(1)

    async def _send_item(self, item: dict):
        token = _access_token
        if not token:
            logger.error("No access token — cannot send outbox #%d", item['id'])
            storage.mark_outbox_failed(item['id'], "No access token")
            return

        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }

        if item['chat_type'] == 'group':
            url = f"{API_BASE}/v2/groups/{item['target_id']}/messages"
        else:
            url = f"{API_BASE}/v2/users/{item['target_id']}/messages"

        is_md = any(marker in item['content'] for marker in ['#', '**', '```', '|', '- ', '> '])
        if is_md:
            payload = {"msg_type": 2, "markdown": {"content": item['content']}}
        else:
            payload = {"content": item['content'], "msg_type": 0}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        logger.error("Send #%d failed %d: %s", item['id'], resp.status, body[:200])
                        storage.mark_outbox_failed(item['id'], f"HTTP {resp.status}: {body[:200]}")
                    else:
                        storage.mark_outbox_sent(item['id'])
                        logger.info("Sent #%d OK (%d chars)", item['id'], len(item['content']))
        except Exception as e:
            logger.error("Send #%d error: %s", item['id'], e)
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
    global _bot_running, _bot_error, _access_token
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    settings = get_settings()
    logger.info("Starting QQ Bot app_id=%s...",
                settings.bot_app_id[:6] + "..." if settings.bot_app_id else "(empty)")
    try:
        intents = botpy.Intents(public_messages=True)
        _bot_running = True
        loop.run_until_complete(
            QQBotClient(intents=intents, timeout=30).start(
                appid=settings.bot_app_id, secret=settings.bot_app_secret
            )
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
    return {"running": _bot_running, "error": _bot_error}
