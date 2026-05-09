"""Stop hook — kill bot daemon when Claude Code exits"""
import os
from pathlib import Path

pid_file = Path(__file__).resolve().parent / "data" / "bot.pid"
if pid_file.exists():
    pid = pid_file.read_text().strip()
    os.system(f"taskkill /F /PID {pid} 2>nul")
    pid_file.unlink(missing_ok=True)
