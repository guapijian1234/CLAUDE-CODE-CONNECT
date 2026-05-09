"""MCP 服务器 — 提供 QQ 消息收发工具"""
from mcp.server.fastmcp import FastMCP
from . import storage
from .config import get_settings
from .qq_bot import start_bot, get_status

mcp = FastMCP("qq-bridge")


@mcp.tool()
async def qq_check(limit: int = 5) -> str:
    """检查 QQ 待处理消息。返回消息列表，含 ID、内容、来源。"""
    msgs = storage.get_pending_messages(limit)
    if not msgs:
        return "暂无待处理 QQ 消息。"
    lines = []
    for m in msgs:
        src = "群聊" if m["chat_type"] == "group" else "私聊"
        lines.append(f"[{m['id']}] {src} | {m['created_at']}\n  {m['content']}")
    return "\n\n".join(lines)


@mcp.tool()
async def qq_reply(msg_id: int, content: str) -> str:
    """回复 QQ 消息。msg_id 是 qq_check 返回的消息 ID，content 是回复内容。"""
    msg = storage.get_message_by_id(msg_id)
    if not msg:
        return f"消息 {msg_id} 不存在"
    target = msg["group_openid"] if msg["chat_type"] == "group" else msg["author_id"]
    oid = storage.insert_outbox(chat_type=msg["chat_type"], target_id=target, content=content)
    storage.update_message_status(msg_id, "processed", content)
    return f"已回复 [ID:{msg_id}] → outbox #{oid}"


@mcp.tool()
async def qq_status() -> str:
    """QQ Bridge 状态：Bot 连接、消息队列。"""
    s = get_status()
    st = storage.get_stats()
    return (
        f"Bot: {'在线' if s['running'] else '离线'}"
        + (f" ({s['error']})" if s.get("error") else "")
        + f" | 待处理: {st['pending_messages']} | 待发送: {st['pending_outbox']} | 累计: {st['total_messages']}"
    )


@mcp.tool()
async def qq_send(target: str, content: str) -> str:
    """直接向 QQ 发送消息。target: 'c2c:OPENID' 或 'group:OPENID'。"""
    parts = target.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("c2c", "group"):
        return "格式: c2c:OPENID 或 group:OPENID"
    oid = storage.insert_outbox(chat_type=parts[0], target_id=parts[1], content=content.strip())
    return f"入队 outbox #{oid}"


def serve():
    settings = get_settings()
    if m := settings.validate():
        print(f"[QQ Bridge] 缺少配置: {m}")
        return
    storage.init_db()
    start_bot()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
