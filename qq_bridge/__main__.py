"""Command line entrypoint used by Claude Code MCP."""

from __future__ import annotations

import argparse

from .hook import install_hooks, main as hook_main, uninstall_hooks
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m qq_bridge")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["mcp", "hook", "install-hooks", "uninstall-hooks"],
        default="mcp",
        help="run the QQ channel MCP server or helper commands",
    )
    args = parser.parse_args()
    if args.command == "hook":
        hook_main()
    elif args.command == "install-hooks":
        install_hooks()
    elif args.command == "uninstall-hooks":
        uninstall_hooks()
    else:
        serve()


if __name__ == "__main__":
    main()
