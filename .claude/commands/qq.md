Start the QQ Bridge Monitor to listen for incoming QQ messages in real-time.

Do these steps:
1. Start the QQ Bot daemon if not running: `python bot_daemon.py` (run in background)
2. Start the Monitor: Monitor(command="python watch_qq.py", persistent=true, description="QQ watcher")
3. Check for any pending QQ messages and process them
4. Report: "QQ Bridge 已启动 — Bot + Monitor 运行中"

When QQ messages arrive, process them immediately and send replies via outbox.
