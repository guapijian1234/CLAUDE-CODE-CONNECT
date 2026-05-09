"""Real-time QQ message watcher — polls DB, prints IDs, writes content to files"""
import sys
import time
import os
sys.path.insert(0, ".")

from qq_bridge import storage
storage.init_db()

seen_ids = set()
for m in storage.get_pending_messages(limit=100):
    seen_ids.add(m['id'])

print(f"WATCH_STARTED seen={len(seen_ids)} pending={storage.get_stats()['pending_messages']}", flush=True)

while True:
    msgs = storage.get_pending_messages(limit=10)
    for m in msgs:
        if m['id'] not in seen_ids:
            seen_ids.add(m['id'])
            ct = "group" if m['chat_type'] == 'group' else 'c2c'
            gid = m.get('group_openid') or ''
            aid = m['author_id']
            mid = m['id']
            content = m['content']

            # Write content to UTF-8 file
            fpath = f"data/msg{mid}.txt"
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)

            # Print ASCII-safe notification
            print(f"NEW|{mid}|{ct}|{gid}|{aid[:8]}", flush=True)
    time.sleep(2)
