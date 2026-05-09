import sqlite3
conn = sqlite3.connect("data/bridge.db")
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT * FROM outbox ORDER BY id DESC").fetchall()
for r in rows:
    err = r['error_info'] or '-'
    print(f"[{r['id']}] status={r['status']} type={r['chat_type']} target={r['target_id']}")
    print(f"  content: {r['content'][:80]}")
    print(f"  error: {err}")
    print()

print(f"Total outbox: {len(rows)}")

# Also check all messages
msgs = conn.execute("SELECT id, status, content, reply_content FROM messages ORDER BY id DESC").fetchall()
for m in msgs:
    print(f"Msg[{m['id']}] status={m['status']} content={m['content']} reply={m.get('reply_content', '-')}")
conn.close()
