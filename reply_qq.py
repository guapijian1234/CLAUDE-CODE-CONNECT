"""Reply to a QQ message and mark it processed."""
import sys
sys.path.insert(0, ".")

from qq_bridge import storage
storage.init_db()

msg_id = int(sys.argv[1])
target = sys.argv[2]  # e.g. c2c:OPENID or group:OPENID
reply_content = sys.argv[3]

# Get the original QQ message_id for passive reply
msg = storage.get_message_by_id(msg_id)
reply_msg_id = msg['message_id'] if msg else None

# Send reply
oid = storage.insert_outbox(
    chat_type=target.split(":")[0],
    target_id=target.split(":")[1],
    content=reply_content,
    reply_msg_id=reply_msg_id,
)
print(f"Reply queued: outbox_id={oid} reply_to_msg={reply_msg_id}")

# Mark processed
storage.update_message_status(msg_id, "processed", reply_content)
print(f"Message {msg_id} marked as processed")
