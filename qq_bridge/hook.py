"""Claude Code hook integration for QQ progress updates."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from . import storage
from .config import Settings, get_settings
from .qq_bot import parse_chat_id
from .text import truncate

HOOK_COMMAND = "python -m qq_bridge hook"
HOOK_EVENTS_WITH_MATCHER = {
    "PreToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
}
HOOK_EVENTS_WITHOUT_MATCHER = {
    "Notification",
    "SubagentStart",
    "TaskCreated",
    "Stop",
    "StopFailure",
}
HOOK_EVENTS = HOOK_EVENTS_WITH_MATCHER | HOOK_EVENTS_WITHOUT_MATCHER

SENSITIVE_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{8,}|"
    r"(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s\"']+)"
)


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handle_event(payload)
    except Exception as exc:
        try:
            get_settings().log_full_path.parent.mkdir(parents=True, exist_ok=True)
            with get_settings().log_full_path.open("a", encoding="utf-8") as fh:
                fh.write(f"qq hook failed: {exc}\n")
        except Exception:
            pass
        return


def handle_event(payload: dict[str, Any]) -> None:
    settings = get_settings()
    event = str(payload.get("hook_event_name") or "")
    if not settings.progress_enabled or settings.progress_mode == "off":
        if event in {"Stop", "StopFailure"}:
            storage.init_db()
            storage.clear_active_chat()
        return

    storage.init_db()
    active = storage.get_active_chat(settings.progress_active_ttl_seconds)
    if not active:
        return

    text = format_event(payload, settings)
    if not text:
        if event in {"Stop", "StopFailure"}:
            storage.clear_active_chat()
        return

    enqueue_progress(active, text, settings)
    if event in {"Stop", "StopFailure"}:
        storage.clear_active_chat()


def enqueue_progress(active: dict[str, Any], text: str, settings: Settings) -> None:
    chat_id = str(active.get("chat_id") or "")
    chat_type, target_id = parse_chat_id(chat_id)
    reply_msg_id = str(active.get("reply_msg_id") or "") if settings.progress_reply_to_source else None
    kwargs = {
        "chat_type": chat_type,
        "target_id": target_id,
        "content": text,
        "reply_msg_id": reply_msg_id or None,
        "source_message_id": active.get("source_message_id"),
    }
    if settings.progress_mode == "full":
        storage.insert_outbox(
            **kwargs,
            message_format="markdown" if settings.markdown_enabled else "text",
        )
    else:
        storage.insert_progress_event(**kwargs)


def format_event(payload: dict[str, Any], settings: Settings) -> str | None:
    event = str(payload.get("hook_event_name") or "")
    tool_name = str(payload.get("tool_name") or "")
    if tool_name.startswith("mcp__qq-bridge__"):
        return None

    if event == "PreToolUse":
        if settings.progress_mode == "compact" and not _is_compact_tool(tool_name):
            return None
        return _limit(format_tool_call(payload), settings)
    if event == "PostToolUseFailure":
        return _limit(f"{tool_name} failed\n{describe_tool(payload)}", settings)
    if event == "PermissionRequest":
        return _limit(f"Permission requested: {tool_name}\n{describe_tool(payload)}", settings)
    if event == "PermissionDenied":
        return _limit(f"Permission denied: {tool_name}\n{describe_tool(payload)}", settings)
    if event == "Notification":
        message = _clean(str(payload.get("message") or payload.get("notification") or ""))
        return _limit(message, settings) if message else None
    if event == "SubagentStart":
        name = _clean(str(payload.get("agent_name") or payload.get("agent_type") or "subagent"))
        return _limit(f"Task {name}", settings)
    if event == "TaskCreated":
        return _limit(f"Task {_task_title(payload)}", settings)
    if event in {"Stop", "StopFailure"}:
        return None
    return None


def format_tool_call(payload: dict[str, Any]) -> str:
    tool_name = str(payload.get("tool_name") or "")
    detail = describe_tool(payload)
    return f"{tool_name} {detail}".strip()


def _is_compact_tool(tool_name: str) -> bool:
    return tool_name in {
        "Bash",
        "Write",
        "Edit",
        "MultiEdit",
        "Task",
        "Agent",
        "WebFetch",
        "WebSearch",
    }


def describe_tool(payload: dict[str, Any]) -> str:
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return _clean(str(tool_input))

    if tool_name == "Bash":
        command = _clean(str(tool_input.get("command") or ""))
        description = _clean(str(tool_input.get("description") or ""))
        return command or description
    if tool_name in {"Read", "Write", "Edit", "MultiEdit"}:
        return _path_line(tool_input)
    if tool_name in {"Grep", "Glob"}:
        pattern = _clean(str(tool_input.get("pattern") or ""))
        path = _display_path(str(tool_input.get("path") or ""))
        if tool_name == "Glob":
            return pattern
        return " ".join(part for part in [f'"{pattern}"' if pattern else "", path] if part)
    if tool_name in {"WebFetch", "WebSearch"}:
        target = tool_input.get("url") or tool_input.get("query") or ""
        return _clean(str(target))
    if tool_name in {"Task", "Agent"}:
        title = tool_input.get("description") or tool_input.get("subagent_type") or tool_input.get("prompt") or ""
        return _clean(str(title))
    if tool_name == "TodoWrite":
        todos = tool_input.get("todos")
        if isinstance(todos, list):
            return f"{len(todos)} todos"

    compact = {key: value for key, value in tool_input.items() if key not in {"content", "new_string", "old_string"}}
    return _clean(json.dumps(compact, ensure_ascii=False))


def _path_line(tool_input: dict[str, Any]) -> str:
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    return _display_path(str(path)) if path else ""


def _display_path(path: str) -> str:
    cleaned = _clean(path)
    if not cleaned:
        return ""
    try:
        absolute = os.path.abspath(os.path.normpath(cleaned))
        for base in (os.getcwd(), str(Path.home())):
            base_abs = os.path.abspath(os.path.normpath(base))
            rel = os.path.relpath(absolute, base_abs)
            if not rel.startswith("..") and not os.path.isabs(rel):
                if base_abs == os.path.abspath(os.path.normpath(str(Path.home()))):
                    rel = os.path.join("~", rel)
                return rel.replace(os.sep, "/")
    except Exception:
        return cleaned
    return cleaned


def _task_title(payload: dict[str, Any]) -> str:
    for key in ("task", "title", "description", "prompt"):
        value = payload.get(key)
        if value:
            return _clean(str(value))
    return "untitled"


def _clean(text: str) -> str:
    text = SENSITIVE_RE.sub("[redacted]", text)
    return text.replace("\r\n", "\n").strip()


def _limit(text: str, settings: Settings) -> str:
    return truncate(text, settings.progress_max_length)


def install_hooks(settings_path: Path | None = None) -> None:
    path = settings_path or Path.home() / ".claude" / "settings.json"
    data = _read_json(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    _remove_hooks(hooks)
    handler = {
        "type": "command",
        "command": HOOK_COMMAND,
        "timeout": 10,
    }

    for event in sorted(HOOK_EVENTS_WITH_MATCHER):
        hooks.setdefault(event, []).append({"matcher": "*", "hooks": [dict(handler)]})
    for event in sorted(HOOK_EVENTS_WITHOUT_MATCHER):
        hooks.setdefault(event, []).append({"hooks": [dict(handler)]})

    _write_json(path, data)


def uninstall_hooks(settings_path: Path | None = None) -> None:
    path = settings_path or Path.home() / ".claude" / "settings.json"
    data = _read_json(path)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        _remove_hooks(hooks)
    _write_json(path, data)


def _remove_hooks(hooks: dict[str, Any]) -> None:
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        cleaned_groups = []
        for group in groups:
            if not isinstance(group, dict):
                cleaned_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                cleaned_groups.append(group)
                continue
            group["hooks"] = [
                hook
                for hook in handlers
                if not (isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND)
            ]
            if group["hooks"]:
                cleaned_groups.append(group)
        if cleaned_groups:
            hooks[event] = cleaned_groups
        else:
            hooks.pop(event, None)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
