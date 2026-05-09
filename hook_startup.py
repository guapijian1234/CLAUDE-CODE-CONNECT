"""SessionStart hook — reminds AI to start QQ Monitor"""
import json
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "[QQ Bridge] Session started. Start Monitor: Monitor(command='python watch_qq.py', persistent=true, description='QQ watcher')"
    }
}))
