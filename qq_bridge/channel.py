"""Claude Code channel notification support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification

logger = logging.getLogger(__name__)


class ChannelPublisher:
    """Publishes inbound QQ messages to the active Claude Code MCP session."""

    def __init__(self) -> None:
        self._session: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    def attach(self, session: Any) -> None:
        self._session = session

    def detach(self, session: Any | None = None) -> None:
        if session is None or self._session is session:
            self._session = None

    async def publish(self, *, content: str, meta: dict[str, str]) -> bool:
        session = self._session
        if session is None:
            logger.warning("cannot publish QQ channel notification: no Claude Code session")
            return False

        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/claude/channel",
            params={"content": content, "meta": meta},
        )
        message = SessionMessage(message=JSONRPCMessage(notification))
        try:
            async with self._lock:
                await session.send_message(message)
            logger.info("published QQ channel notification: chat_id=%s message_id=%s", meta.get("chat_id"), meta.get("message_id"))
            return True
        except Exception:
            logger.exception("failed to publish QQ channel notification")
            self.detach(session)
            return False
