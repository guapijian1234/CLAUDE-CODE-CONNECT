"""QQ bot transport built on Tencent botpy."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import botpy
from botpy.message import C2CMessage, GroupMessage

from . import storage
from .config import Settings
from .text import split_text, truncate

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IncomingMessage:
    id: int
    message_id: str
    content: str
    raw_content: str
    author_id: str
    author_name: str | None
    chat_type: str
    chat_id: str
    group_openid: str | None = None


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class QQBotClient(botpy.Client):
    def __init__(self, service: "QQBotService", **kwargs: Any) -> None:
        self.service = service
        super().__init__(**kwargs)

    async def on_ready(self) -> None:
        await self.service.on_ready(self)

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        await self.service.handle_c2c_message(message)

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        await self.service.handle_group_message(message)

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        logger.exception("QQ bot event failed: %s", event_method)


class QQBotService:
    def __init__(self, settings: Settings, on_message: MessageHandler | None = None) -> None:
        self.settings = settings
        self.on_message = on_message
        self.client: QQBotClient | None = None
        self._ready = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._outbox_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._state: dict[str, Any] = {
            "running": False,
            "ready": False,
            "error": None,
            "mode": "channel",
        }

    def status(self) -> dict[str, Any]:
        return dict(self._state)

    async def start(self) -> None:
        self.client = QQBotClient(
            service=self,
            intents=botpy.Intents(public_messages=True),
            timeout=30,
            bot_log=False,
        )
        self._outbox_task = asyncio.create_task(self._outbox_loop(), name="qq-outbox")
        self._state.update({"running": True, "error": None})
        try:
            logger.info("starting QQ bot app=%s...", self.settings.bot_app_id[:6])
            await self.client.start(appid=self.settings.bot_app_id, secret=self.settings.bot_app_secret)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._state["error"] = str(exc)
            logger.exception("QQ bot stopped with an error")
        finally:
            self._state["running"] = False
            self._state["ready"] = False
            self._ready.clear()

    async def stop(self) -> None:
        self._stopping = True
        if self._outbox_task:
            self._outbox_task.cancel()
        if self.client:
            await self.client.close()

    async def on_ready(self, client: QQBotClient) -> None:
        self.client = client
        self._state["ready"] = True
        self._ready.set()
        logger.info("QQ bot is ready")
        await self.flush_outbox_once()

    async def handle_c2c_message(self, message: C2CMessage) -> None:
        content = (getattr(message, "content", "") or "").strip()
        if not content:
            return
        author_id = _first_attr(message.author, "user_openid", "id", default="")
        incoming = self._save_message(
            message=message,
            content=content,
            raw_content=content,
            chat_type="c2c",
            author_id=author_id,
            group_openid=None,
        )
        await self._dispatch(incoming)

    async def handle_group_message(self, message: GroupMessage) -> None:
        content = self._strip_mentions(message).strip()
        if not content:
            return
        author_id = _first_attr(message.author, "member_openid", "user_openid", "id", default="")
        group_openid = getattr(message, "group_openid", None)
        incoming = self._save_message(
            message=message,
            content=content,
            raw_content=getattr(message, "content", "") or "",
            chat_type="group",
            author_id=author_id,
            group_openid=group_openid,
        )
        await self._dispatch(incoming)

    async def _dispatch(self, incoming: IncomingMessage) -> None:
        logger.info("QQ inbound %s #%s: %s", incoming.chat_id, incoming.id, truncate(incoming.content, 80))

        command_reply = self._handle_builtin_command(incoming, public_only=True)
        if command_reply is not None:
            await self.send_text(incoming.chat_id, command_reply, reply_to=incoming.message_id, message_format="text")
            storage.update_message_status(incoming.id, "processed", command_reply)
            return

        if not self._is_allowed(incoming):
            text = (
                "This QQ sender is not allowed. Send /id to get identifiers for "
                "QQ_BRIDGE_ALLOWED_USERS or QQ_BRIDGE_ALLOWED_GROUPS."
            )
            await self.send_text(incoming.chat_id, text, reply_to=incoming.message_id, message_format="text")
            storage.update_message_status(incoming.id, "rejected", text)
            return

        command_reply = self._handle_builtin_command(incoming)
        if command_reply is not None:
            await self.send_text(incoming.chat_id, command_reply, reply_to=incoming.message_id, message_format="text")
            storage.update_message_status(incoming.id, "processed", command_reply)
            return

        if self.on_message is None:
            storage.update_message_status(incoming.id, "pending")
            return

        try:
            await self.on_message(incoming)
        except Exception as exc:
            logger.exception("inbound message handler failed")
            storage.update_message_status(incoming.id, "failed", error_info=str(exc))
            await self.send_text(
                incoming.chat_id,
                f"Bridge error: {truncate(str(exc), 600)}",
                reply_to=incoming.message_id,
                message_format="text",
            )

    def _save_message(
        self,
        *,
        message: C2CMessage | GroupMessage,
        content: str,
        raw_content: str,
        chat_type: str,
        author_id: str,
        group_openid: str | None,
    ) -> IncomingMessage:
        chat_id = f"{chat_type}:{group_openid if chat_type == 'group' else author_id}"
        message_id = str(getattr(message, "id", ""))
        author_name = _first_attr(message.author, "username", "name", "nick", default=None)
        internal_id = storage.insert_message(
            message_id=message_id,
            content=content,
            raw_content=raw_content,
            author_id=author_id,
            author_name=author_name,
            chat_type=chat_type,
            chat_id=chat_id,
            group_openid=group_openid,
        )
        return IncomingMessage(
            id=internal_id,
            message_id=message_id,
            content=content,
            raw_content=raw_content,
            author_id=author_id,
            author_name=author_name,
            chat_type=chat_type,
            chat_id=chat_id,
            group_openid=group_openid,
        )

    def _handle_builtin_command(self, incoming: IncomingMessage, public_only: bool = False) -> str | None:
        command = incoming.content.strip()
        lowered = command.lower()
        if lowered in {"/id", "id"}:
            lines = [
                f"chat_id={incoming.chat_id}",
                f"user_openid={incoming.author_id}",
            ]
            if incoming.group_openid:
                lines.append(f"group_openid={incoming.group_openid}")
            return "\n".join(lines)
        if public_only:
            return None
        if lowered in {"/status", "status"}:
            stats = storage.get_stats()
            state = self.status()
            return (
                f"mode=channel ready={state['ready']} running={state['running']}\n"
                f"messages pending={stats['pending_messages']} delivered={stats['delivered_messages']} "
                f"failed={stats['failed_messages']}\n"
                f"outbox pending={stats['pending_outbox']} failed={stats['failed_outbox']}"
            )
        if lowered in {"/help", "help"}:
            return (
                "QQ Bridge commands:\n"
                "/id - show identifiers for allowlist config\n"
                "/status - show bridge status"
            )
        return None

    def _is_allowed(self, incoming: IncomingMessage) -> bool:
        allowed_users = self.settings.allowed_user_ids
        allowed_groups = self.settings.allowed_group_ids
        if allowed_users and incoming.author_id not in allowed_users:
            return False
        if incoming.chat_type == "group" and allowed_groups and incoming.group_openid not in allowed_groups:
            return False
        return True

    async def send_text(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to: str | None = None,
        source_message_id: int | None = None,
        message_format: str | None = None,
    ) -> list[int]:
        chat_type, target_id = parse_chat_id(chat_id)
        if message_format is None:
            message_format = "markdown" if self.settings.markdown_enabled else "text"
        if message_format not in {"text", "markdown"}:
            raise ValueError("message_format must be text or markdown")

        outbox_ids: list[int] = []
        for index, chunk in enumerate(split_text(text, self.settings.message_chunk_size)):
            outbox_ids.append(
                storage.insert_outbox(
                    chat_type=chat_type,
                    target_id=target_id,
                    content=chunk,
                    message_format=message_format,
                    reply_msg_id=reply_to if index == 0 else None,
                    source_message_id=source_message_id,
                )
            )
        await self.flush_outbox_once()
        return outbox_ids

    async def flush_outbox_once(self) -> None:
        if not self._ready.is_set() or not self.client:
            return
        async with self._send_lock:
            for item in storage.get_pending_outbox(limit=10):
                if not storage.mark_outbox_sending(int(item["id"])):
                    continue
                try:
                    remote_id = await self._send_item(item)
                except Exception as exc:
                    storage.mark_outbox_failed(int(item["id"]), str(exc))
                    logger.exception("failed to send outbox #%s", item["id"])
                else:
                    storage.mark_outbox_sent(int(item["id"]), remote_id)

    async def _send_item(self, item: dict[str, Any]) -> str | None:
        if not self.client:
            raise RuntimeError("QQ client not ready")

        message_format = item.get("message_format") or "text"
        if message_format == "markdown":
            try:
                return await self._post_message(item, msg_type=2, markdown={"content": item["content"]})
            except Exception:
                if not self.settings.markdown_fallback_to_text:
                    raise
                logger.warning("failed to send markdown outbox #%s; falling back to text", item["id"], exc_info=True)
                return await self._post_message(item, msg_type=0, content=item["content"])

        return await self._post_message(item, msg_type=0, content=item["content"])

    async def _post_message(self, item: dict[str, Any], **kwargs: Any) -> str | None:
        if not self.client:
            raise RuntimeError("QQ client not ready")

        if item.get("reply_msg_id"):
            kwargs["msg_id"] = item["reply_msg_id"]
        if item["chat_type"] == "group":
            kwargs["msg_seq"] = int(item["id"])
            result = await self.client.api.post_group_message(
                group_openid=item["target_id"],
                **kwargs,
            )
        else:
            kwargs["msg_seq"] = str(item["id"])
            result = await self.client.api.post_c2c_message(
                openid=item["target_id"],
                **kwargs,
            )
        return str(getattr(result, "id", "")) or None

    async def _outbox_loop(self) -> None:
        while not self._stopping:
            try:
                await self.flush_outbox_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("outbox loop failed")
            await asyncio.sleep(1)

    def _strip_mentions(self, message: GroupMessage) -> str:
        content = getattr(message, "content", "") or ""
        for mention in getattr(message, "mentions", None) or []:
            uid = _first_attr(mention, "id", "user_id", default="")
            if uid:
                content = content.replace(f"<@!{uid}>", "").replace(f"<@{uid}>", "")
        return content


def parse_chat_id(chat_id: str) -> tuple[str, str]:
    chat_type, sep, target_id = chat_id.partition(":")
    if sep != ":" or chat_type not in {"c2c", "group"} or not target_id:
        raise ValueError("chat_id must be c2c:<openid> or group:<openid>")
    return chat_type, target_id


def _first_attr(obj: Any, *names: str, default: Any = "") -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value:
            return value
    return default
