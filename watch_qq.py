"""Real-time QQ message watcher — polls DB and prints new messages"""
import sys
import time
sys.path.insert(0, ".")

from qq_bridge import storage
storage.init_db()

# Track the last message ID we've seen
seen_ids = set()
# Initialize with messages that already exist (so we don't re-process old ones)
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
            # Output format: NEW|id|chat_type|group_openid|author_id|content
            content = m['content'].replace('\n', '\\n').replace('|', '\\|')
            print(f"NEW|{m['id']}|{ct}|{gid}|{m['author_id']}|{content}", flush=True)
    time.sleep(2)
