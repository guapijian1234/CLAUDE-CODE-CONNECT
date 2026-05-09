"""Run the QQ channel MCP server.

Claude Code normally starts this process through the plugin or .mcp.json.
Running it directly is useful only for MCP/stdin debugging.
"""

from qq_bridge.server import serve


if __name__ == "__main__":
    serve()
