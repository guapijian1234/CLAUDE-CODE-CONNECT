"""MCP 服务器 — 提供 QQ 消息检查、发送等工具给本地 AI 助手调用"""

import json
from mcp.server.fastmcp import FastMCP
from . import storage
from .config import get_settings
from .qq_bot import start_bot_thread, get_bot_status

mcp = FastMCP("qq-bridge")


@mcp.tool()
async def qq_check(limit: int = 5) -> str:
    """检查 QQ 中的待处理消息（群聊@和私聊）

    返回待处理消息列表，包含消息 ID、来源类型、发送者、内容和时间。
    处理完消息后应调用 qq_send 回复，然后调用 qq_mark 标记已处理。
    """
    msgs = storage.get_pending_messages(limit=limit)
    if not msgs:
        return "暂无待处理的 QQ 消息。"

    result = []
    for m in msgs:
        chat_label = "群聊" if m['chat_type'] == 'group' else "私聊"
        group_info = f" | 群: {m['group_openid']}" if m.get('group_openid') else ""
        result.append(
            f"[ID:{m['id']}] {chat_label}{group_info}\n"
            f"  发送者: {m['author_id']}\n"
            f"  时间: {m['created_at']}\n"
            f"  内容: {m['content']}"
        )
    return "\n\n".join(result)


@mcp.tool()
async def qq_send(target: str, content: str) -> str:
    """发送消息到 QQ 聊天

    target 参数格式:
      - 发送到群聊: "group:{group_openid}"
      - 发送到私聊: "c2c:{user_openid}"

    消息先放入发件箱，QQ Bot 会在 1-2 秒内自动发送。
    """
    parts = target.split(":", 1)
    if len(parts) != 2:
        return f"错误: target 格式应为 'group:openid' 或 'c2c:openid'，收到: {target}"

    chat_type, target_id = parts
    if chat_type not in ("group", "c2c"):
        return f"错误: chat_type 必须为 'group' 或 'c2c'，收到: {chat_type}"

    content = content.strip()
    if not content:
        return "错误: 消息内容不能为空"

    if len(content) > 4000:
        content = content[:4000]

    outbox_id = storage.insert_outbox(
        chat_type=chat_type,
        target_id=target_id,
        content=content,
    )

    return (
        f"消息已入队 (发件箱 ID: {outbox_id})，将在 1-2 秒内发送。\n"
        f"  类型: {chat_type}\n"
        f"  目标: {target_id}\n"
        f"  内容: {content[:100]}{'...' if len(content) > 100 else ''}"
    )


@mcp.tool()
async def qq_mark(msg_id: int, status: str = "processed") -> str:
    """标记消息状态

    status 可选值: processed（已处理）、skipped（跳过）、failed（失败）
    """
    msg = storage.get_message_by_id(msg_id)
    if not msg:
        return f"错误: 未找到 ID 为 {msg_id} 的消息"

    if status not in ("processed", "skipped", "failed"):
        return f"错误: status 必须为 processed/skipped/failed，收到: {status}"

    storage.update_message_status(msg_id, status)
    return f"消息 {msg_id} 已标记为 {status}"


@mcp.tool()
async def qq_status() -> str:
    """查看 QQ Bridge 运行状态（Bot 连接、消息队列等）"""
    bot = get_bot_status()
    stats = storage.get_stats()

    lines = [
        "QQ Bridge 运行状态",
        "==================",
        f"Bot 连接: {'已连接' if bot['running'] else '未连接'}",
    ]
    if bot.get('error'):
        lines.append(f"连接错误: {bot['error']}")
    lines.extend([
        f"待处理消息: {stats['pending_messages']} 条",
        f"待发送消息: {stats['pending_outbox']} 条",
        f"累计消息: {stats['total_messages']} 条",
    ])
    return "\n".join(lines)


def serve():
    """启动 MCP 服务器"""
    settings = get_settings()
    missing = settings.validate()
    if missing:
        print(f"[QQ Bridge] 错误: 缺少必要配置: {', '.join(missing)}")
        print("[QQ Bridge] 请在 .env 文件中设置以上环境变量")
        return

    storage.init_db()
    start_bot_thread()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
