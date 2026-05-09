"""Text helpers shared by QQ and Claude transports."""

from __future__ import annotations


def split_text(text: str, limit: int) -> list[str]:
    cleaned = (text or "").replace("\r\n", "\n").strip()
    if not cleaned:
        return [""]

    chunks: list[str] = []
    rest = cleaned
    while len(rest) > limit:
        cut = limit
        paragraph = rest.rfind("\n\n", 0, limit)
        newline = rest.rfind("\n", 0, limit)
        space = rest.rfind(" ", 0, limit)
        for candidate in (paragraph, newline, space):
            if candidate > limit // 2:
                cut = candidate
                break
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def truncate(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
