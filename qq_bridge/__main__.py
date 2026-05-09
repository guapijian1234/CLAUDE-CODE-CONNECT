"""Command line entrypoint used by Claude Code MCP."""

from __future__ import annotations

import argparse

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m qq_bridge")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["mcp"],
        default="mcp",
        help="run the QQ channel MCP server",
    )
    parser.parse_args()
    serve()


if __name__ == "__main__":
    main()
