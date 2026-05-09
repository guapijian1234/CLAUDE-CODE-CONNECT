"""QQ Bot WebSocket 客户端 — 收到消息立即调 AI 实时回复"""

import asyncio
import logging
import threading

import aiohttp
import botpy
from botpy.message import GroupMessage, C2CMessage

from .config import get_settings
from . import storage
from . import ai_client

logger = logging.getLogger("qq_bridge.bot")

API_BASE = "https://api.sgroup.qq.com"
_bot_running = False
_bot_error: str | None = None
_access_token: str | None = None


class QQBotClient(botpy.Client):
    """QQ 机器人客户端，收到消息立即调 AI 并回复"""

    async def on_ready(self):
        global _access_token
        self._capture_token()
        logger.info("Bot ready, token=%s", "OK" if _access_token else "MISSING")

    def _capture_token(self):
        global _access_token
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
        await self._handle_message(
            msg_id=message.id,
            content=content,
            chat_type='group',
            target_id=message.group_openid,
        )

    async def on_c2c_message_create(self, message: C2CMessage):
        content = getattr(message, 'content', '') or ''
        if not content.strip():
            return
        logger.info("C2C msg: %s", content[:50])
        self._capture_token()
        await self._handle_message(
            msg_id=message.id,
            content=content.strip(),
            chat_type='c2c',
            target_id=message.author.user_openid,
        )

    async def _handle_message(self, *, msg_id: str, content: str,
                              chat_type: str, target_id: str):
        """收到消息 → 调 AI → 立即回复"""
        # Store the message
        storage.insert_message(
            message_id=msg_id,
            content=content,
            raw_content=content,
            author_id=target_id,
            author_name=None,
            chat_type=chat_type,
        )

        # Call AI API for real-time response
        logger.info("Calling AI for: %s", content[:80])
        reply = await ai_client.chat(content)
        logger.info("AI reply: %s", reply[:80])

        if not reply or reply.startswith("[AI"):
            logger.error("AI returned error: %s", reply)
            return

        # Send reply to QQ immediately
        await self._send_reply(chat_type=chat_type, target_id=target_id,
                               content=reply, reply_msg_id=msg_id)

    async def _send_reply(self, *, chat_type: str, target_id: str,
                          content: str, reply_msg_id: str):
        """直接通过 HTTP API 发送 QQ 消息"""
        token = _access_token
        if not token:
            logger.error("No access token, cannot send")
            return

        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }

        if chat_type == 'group':
            url = f"{API_BASE}/v2/groups/{target_id}/messages"
        else:
            url = f"{API_BASE}/v2/users/{target_id}/messages"

        payload = {"content": content, "msg_type": 0}
        if reply_msg_id:
            payload["msg_id"] = reply_msg_id

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        logger.error("QQ send failed %d: %s", resp.status, body[:200])
                    else:
                        logger.info("QQ reply sent OK (%d chars)", len(content))
        except Exception as e:
            logger.error("QQ send error: %s", e)

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
    logger.info("AI API: %s model=%s", ai_client.API_BASE, ai_client.MODEL)
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
    return {"running": _bot_running, "error": _bot_error}
