Start the QQ Bridge system.

Do these steps using PowerShell (not Bash):

1. Start Bot daemon as independent process:
   ```
   Remove-Item "data/bot.pid" -Force -ErrorAction SilentlyContinue
   Start-Process python -ArgumentList "bot_daemon.py" -WorkingDirectory "$PWD" -WindowStyle Hidden
   ```

2. Wait 6 seconds then check bot connected:
   Check logs/bot.log for "Bot ready, token=OK"

3. Start Monitor:
   Monitor(command="python watch_qq.py", persistent=true, description="QQ watcher")

4. Check for pending QQ messages and process them.

5. Report: "QQ Bridge 已启动 — Bot + Monitor 运行中"
