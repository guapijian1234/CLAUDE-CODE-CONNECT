import sqlite3
conn = sqlite3.connect("data/bridge.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT id, content, raw_content, author_id, chat_type, created_at "
    "FROM messages WHERE status='pending'"
).fetchall()

with open("data/msg_output.txt", "w", encoding="utf-8") as f:
    for r in rows:
        f.write(f"[{r['id']}] {r['chat_type']} | {r['author_id']} | {r['created_at']}\n")
        f.write(f"  content: {r['content']}\n")
        if r['raw_content']:
            f.write(f"  raw: {r['raw_content']}\n")
        f.write("\n")
    f.write(f"Total: {len(rows)}\n")

print(f"Wrote {len(rows)} messages to data/msg_output.txt")
print("Done")
conn.close()
