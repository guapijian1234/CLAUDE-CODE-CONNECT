"""Command line entrypoint used by Claude Code MCP."""

from __future__ import annotations

import argparse


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
        from .fast_hook import main as hook_main

        hook_main()
    elif args.command == "install-hooks":
        from .hook import install_hooks

        install_hooks()
    elif args.command == "uninstall-hooks":
        from .hook import uninstall_hooks

        uninstall_hooks()
    else:
        from .server import serve

        serve()


if __name__ == "__main__":
    main()
