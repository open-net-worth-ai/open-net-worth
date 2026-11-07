"""`onw` — the command line of an installed Open Net Worth.

`uv tool install open-net-worth` (or `make setup` in a checkout, which installs the package in
editable mode) puts this on the PATH. It sits at the very top of the import stack: it may import
anything below it (backend.main included); nothing below may import it.

    onw start [--open] [--background] [--autostart] [--auto-upgrade]
                                         run the app — API + built frontend, one process
    onw stop                             stop it
    onw restart                          restart it, the way it was running
    onw status                           whether it's running, its URL, version and folder
    onw logs [--follow] [--lines N]      show the end of its log
    onw autostart install|uninstall|status
                                         start at login/boot and restart on crash
    onw upgrade [--check]                update to the newest release
    onw reset-password                   set a new password when the old one is forgotten
    onw mcp                              stdio MCP bridge an AI app launches itself
    onw version                          print the installed version
    onw help [command]                   show this list, or one command's options

Exit codes: 0 worked, 1 failed (`onw status`: not running), 2 wrong usage, 130 Ctrl-C.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

_WINDOWS_TASK_COMMAND = "autostart-run"
_INTERRUPTED = 130  # 128 + SIGINT, what a shell reports for Ctrl-C


class _ListFormatter(argparse.HelpFormatter):
    """Drops the bare `<command>` row argparse prints above the list of commands."""

    def _format_action(self, action: argparse.Action) -> str:
        text = super()._format_action(action)
        return text.split("\n", 1)[1] if action.nargs == argparse.PARSER else text


def build_parser() -> argparse.ArgumentParser:
    from backend.core.version import APP_VERSION

    parser = argparse.ArgumentParser(
        prog="onw",
        description="Open Net Worth — self-hosted personal finance with an AI assistant.",
        epilog="Run `onw help <command>` to see a command's options.",
        formatter_class=_ListFormatter,
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"open-net-worth {APP_VERSION}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", title="commands")

    start = sub.add_parser("start", help="run the app")
    start.add_argument("--host", help="bind address (default: HOST env, else 127.0.0.1)")
    start.add_argument("--port", type=int, help="port (default: PORT env, else 8080)")
    start.add_argument(
        "--open", action="store_true", help="open the app in your browser once it is up"
    )
    start.add_argument(
        "--background",
        action="store_true",
        help="free this terminal and keep it running until `onw stop` or a reboot",
    )
    start.add_argument(
        "--autostart",
        action="store_true",
        help="run as a service that starts at login/boot and restarts on crash, not in this terminal",
    )
    start.add_argument(
        "--auto-upgrade",
        action="store_true",
        help="install new releases automatically and restart when idle (a `uv tool` install only)",
    )
    start.add_argument("--service", action="store_true", help=argparse.SUPPRESS)  # the boot service

    sub.add_parser("stop", help="stop the app")
    sub.add_parser("restart", help="restart the app, the way it was running")
    sub.add_parser("status", help="show whether it's running, its URL, version and folder")
    logs = sub.add_parser("logs", help="show the end of the app's log")
    logs.add_argument("-f", "--follow", action="store_true", help="keep showing what it adds")
    logs.add_argument("-n", "--lines", type=int, default=50, help="how many lines (default: 50)")

    auto = sub.add_parser("autostart", help="start the app at login/boot and restart it on a crash")
    # `stop` is the old name for `uninstall`, and still works.
    auto.add_argument(
        "action",
        choices=("install", "uninstall", "status", "stop"),
        metavar="{install,uninstall,status}",
    )

    reset = sub.add_parser(
        "reset-password",
        help="set a new password, on the machine that holds the book",
    )
    reset.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the new password from one line of stdin instead of prompting",
    )
    mcp = sub.add_parser(
        "mcp",
        help="connect an AI app to your book (the app runs this)",
    )
    mcp.add_argument(
        "--base-url",
        help="this instance's address (default: ONW_BASE_URL, else the running instance's port)",
    )
    upgrade = sub.add_parser("upgrade", help="update to the newest release")
    upgrade.add_argument(
        "--check", action="store_true", help="only report whether a newer release is out"
    )
    sub.add_parser("version", help="print the installed version")
    show_help = sub.add_parser("help", help="show this list, or one command's options")
    show_help.add_argument("topic", nargs="?", metavar="command", help="the command to explain")
    return parser


def _use_utf8_stdio() -> None:
    """Windows consoles and pipes default to a legacy code page, and a windowless ``pythonw``
    has no stdio at all: print and logging must work with either."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
        elif hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_stdio()
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments == [_WINDOWS_TASK_COMMAND]:  # not for people: on no list, in no error message
            from backend.cli.autostart_windows import run_service

            return run_service()
        return _run(build_parser(), arguments)
    except KeyboardInterrupt:
        return _INTERRUPTED


def _run(parser: argparse.ArgumentParser, arguments: list[str]) -> int:
    args = parser.parse_args(arguments)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "help":
        # Exits, as `--help` does. An unknown command gets argparse's list of the real ones.
        parser.parse_args([args.topic, "--help"] if args.topic else ["--help"])
    if args.command == "version":
        from backend.core.version import APP_VERSION

        print(f"open-net-worth {APP_VERSION}")
        return 0
    if args.command == "start":
        from backend.cli.serve import start

        return start(
            host=args.host,
            port=args.port,
            open_browser=args.open,
            autostart=args.autostart,
            auto_upgrade=args.auto_upgrade,
            service=args.service,
            background=args.background,
        )
    if args.command == "stop":
        from backend.cli.background import stop

        return stop()
    if args.command == "restart":
        from backend.cli.background import restart

        return restart()
    if args.command == "logs":
        from backend.cli.background import logs as show_logs

        return show_logs(follow=args.follow, lines=args.lines)
    if args.command == "status":
        from backend.cli.status import run_status

        return run_status()
    if args.command == "reset-password":
        from backend.cli.reset_password import run_reset_password

        return run_reset_password(password_stdin=args.password_stdin)
    if args.command == "mcp":
        from backend.cli.mcp import run_mcp

        return run_mcp(base_url=args.base_url)
    if args.command == "upgrade":
        from backend.cli.self_update import check, upgrade

        return check() if args.check else upgrade()
    from backend.cli.autostart import run_autostart

    return run_autostart(args.action)
