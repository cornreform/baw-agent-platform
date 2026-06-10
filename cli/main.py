#!/usr/bin/env python3
"""
BAW CLI — main entry point.

Usage:
  baw status          Show bot health + connector status
  baw config show     View current config
  baw config edit     Open config in $EDITOR
  baw config get KEY  Get a specific config value
  baw config set KEY VAL  Set a config value
  baw models list     List available models
  baw logs [--follow] View bot logs
  baw soul show       Show SOUL.md personality
  baw soul edit       Edit SOUL.md
  baw dashboard       Live TUI dashboard
  baw restart         Restart the BAW bot
  baw --help          Show this help
"""
from __future__ import annotations
import sys
import argparse
from pathlib import Path

# Rich imports
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.syntax import Syntax
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    print("⚠️  Rich not installed. Run: pip install rich")
    sys.exit(1)

from cli import console, BAW_LOGO

BAW_HOME = Path.home() / ".baw"
APP_DIR = Path(__file__).resolve().parent.parent


def show_help():
    """Display beautiful help."""
    console.print(BAW_LOGO)
    console.print()

    table = Table(title="📋 BAW Commands", box=box.ROUNDED, border_style="baw.border")
    table.add_column("Command", style="baw.highlight", no_wrap=True)
    table.add_column("Description", style="baw.value")

    table.add_row("baw status", "Bot health + connector status + uptime")
    table.add_row("baw config show", "View current configuration (with highlighting)")
    table.add_row("baw config edit", "Open config.yaml in $EDITOR")
    table.add_row("baw config get <key>", "Get a specific config value")
    table.add_row("baw config set <key> <val>", "Set a config value")
    table.add_row("baw models list", "List all configured models")
    table.add_row("baw logs", "View recent bot logs")
    table.add_row("baw logs --follow", "Tail live logs")
    table.add_row("baw soul show", "Show BAW's SOUL.md personality")
    table.add_row("baw soul edit", "Edit SOUL.md in $EDITOR")
    table.add_row("baw dashboard", "Live TUI dashboard")
    table.add_row("baw restart", "Restart the BAW bot (Docker container)")

    console.print(table)
    console.print()
    console.print("[baw.muted]BAW data dir:[/baw.muted] [baw.key]{0}[/baw.key]".format(BAW_HOME))
    console.print("[baw.muted]GitHub:[/baw.muted] [baw.key]cornreform/baw-agent-platform[/baw.key]")


def main():
    parser = argparse.ArgumentParser(
        prog="baw",
        description="BAW Agent Platform CLI",
        add_help=False,
    )
    parser.add_argument("command", nargs="?", default="chat")
    parser.add_argument("subcommand", nargs="?", default=None)
    parser.add_argument("args", nargs="*", default=[])
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("--follow", "-f", action="store_true")

    args, unknown = parser.parse_known_args()

    if args.help or args.command == "help":
        show_help()
        return

    # Route commands
    cmd = args.command.lower()

    if cmd == "status":
        from cli.commands.status import cmd_status
        cmd_status()

    elif cmd == "config":
        from cli.commands.config_cmd import cmd_config
        cmd_config(args.subcommand, args.args)

    elif cmd == "models":
        from cli.commands.models import cmd_models
        cmd_models(args.subcommand, args.args)

    elif cmd == "logs":
        from cli.commands.logs import cmd_logs
        cmd_logs(follow=args.follow)

    elif cmd == "soul":
        from cli.commands.soul import cmd_soul
        cmd_soul(args.subcommand)

    elif cmd == "dashboard":
        try:
            from cli.commands.dashboard import cmd_dashboard
            cmd_dashboard()
        except ImportError:
            console.print("[baw.error]Textual not installed.[/baw.error] Run: pip install textual")
            console.print("[baw.muted]Dashboard requires textual for TUI mode.[/baw.muted]")

    elif cmd == "restart":
        from cli.commands.restart import cmd_restart
        cmd_restart()

    elif cmd == "chat":
        from cli.commands.chat import cmd_chat
        cmd_chat(args.subcommand)

    else:
        console.print(f"[baw.error]Unknown command:[/baw.error] {cmd}")
        console.print("[baw.muted]Run 'baw --help' to see available commands.[/baw.muted]")


if __name__ == "__main__":
    main()