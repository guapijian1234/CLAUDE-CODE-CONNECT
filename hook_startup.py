"""SessionStart hook — reminds AI to start QQ Monitor"""
import json
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "[QQ Bridge] Session started. If Monitor is not running, start it now: Monitor(command='python "C:\\Users\\Administrator\\Desktop\\CLAUDE CODE CONNECT\\watch_qq.py"', persistent=true, description='QQ watcher')"
    }
}))
