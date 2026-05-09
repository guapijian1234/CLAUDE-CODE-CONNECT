"""QQ Bot — WebSocket 接收 + HTTP 发送，单一线程运行"""
import asyncio
import logging
import threading
import time

import aiohttp
import botpy
from botpy.message import GroupMessage, C2CMessage

from .config import get_settings
from . import storage

logger = logging.getLogger("qq_bridge.bot")
API_BASE = "https://api.sgroup.qq.com"

_state = {"running": False, "error": None, "token": None}


class QQBotClient(botpy.Client):
    """QQ 机器人客户端"""

    async def on_ready(self):
        self._capture_token()
        logger.info("Bot ready, token=%s", "OK" if _state["token"] else "MISSING")
        asyncio.create_task(self._poll_outbox())

    def _capture_token(self):
        if _state["token"]:
            return
        try:
            http = getattr(self, "http", None)
            if http:
                tok = getattr(http, "_token", None)
                if tok:
                    _state["token"] = getattr(tok, "access_token", None)
        except Exception as e:
            logger.error("Token error: %s", e)

    async def on_c2c_message_create(self, message: C2CMessage):
        self._capture_token()
        content = (getattr(message, "content", "") or "").strip()
        if not content:
            return
        logger.info("QQ: %s", content[:60])
        storage.insert_message(
            message_id=message.id, content=content, raw_content=content,
            author_id=message.author.user_openid, author_name=None, chat_type="c2c",
        )

    async def on_group_at_message_create(self, message: GroupMessage):
        content = self._strip_mentions(message)
        if not content:
            return
        logger.info("QQ群: %s", content[:60])
        storage.insert_message(
            message_id=message.id, content=content,
            raw_content=getattr(message, "content", "") or "",
            author_id=message.author.member_openid, author_name=None,
            chat_type="group", group_openid=message.group_openid,
        )

    async def _poll_outbox(self):
        logger.info("Outbox poller started")
        while True:
            try:
                for item in storage.get_pending_outbox(5):
                    storage.mark_outbox_sending(item["id"])
                    await self._send_item(item)
            except Exception as e:
                logger.error("Poller: %s", e)
            await asyncio.sleep(1)

    async def _send_item(self, item: dict):
        token = _state["token"]
        if not token:
            storage.mark_outbox_failed(item["id"], "No token")
            return
        headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
        url = f"{API_BASE}/v2/{'groups' if item['chat_type'] == 'group' else 'users'}/{item['target_id']}/messages"
        is_md = any(m in item["content"] for m in ["#", "**", "```", "| ", "- ", "> "])
        payload = {"msg_type": 2, "markdown": {"content": item["content"]}} if is_md else {"content": item["content"], "msg_type": 0}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, headers=headers, json=payload) as r:
                    if r.status == 200:
                        storage.mark_outbox_sent(item["id"])
                        logger.info("Sent #%d OK", item["id"])
                    else:
                        body = await r.text()
                        storage.mark_outbox_failed(item["id"], f"HTTP {r.status}: {body[:200]}")
                        logger.error("Send #%d fail %d: %s", item["id"], r.status, body[:200])
        except Exception as e:
            storage.mark_outbox_failed(item["id"], str(e))
            logger.error("Send #%d: %s", item["id"], e)

    def _strip_mentions(self, message):
        content = getattr(message, "content", "") or ""
        for m in getattr(message, "mentions", None) or []:
            uid = getattr(m, "id", "") or getattr(m, "user_id", "") or ""
            if uid:
                content = content.replace(f"<@!{uid}>", "").replace(f"<@{uid}>", "")
        return content.strip()


def _run_bot():
    settings = get_settings()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("Bot starting app=%s...", settings.bot_app_id[:6])
    try:
        _state["running"] = True
        loop.run_until_complete(
            QQBotClient(intents=botpy.Intents(public_messages=True), timeout=30).start(
                appid=settings.bot_app_id, secret=settings.bot_app_secret
            )
        )
    except Exception as e:
        _state["error"] = str(e)
        _state["running"] = False
        logger.error("Bot died: %s", e)
    finally:
        _state["running"] = False


def start_bot():
    thread = threading.Thread(target=_run_bot, daemon=True, name="qq-bot")
    thread.start()
    return thread


def get_status():
    return dict(_state)
