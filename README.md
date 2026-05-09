# QQ Bridge for Claude Code

This project has one job: route QQ private messages and group @ messages into
an already running Claude Code session, then send Claude Code's `reply` tool
output back to QQ.

It does not start `claude -p` for incoming QQ messages and it does not create a
new Claude Code session per message.

## Architecture

```text
QQ -> qq_bridge MCP server -> active Claude Code session -> reply tool -> QQ
```

Claude Code must be started with this channel loaded. A Claude Code process
that was started without the QQ channel cannot be attached to later by forcing
stdin/stdout from the outside.

## Config

Copy `.env.example` to `.env`, then fill the Tencent QQ Open Platform values:

```env
QQ_BRIDGE_BOT_APP_ID=your_app_id_here
QQ_BRIDGE_BOT_APP_SECRET=your_app_secret_here
```

Optional allowlists:

```env
QQ_BRIDGE_ALLOWED_USERS=
QQ_BRIDGE_ALLOWED_GROUPS=
```

Send `/id` to the bot to get `user_openid` and `group_openid`.

Claude replies are sent to QQ as Markdown by default:

```env
QQ_BRIDGE_MARKDOWN_ENABLED=true
QQ_BRIDGE_MARKDOWN_FALLBACK_TO_TEXT=true
```

If your QQ bot does not have Markdown permission, keep fallback enabled so the
same reply is resent as plain text.

## Start Claude Code

Start Claude Code with the QQ channel server:

```sh
claude --dangerously-load-development-channels server:qq-bridge
```

Keep that Claude Code window open. Incoming QQ messages will appear inside that
session as channel notifications. Claude Code should answer with the `reply`
tool and reuse the inbound `chat_id`.

## Auto Start Everywhere

Install the PowerShell wrapper once:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-claude-autostart.ps1
```

The installer does three things:

- installs this project with `pip install -e`
- writes `qq-bridge` into Claude Code's user MCP config with
  `claude mcp add -s user`
- writes a guarded `claude` function to your PowerShell profile
- installs a `claude.cmd` shim in `%USERPROFILE%\bin` and prepends that folder
  to your user `PATH`, so new CMD/PowerShell terminals can also auto-attach QQ

If your current user execution policy blocks profiles, it sets it to
`RemoteSigned`.

Open a new PowerShell window in any project, then just run:

```powershell
claude
```

That command automatically adds the QQ channel server:

```powershell
--dangerously-load-development-channels server:qq-bridge
```

The installer also writes `qq-bridge` into Claude Code's user MCP config.

Management commands such as `claude plugin validate`, `claude mcp list`,
`claude --version`, and commands that already pass channel flags keep their
normal behavior.

To limit the wrapper to this bridge project only, reinstall it with
`-ProjectOnly`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-claude-autostart.ps1 -ProjectOnly
```

To remove the wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-claude-autostart.ps1 -Uninstall
```

## Verify

```sh
python -m compileall qq_bridge
claude plugin validate .
```

`python -m qq_bridge mcp` and `python run.py` are MCP server entrypoints.
Normally Claude Code starts them automatically; you do not need to run them as
separate user-facing processes.
