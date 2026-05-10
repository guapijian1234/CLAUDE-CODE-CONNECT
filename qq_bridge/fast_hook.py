"""Lightweight Claude Code hook runtime.

This module intentionally uses only the Python standard library. Claude Code
starts a fresh hook process for every matching event, so importing the MCP
server, botpy client, or pydantic settings here would directly slow down every
tool call.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT_OFFSET_PREFIX = "transcript_offset:"
ASSISTANT_LINE_PREFIX = "\u25cf "
SENSITIVE_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{8,}|"
    r"(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s\"']+)"
)


@dataclass(slots=True)
class HookSettings:
    db_path: str = "data/bridge.db"
    log_path: str = "logs/qq_bridge.log"
    markdown_enabled: bool = True
    progress_enabled: bool = True
    progress_level: str = "normal"
    progress_active_ttl_seconds: int = 7200
    progress_max_length: int = 500
    progress_reply_to_source: bool = False
    progress_include_assistant_text: bool = True
    progress_transcript_tail_bytes: int = 1_000_000

    @property
    def progress_mode(self) -> str:
        mode = (self.progress_level or "normal").strip().lower()
        return mode if mode in {"off", "compact", "normal", "full"} else "normal"

    @property
    def db_full_path(self) -> Path:
        return _resolve_project_path(self.db_path)

    @property
    def log_full_path(self) -> Path:
        return _resolve_project_path(self.log_path)


def main() -> None:
    settings = load_settings()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handle_event(payload, settings)
    except Exception as exc:
        _log_error(settings, f"qq fast hook failed: {exc}")


def handle_event(payload: dict[str, Any], settings: HookSettings) -> None:
    event = str(payload.get("hook_event_name") or "")
    conn = _connect(settings)
    try:
        if not settings.progress_enabled or settings.progress_mode == "off":
            if event in {"Stop", "StopFailure"}:
                _delete_state(conn, "active_chat")
            return

        active = _get_active_chat(conn, settings.progress_active_ttl_seconds)
        if not active:
            return

        texts: list[str] = []
        if event == "PreToolUse":
            texts.extend(transcript_progress_events(conn, payload, active, settings))

        text = format_event(payload, settings)
        if text:
            texts.append(text)

        wrote_progress = False
        for item in _unique_texts(texts):
            enqueue_progress(conn, active, item, settings)
            wrote_progress = True

        if event in {"Stop", "StopFailure"}:
            _delete_state(conn, "active_chat")
        elif wrote_progress:
            conn.commit()
    finally:
        conn.close()


def enqueue_progress(
    conn: sqlite3.Connection,
    active: dict[str, Any],
    text: str,
    settings: HookSettings,
) -> None:
    chat_id = str(active.get("chat_id") or "")
    chat_type, target_id = parse_chat_id(chat_id)
    reply_msg_id = str(active.get("reply_msg_id") or "") if settings.progress_reply_to_source else None
    if settings.progress_mode == "full":
        conn.execute(
            """
            INSERT INTO outbox (
                chat_type, target_id, content, message_format, reply_msg_id,
                source_message_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_type,
                target_id,
                text,
                "markdown" if settings.markdown_enabled else "text",
                reply_msg_id or None,
                active.get("source_message_id"),
                utc_now(),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO progress_events (
                chat_type, target_id, content, reply_msg_id,
                source_message_id, status, created_at, created_ts
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                chat_type,
                target_id,
                text,
                reply_msg_id or None,
                active.get("source_message_id"),
                utc_now(),
                time.time(),
            ),
        )


def format_event(payload: dict[str, Any], settings: HookSettings) -> str | None:
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
    return None


def transcript_progress_events(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    active: dict[str, Any],
    settings: HookSettings,
) -> list[str]:
    if not settings.progress_include_assistant_text:
        return []

    path_value = payload.get("transcript_path")
    if not path_value:
        return []

    path = Path(str(path_value)).expanduser()
    if not path.is_file():
        return []

    state_key = _transcript_state_key(path)
    offset = _parse_offset(_get_state(conn, state_key))
    lines, new_offset = _read_transcript_lines(
        path,
        offset=offset,
        tail_bytes=settings.progress_transcript_tail_bytes,
    )
    _set_state(conn, state_key, str(new_offset))
    if not lines:
        return []

    started_at = _active_started_at(active)
    texts: list[str] = []
    for line in lines:
        if '"assistant"' not in line or '"text"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if started_at is not None:
            timestamp = _entry_timestamp(entry)
            if timestamp is not None and timestamp < started_at - 15:
                continue
        for text in _assistant_visible_texts(entry):
            formatted = _format_assistant_line(text, settings)
            if formatted:
                texts.append(formatted)
    return _unique_texts(texts)


def format_tool_call(payload: dict[str, Any]) -> str:
    tool_name = str(payload.get("tool_name") or "")
    detail = describe_tool(payload)
    return f"{tool_name} {detail}".strip()


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


def _read_transcript_lines(path: Path, *, offset: int | None, tail_bytes: int) -> tuple[list[str], int]:
    size = path.stat().st_size
    if offset is None or offset < 0 or offset > size:
        start = max(0, size - tail_bytes) if tail_bytes > 0 else 0
        discard_partial_head = start > 0
    else:
        start = offset
        discard_partial_head = False

    with path.open("rb") as fh:
        fh.seek(start)
        if discard_partial_head:
            fh.readline()
            start = fh.tell()
        raw = fh.read()

    if not raw:
        return [], start
    if raw.endswith(b"\n"):
        complete = raw
        new_offset = start + len(raw)
    else:
        last_newline = raw.rfind(b"\n")
        if last_newline < 0:
            return [], start
        complete = raw[: last_newline + 1]
        new_offset = start + last_newline + 1

    text = complete.decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if line.strip()], new_offset


def _assistant_visible_texts(entry: dict[str, Any]) -> list[str]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    if entry.get("type") != "assistant" and message.get("role") != "assistant":
        return []

    content = message.get("content")
    if not isinstance(content, list):
        return []

    has_tool_use = any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content)
    if message.get("stop_reason") != "tool_use" and not has_tool_use:
        return []

    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = _clean(str(block.get("text") or ""))
        if text:
            texts.append(text)
    return texts


def _format_assistant_line(text: str, settings: HookSettings) -> str | None:
    lines = [_clean(line) for line in text.splitlines() if _clean(line)]
    if not lines:
        return None
    rendered = ASSISTANT_LINE_PREFIX + lines[0]
    if len(lines) > 1:
        rendered += "\n" + "\n".join(f"  {line}" for line in lines[1:])
    return _limit(rendered, settings)


def _connect(settings: HookSettings) -> sqlite3.Connection:
    db_path = settings.db_full_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    needs_init = not db_path.exists()
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if needs_init:
        _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bridge_state (
            key             TEXT PRIMARY KEY,
            value           TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS progress_events (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_type           TEXT    NOT NULL,
            target_id           TEXT    NOT NULL,
            content             TEXT    NOT NULL,
            reply_msg_id        TEXT,
            source_message_id   INTEGER,
            status              TEXT    NOT NULL DEFAULT 'pending',
            created_at          TEXT    NOT NULL,
            created_ts          REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outbox (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_type       TEXT    NOT NULL,
            target_id       TEXT    NOT NULL,
            content         TEXT    NOT NULL,
            message_format  TEXT    NOT NULL DEFAULT 'text',
            reply_msg_id    TEXT,
            status          TEXT    NOT NULL DEFAULT 'pending',
            created_at      TEXT    NOT NULL,
            sent_at         TEXT,
            error_info      TEXT,
            retry_count     INTEGER DEFAULT 0,
            source_message_id INTEGER,
            remote_message_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_progress_status_created
            ON progress_events(status, created_ts);
        CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
        """
    )
    conn.commit()


def _get_state(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute("SELECT value FROM bridge_state WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        _init_db(conn)
        row = conn.execute("SELECT value FROM bridge_state WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO bridge_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, utc_now()),
    )
    conn.commit()


def _delete_state(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM bridge_state WHERE key=?", (key,))
    conn.commit()


def _get_active_chat(conn: sqlite3.Connection, max_age_seconds: int) -> dict[str, Any] | None:
    raw = _get_state(conn, "active_chat")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _delete_state(conn, "active_chat")
        return None

    started_at = int(payload.get("started_at") or 0)
    if not started_at or time.time() - started_at > max_age_seconds:
        _delete_state(conn, "active_chat")
        return None
    return payload


def load_settings() -> HookSettings:
    values = _read_dotenv(PROJECT_ROOT / ".env")
    return HookSettings(
        db_path=_cfg(values, "DB_PATH", "data/bridge.db"),
        log_path=_cfg(values, "LOG_PATH", "logs/qq_bridge.log"),
        markdown_enabled=_cfg_bool(values, "MARKDOWN_ENABLED", True),
        progress_enabled=_cfg_bool(values, "PROGRESS_ENABLED", True),
        progress_level=_cfg(values, "PROGRESS_LEVEL", "normal"),
        progress_active_ttl_seconds=_cfg_int(values, "PROGRESS_ACTIVE_TTL_SECONDS", 7200, 60, 86400),
        progress_max_length=_cfg_int(values, "PROGRESS_MAX_LENGTH", 500, 120, 2000),
        progress_reply_to_source=_cfg_bool(values, "PROGRESS_REPLY_TO_SOURCE", False),
        progress_include_assistant_text=_cfg_bool(values, "PROGRESS_INCLUDE_ASSISTANT_TEXT", True),
        progress_transcript_tail_bytes=_cfg_int(
            values,
            "PROGRESS_TRANSCRIPT_TAIL_BYTES",
            1_000_000,
            0,
            50_000_000,
        ),
    )


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def _cfg(values: dict[str, str], name: str, default: str) -> str:
    key = "QQ_BRIDGE_" + name
    return os.environ.get(key, values.get(key, default))


def _cfg_bool(values: dict[str, str], name: str, default: bool) -> bool:
    raw = _cfg(values, name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _cfg_int(values: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_cfg(values, name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _resolve_project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def parse_chat_id(chat_id: str) -> tuple[str, str]:
    chat_type, sep, target_id = chat_id.partition(":")
    if sep != ":" or chat_type not in {"c2c", "group"} or not target_id:
        raise ValueError("chat_id must be c2c:<openid> or group:<openid>")
    return chat_type, target_id


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


def _transcript_state_key(path: Path) -> str:
    absolute = str(path.resolve(strict=False)).lower()
    digest = hashlib.sha256(absolute.encode("utf-8", errors="ignore")).hexdigest()
    return TRANSCRIPT_OFFSET_PREFIX + digest


def _parse_offset(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _active_started_at(active: dict[str, Any]) -> float | None:
    try:
        value = float(active.get("started_at") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _entry_timestamp(entry: dict[str, Any]) -> float | None:
    raw = entry.get("timestamp") or entry.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _unique_texts(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for text in texts:
        cleaned = _clean(text)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def _clean(text: str) -> str:
    text = SENSITIVE_RE.sub("[redacted]", text)
    return text.replace("\r\n", "\n").strip()


def _limit(text: str, settings: HookSettings) -> str:
    limit = settings.progress_max_length
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _log_error(settings: HookSettings, message: str) -> None:
    try:
        settings.log_full_path.parent.mkdir(parents=True, exist_ok=True)
        with settings.log_full_path.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
