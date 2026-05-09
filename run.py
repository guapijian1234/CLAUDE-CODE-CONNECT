"""启动 QQ Bridge — 最少依赖"""
import sys, time
sys.path.insert(0, ".")
from qq_bridge.config import get_settings
from qq_bridge import storage
from qq_bridge.qq_bot import start_bot, get_status

settings = get_settings()
if settings.validate():
    print("Missing config:", settings.validate())
    sys.exit(1)

storage.init_db()
print("DB ready")
start_bot()
time.sleep(5)
print("Bot:", get_status())

try:
    while True:
        time.sleep(30)
except KeyboardInterrupt:
    print("Done")
