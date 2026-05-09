"""QQ Bot 独立守护进程 — 不依赖 MCP，单独运行"""
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qq_bridge.config import get_settings, PROJECT_ROOT

# Setup logging
log_file = PROJECT_ROOT / "logs" / "bot.log"
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bot_daemon")

settings = get_settings()
missing = settings.validate()
if missing:
    logger.error("Missing config: %s", missing)
    sys.exit(1)

logger.info("AppID: %s...", settings.bot_app_id[:6])
logger.info("DB: %s", settings.db_full_path)

from qq_bridge import storage
storage.init_db()
logger.info("Database initialized")

from qq_bridge.qq_bot import start_bot_thread, get_bot_status

logger.info("Starting bot thread...")
start_bot_thread()

# Wait for connection
time.sleep(5)

status = get_bot_status()
if status["running"]:
    logger.info("Bot started successfully!")
else:
    logger.error("Bot failed to start: %s", status.get("error"))
    sys.exit(1)

logger.info("Bot is running. Press Ctrl+C to stop.")

try:
    while True:
        time.sleep(15)
        stats = storage.get_stats()
        logger.info(
            "Heartbeat | pending_msgs=%d outbox=%d total=%d",
            stats["pending_messages"],
            stats["pending_outbox"],
            stats["total_messages"],
        )
except KeyboardInterrupt:
    logger.info("Bot stopped by user")
