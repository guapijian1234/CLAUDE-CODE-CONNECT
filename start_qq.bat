@echo off
echo Starting QQ Bridge...
start /B python "%~dp0bot_daemon.py" > nul 2>&1
echo QQ Bot daemon started
echo.
echo Now start the Monitor inside Claude Code with:
echo   /qq
echo.
echo Or start Claude Code and the Monitor will auto-load.
