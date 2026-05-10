"""Small SQLite persistence layer used by the bridge."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any

from .config import get_settings

_local = threading.local()


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(str(get_settings().db_full_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return _local.conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id      TEXT    NOT NULL,
            content         TEXT    NOT NULL,
            raw_content     TEXT,
            author_id       TEXT    NOT NULL,
            author_name     TEXT,
            chat_type       TEXT    NOT NULL,
            group_openid    TEXT,
            guild_id        TEXT,
            channel_id      TEXT,
            status          TEXT    NOT NULL DEFAULT 'pending',
            created_at      TEXT    NOT NULL,
            processed_at    TEXT,
            reply_content   TEXT
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
            retry_count     INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_msg_status ON messages(status);
        CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);

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

        CREATE INDEX IF NOT EXISTS idx_progress_status_created
            ON progress_events(status, created_ts);
        """
    )
    _add_column(conn, "messages", "chat_id", "TEXT")
    _add_column(conn, "messages", "error_info", "TEXT")
    _add_column(conn, "outbox", "source_message_id", "INTEGER")
    _add_column(conn, "outbox", "remote_message_id", "TEXT")
    _add_column(conn, "outbox", "message_format", "TEXT NOT NULL DEFAULT 'text'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id)")
    conn.commit()


def set_state(key: str, value: str) -> None:
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO bridge_state (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, utc_now()),
    )
    conn.commit()


def get_state(key: str) -> str | None:
    conn = _get_conn()
    row = conn.execute("SELECT value FROM bridge_state WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def delete_state(key: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM bridge_state WHERE key=?", (key,))
    conn.commit()


def set_active_chat(
    *,
    chat_id: str,
    reply_msg_id: str | None,
    source_message_id: int | None,
) -> None:
    payload = {
        "chat_id": chat_id,
        "reply_msg_id": reply_msg_id,
        "source_message_id": source_message_id,
        "started_at": int(time.time()),
    }
    set_state("active_chat", json.dumps(payload, ensure_ascii=False))


def get_active_chat(max_age_seconds: int | None = None) -> dict[str, Any] | None:
    raw = get_state("active_chat")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        delete_state("active_chat")
        return None

    if max_age_seconds is not None:
        started_at = int(payload.get("started_at") or 0)
        if not started_at or time.time() - started_at > max_age_seconds:
            delete_state("active_chat")
            return None
    return payload


def clear_active_chat() -> None:
    delete_state("active_chat")


def insert_progress_event(
    *,
    chat_type: str,
    target_id: str,
    content: str,
    reply_msg_id: str | None = None,
    source_message_id: int | None = None,
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO progress_events (
            chat_type, target_id, content, reply_msg_id,
            source_message_id, status, created_at, created_ts
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (chat_type, target_id, content, reply_msg_id, source_message_id, utc_now(), time.time()),
    )
    conn.commit()
    return int(cur.lastrowid)


def claim_progress_batch(max_items: int, delay_seconds: float) -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT * FROM progress_events
        WHERE status='pending'
        ORDER BY created_ts ASC
        LIMIT 100
        """
    ).fetchall()
    pending = [dict(row) for row in rows]
    if not pending:
        return []

    first = pending[0]
    same_chat = [
        row
        for row in pending
        if row["chat_type"] == first["chat_type"]
        and row["target_id"] == first["target_id"]
        and (row.get("reply_msg_id") or "") == (first.get("reply_msg_id") or "")
    ]
    selected = same_chat[:max_items]
    if len(selected) < max_items and time.time() - float(first["created_ts"]) < delay_seconds:
        return []

    claimed: list[dict[str, Any]] = []
    for item in selected:
        cur = conn.execute(
            "UPDATE progress_events SET status='sending' WHERE id=? AND status='pending'",
            (item["id"],),
        )
        if cur.rowcount == 1:
            claimed.append(item)
    conn.commit()
    return claimed


def mark_progress_batch_sent(ids: list[int]) -> None:
    if not ids:
        return
    conn = _get_conn()
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE progress_events SET status='sent' WHERE id IN ({placeholders})", ids)
    conn.commit()


def reset_progress_batch(ids: list[int], error: str | None = None) -> None:
    if not ids:
        return
    conn = _get_conn()
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"UPDATE progress_events SET status='pending' WHERE id IN ({placeholders})", ids)
    conn.commit()


def insert_message(
    *,
    message_id: str,
    content: str,
    raw_content: str | None,
    author_id: str,
    author_name: str | None,
    chat_type: str,
    chat_id: str,
    group_openid: str | None = None,
    guild_id: str | None = None,
    channel_id: str | None = None,
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO messages (
            message_id, content, raw_content, author_id, author_name,
            chat_type, chat_id, group_openid, guild_id, channel_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            content,
            raw_content,
            author_id,
            author_name,
            chat_type,
            chat_id,
            group_openid,
            guild_id,
            channel_id,
            utc_now(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_message_by_id(msg_id: int) -> dict[str, Any] | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return dict(row) if row else None


def get_pending_messages(limit: int = 5) -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE status='pending' ORDER BY created_at ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_message_status(
    msg_id: int,
    status: str,
    reply_content: str | None = None,
    error_info: str | None = None,
) -> None:
    conn = _get_conn()
    conn.execute(
        """
        UPDATE messages
        SET status=?, processed_at=?, reply_content=COALESCE(?, reply_content),
            error_info=COALESCE(?, error_info)
        WHERE id=?
        """,
        (status, utc_now(), reply_content, error_info, msg_id),
    )
    conn.commit()


def insert_outbox(
    *,
    chat_type: str,
    target_id: str,
    content: str,
    message_format: str = "text",
    reply_msg_id: str | None = None,
    source_message_id: int | None = None,
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO outbox (
            chat_type, target_id, content, message_format, reply_msg_id, source_message_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (chat_type, target_id, content, message_format, reply_msg_id, source_message_id, utc_now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_pending_outbox(limit: int = 10) -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM outbox WHERE status='pending' ORDER BY created_at ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_outbox_sending(outbox_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE outbox SET status='sending' WHERE id=? AND status='pending'",
        (outbox_id,),
    )
    conn.commit()
    return cur.rowcount == 1


def mark_outbox_sent(outbox_id: int, remote_message_id: str | None = None) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE outbox SET status='sent', sent_at=?, remote_message_id=? WHERE id=?",
        (utc_now(), remote_message_id, outbox_id),
    )
    conn.commit()


def mark_outbox_failed(outbox_id: int, error: str) -> None:
    conn = _get_conn()
    conn.execute(
        """
        UPDATE outbox
        SET status='failed', error_info=?, retry_count=retry_count+1
        WHERE id=?
        """,
        (error[:1000], outbox_id),
    )
    conn.commit()


def reset_outbox_to_pending(outbox_id: int, error: str | None = None) -> None:
    conn = _get_conn()
    conn.execute(
        """
        UPDATE outbox
        SET status='pending', error_info=COALESCE(?, error_info)
        WHERE id=?
        """,
        (error[:1000] if error else None, outbox_id),
    )
    conn.commit()


def get_stats() -> dict[str, int]:
    conn = _get_conn()

    def count(query: str) -> int:
        return int(conn.execute(query).fetchone()["cnt"])

    return {
        "pending_messages": count("SELECT COUNT(*) AS cnt FROM messages WHERE status='pending'"),
        "delivered_messages": count("SELECT COUNT(*) AS cnt FROM messages WHERE status='delivered'"),
        "failed_messages": count("SELECT COUNT(*) AS cnt FROM messages WHERE status='failed'"),
        "pending_outbox": count("SELECT COUNT(*) AS cnt FROM outbox WHERE status='pending'"),
        "failed_outbox": count("SELECT COUNT(*) AS cnt FROM outbox WHERE status='failed'"),
        "total_messages": count("SELECT COUNT(*) AS cnt FROM messages"),
    }
