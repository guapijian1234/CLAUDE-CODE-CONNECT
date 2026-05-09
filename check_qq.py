"""检查 QQ 待处理消息 - 供定时任务使用"""
import sys
sys.path.insert(0, ".")

from qq_bridge import storage
storage.init_db()

msgs = storage.get_pending_messages(limit=10)
stats = storage.get_stats()

print(f"pending={stats['pending_messages']} outbox={stats['pending_outbox']} total={stats['total_messages']}")

if msgs:
    for m in msgs:
        ct = "group" if m['chat_type'] == 'group' else 'c2c'
        gid = m.get('group_openid') or ''
        print(f"[{m['id']}] {ct} gid={gid} author={m['author_id']} time={m['created_at']}")
        print(f"  content: {m['content']}")
else:
    print("no_messages")
