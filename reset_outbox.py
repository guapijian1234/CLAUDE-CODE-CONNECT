import sqlite3
conn = sqlite3.connect("data/bridge.db")
conn.execute("UPDATE outbox SET status='pending', error_info=NULL WHERE status='failed'")
cnt = conn.execute("SELECT changes()").fetchone()[0]
conn.commit()
print(f"Reset {cnt} failed outbox items to pending")
conn.close()
