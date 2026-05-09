# QQ Bridge for Claude Code

把 QQ 私聊消息、群聊 @ 消息接入已经打开的 Claude Code 会话，并把 Claude Code 的回复发回 QQ。

这个项目不是一个独立 Agent，也不会为每条 QQ 消息单独启动 `claude -p`。它的目标很简单：让你在外面用 QQ 继续操作当前正在跑的 Claude Code。

## 工作方式

```text
QQ 消息 -> qq-bridge MCP Channel -> 当前 Claude Code 会话 -> reply 工具 -> QQ
```

Claude Code 必须在启动时加载 `qq-bridge` channel。已经启动但没有加载 channel 的 Claude Code 进程，无法从外部强行接上。

## 功能

- 支持 QQ 私聊消息
- 支持 QQ 群聊 @ 机器人消息
- 消息进入当前 Claude Code 会话，而不是新建独立对话
- Claude Code 通过 `reply` 工具把结果发回 QQ
- 默认使用 QQ Markdown 发送回复
- 如果 QQ 平台拒绝 Markdown，可自动降级为普通文本
- 支持用户和群聊 allowlist
- 支持全局自动启动，任意项目里运行 `claude` 都能接入 QQ

## 环境要求

- Windows
- Python 3.10+
- Claude Code
- 腾讯 QQ 开放平台机器人配置

## 安装

克隆仓库后进入项目目录：

```powershell
cd "C:\path\to\CLAUDE CODE CONNECT"
python -m pip install -e .
```

复制配置文件：

```powershell
copy .env.example .env
```

编辑 `.env`，填入 QQ 机器人凭据：

```env
QQ_BRIDGE_BOT_APP_ID=your_app_id_here
QQ_BRIDGE_BOT_APP_SECRET=your_app_secret_here
```

可选配置：

```env
QQ_BRIDGE_ALLOWED_USERS=
QQ_BRIDGE_ALLOWED_GROUPS=
QQ_BRIDGE_MESSAGE_CHUNK_SIZE=1800
QQ_BRIDGE_MARKDOWN_ENABLED=true
QQ_BRIDGE_MARKDOWN_FALLBACK_TO_TEXT=true
```

`QQ_BRIDGE_ALLOWED_USERS` 和 `QQ_BRIDGE_ALLOWED_GROUPS` 留空表示允许所有能触达机器人的 QQ 用户或群。给机器人发送 `/id` 可以查看当前 `user_openid` 和 `group_openid`。

## 手动启动

先注册 MCP server：

```powershell
claude mcp add -s user qq-bridge -- python -m qq_bridge mcp
```

然后在你的业务项目里启动 Claude Code：

```powershell
cd "C:\path\to\your\project"
claude --dangerously-load-development-channels server:qq-bridge
```

启动成功后，Claude Code 界面里应出现类似内容：

```text
Listening for channel messages from: server:qq-bridge
```

保持这个 Claude Code 窗口打开。之后 QQ 发来的消息会进入当前会话，Claude Code 会使用 `reply` 工具回复 QQ。

## 全局自动启动

如果你希望在任意项目里运行 `claude` 时自动接入 QQ，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-claude-autostart.ps1
```

安装脚本会做这些事：

- 执行 `pip install -e .`
- 注册用户级 MCP server：`qq-bridge`
- 在 PowerShell profile 中写入 `claude` 包装函数
- 在 `%USERPROFILE%\bin` 下安装 `claude.cmd`
- 把 `%USERPROFILE%\bin` 加到用户 `PATH` 最前面

之后重新打开一个 CMD 或 PowerShell，在任何项目里直接运行：

```powershell
claude
```

它会自动附加：

```powershell
--dangerously-load-development-channels server:qq-bridge
```

管理命令不会被包装，例如：

```powershell
claude --version
claude mcp list
claude plugin validate .
```

如果只想在本项目目录内自动接入 QQ：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-claude-autostart.ps1 -ProjectOnly
```

卸载自动启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-claude-autostart.ps1 -Uninstall
```

## QQ 侧命令

给机器人发送：

```text
/id
```

查看当前会话标识，用于 allowlist 配置。

```text
/status
```

查看 bridge、QQ bot 和队列状态。

```text
/help
```

查看内置命令。

## Markdown 回复

Claude Code 通过 `reply` 或 `send` 工具发往 QQ 的内容默认按 QQ Markdown 发送：

```json
{
  "msg_type": 2,
  "markdown": {
    "content": "# 标题\n\n**加粗内容**"
  }
}
```

如果 QQ 平台或机器人权限不允许 Markdown，bridge 会自动降级为普通文本。可以通过环境变量关闭：

```env
QQ_BRIDGE_MARKDOWN_ENABLED=false
```

## 验证

```powershell
python -m compileall qq_bridge
claude mcp list
```

`claude mcp list` 中应看到：

```text
qq-bridge: python -m qq_bridge mcp - ✓ Connected
```

## 常见问题

### QQ 发消息后 Claude Code 没反应

确认当前 Claude Code 是用 channel 启动的：

```text
Listening for channel messages from: server:qq-bridge
```

如果没有这行，退出 Claude Code 后重新启动。

### 提示 server 不在 approved channels allowlist

启动命令需要使用：

```powershell
claude --dangerously-load-development-channels server:qq-bridge
```

如果你安装了自动启动脚本，关闭旧终端，重新打开 CMD 或 PowerShell 后再运行 `claude`。

### QQ Markdown 没有渲染

查看日志里是否有 `falling back to text`。如果有，说明 QQ 平台拒绝了 Markdown 消息，通常是机器人权限或平台限制导致的。

日志路径默认是：

```text
logs/qq_bridge.log
```

### VS Code 里的 Claude Code 没接入 QQ

VS Code 扩展可能直接调用自己的 Claude Code 二进制，不一定经过本项目安装的 `claude.cmd` 包装器。建议用独立 CMD 或 PowerShell 启动 `claude`。

## 开源前注意

不要提交这些本机私有文件：

- `.env`
- `data/`
- `logs/`
- `botpy.log`
- `qq_bridge.egg-info/`
- 任何包含本机绝对路径、QQ OpenID、App Secret、会话日志的文件

`.gitignore` 已经忽略了常见密钥、日志、数据库和构建产物。发布前仍建议执行：

```powershell
git status --short
```

确认没有把密钥或本机运行数据加入 Git。

## 安全说明

QQ 消息会作为外部输入进入 Claude Code 当前会话。请把 QQ 消息视为不可信输入，尤其不要让陌生 QQ 用户直接控制你的本地项目。

建议：

- 配置 `QQ_BRIDGE_ALLOWED_USERS`
- 群聊场景配置 `QQ_BRIDGE_ALLOWED_GROUPS`
- 不要把机器人加入不可信群
- 不要在公开环境中暴露 `.env`
- 重要项目中谨慎开启 bypass permissions

## 入口说明

通常不需要手动运行下面两个入口，Claude Code 会在加载 MCP server 时自动启动：

```powershell
python -m qq_bridge mcp
python run.py
```

它们只是 MCP server entrypoint，不是给 QQ 消息单独启动的 Agent。
