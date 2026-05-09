"""AI API 客户端 — 直接调用 DeepSeek/Anthropic API 获取回复"""
import os
import json
import logging
import aiohttp

logger = logging.getLogger("qq_bridge.ai")

# Use same API config as the CLI session
API_BASE = os.environ.get(
    "ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"
)
API_KEY = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro")

# Conversation history (simple in-memory, single user)
_history: list[dict] = []
MAX_HISTORY = 20


async def chat(content: str) -> str:
    """Send a message to the AI API and return the response text."""
    global _history

    if not API_KEY:
        return "[错误: 未配置 AI API Key]"

    # Add user message to history
    _history.append({"role": "user", "content": content})

    # Trim history
    if len(_history) > MAX_HISTORY:
        _history = _history[-MAX_HISTORY:]

    headers = {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "max_tokens": 4096,
        "messages": _history,
    }

    url = f"{API_BASE}/v1/messages"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                body = await resp.json()

                if resp.status != 200:
                    err = body.get("error", {}).get("message", str(body))
                    logger.error("AI API error %d: %s", resp.status, err)
                    return f"[AI 错误: {err}]"

                # Anthropic format: content is a list of blocks
                content_blocks = body.get("content", [])
                text_parts = []
                for block in content_blocks:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))

                reply = "".join(text_parts).strip()

                # Add assistant response to history
                if reply:
                    _history.append({"role": "assistant", "content": reply})

                return reply or "[AI 返回空内容]"

    except aiohttp.ClientTimeout:
        logger.error("AI API timeout")
        return "[AI 响应超时]"
    except Exception as e:
        logger.error("AI API exception: %s", e)
        return f"[AI 调用异常: {e}]"


def reset_history():
    """重置对话历史"""
    global _history
    _history = []
    logger.info("Conversation history reset")
