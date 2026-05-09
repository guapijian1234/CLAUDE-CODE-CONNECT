"""SQLite 消息存储 — 线程安全的数据库操作"""

import sqlite3
import threading
from datetime import datetime
from .config import get_settings

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（线程本地存储）"""
    if not hasattr(_local, "conn") or _local.conn is None:
        db_path = str(get_settings().db_full_path)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return _local.conn


def init_db():
    """初始化数据库表"""
    conn = _get_conn()
    conn.executescript("""
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
    """)
    conn.commit()


def insert_message(*, message_id: str, content: str, raw_content: str | None,
                   author_id: str, author_name: str | None, chat_type: str,
                   group_openid: str | None = None, guild_id: str | None = None,
                   channel_id: str | None = None) -> int:
    """插入收到的 QQ 消息，返回内部 id"""
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """INSERT INTO messages (message_id, content, raw_content, author_id, author_name,
           chat_type, group_openid, guild_id, channel_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (message_id, content, raw_content, author_id, author_name,
         chat_type, group_openid, guild_id, channel_id, now)
    )
    conn.commit()
    return cur.lastrowid


def get_pending_messages(limit: int = 5):
    """获取待处理的消息列表"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE status='pending' ORDER BY created_at ASC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_message_by_id(msg_id: int):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return dict(row) if row else None


def update_message_status(msg_id: int, status: str, reply_content: str | None = None):
    """更新消息状态，可选附带回复内容"""
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if reply_content is not None:
        conn.execute(
            "UPDATE messages SET status=?, processed_at=?, reply_content=? WHERE id=?",
            (status, now, reply_content, msg_id)
        )
    else:
        conn.execute(
            "UPDATE messages SET status=?, processed_at=? WHERE id=?",
            (status, now, msg_id)
        )
    conn.commit()


def count_by_status(status: str) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE status=?", (status,)
    ).fetchone()
    return row["cnt"]


def insert_outbox(*, chat_type: str, target_id: str, content: str,
                  reply_msg_id: str | None = None) -> int:
    """向发件箱插入待发送消息"""
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO outbox (chat_type, target_id, content, reply_msg_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (chat_type, target_id, content, reply_msg_id, now)
    )
    conn.commit()
    return cur.lastrowid


def get_pending_outbox(limit: int = 5):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM outbox WHERE status='pending' ORDER BY created_at ASC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_outbox_sending(outbox_id: int):
    """标记发件箱消息为发送中，防止重复发送"""
    conn = _get_conn()
    conn.execute(
        "UPDATE outbox SET status='sending' WHERE id=? AND status='pending'",
        (outbox_id,)
    )
    conn.commit()


def mark_outbox_sent(outbox_id: int):
    conn = _get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE outbox SET status='sent', sent_at=? WHERE id=?", (now, outbox_id)
    )
    conn.commit()


def mark_outbox_failed(outbox_id: int, error: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE outbox SET status='failed', error_info=?, retry_count=retry_count+1 "
        "WHERE id=?", (error, outbox_id)
    )
    conn.commit()


def get_stats() -> dict:
    """获取统计信息"""
    conn = _get_conn()
    pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE status='pending'"
    ).fetchone()["cnt"]
    outbox_pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM outbox WHERE status='pending'"
    ).fetchone()["cnt"]
    total_msg = conn.execute(
        "SELECT COUNT(*) as cnt FROM messages"
    ).fetchone()["cnt"]
    return {
        "pending_messages": pending,
        "pending_outbox": outbox_pending,
        "total_messages": total_msg,
    }
