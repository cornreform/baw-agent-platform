"""BAW CLI — unified entry point.

    baw                  → interactive chat (default)
    baw chat             → explicit chat mode
    baw status           → health overview
    baw models           → model table
    baw config [show]    → view config
    baw logs             → tail Docker logs
    baw soul             → view SOUL.md
    baw dashboard        → Textual TUI dashboard
    baw restart          → restart container
    baw --help / -h      → this help
"""

from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

# ── resolve BAW root ──────────────────────────────────────────────
BAW_ROOT = Path(__file__).resolve().parent.parent
if str(BAW_ROOT) not in sys.path:
    sys.path.insert(0, str(BAW_ROOT))

# ── subcommand registry ────────────────────────────────────────────
SUBCOMMANDS = {
    "chat":        "cli.commands.chat:cmd_chat",
    "status":      "cli.commands.status:cmd_status",
    "models":      "cli.commands.models:cmd_models",
    "config":      "cli.commands.config_cmd:cmd_config",
    "logs":        "cli.commands.logs:cmd_logs",
    "soul":        "cli.commands.soul:cmd_soul",
    "dashboard":   "cli.commands.dashboard:cmd_dashboard",
    "restart":     "cli.commands.restart:cmd_restart",
}


def _load_command(name: str):
    """Lazy-load a subcommand module."""
    mod_path, func_name = SUBCOMMANDS[name].split(":")
    import importlib
    mod = importlib.import_module(mod_path)
    return getattr(mod, func_name)


def main():
    parser = argparse.ArgumentParser(
        prog="baw",
        description="BAW Agent Platform — CLI",
        add_help=False,
    )
    parser.add_argument("subcommand", nargs="?", default="chat",
                        choices=list(SUBCOMMANDS.keys()),
                        help="What to do (default: chat)")
    parser.add_argument("args", nargs=argparse.REMAINDER,
                        help="Extra args passed to subcommand")
    parser.add_argument("-h", "--help", action="store_true",
                        help="Show help")
    parser.add_argument("--version", action="store_true",
                        help="Show version")

    args = parser.parse_args()

    if args.help:
        _print_help()
        return
    if args.version:
        from pathlib import Path
        try:
            v = (Path.home() / ".baw" / "VERSION").read_text().strip()
        except Exception:
            v = "dev"
        print(f"baw v{v}")
        return

    # Dispatch
    cmd = args.subcommand
    func = _load_command(cmd)
    sys.argv = [f"baw {cmd}"] + args.args
    func()


def _print_help():
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box

    c = Console()
    # Brand header
    c.print()
    c.print(Panel.fit(
        "[bold white on #6c3fc9]  🖤  BAW Agent Platform  [/bold white on #6c3fc9]",
        border_style="#6c3fc9",
        padding=(0, 3),
    ))
    c.print()

    table = Table(box=box.ROUNDED, border_style="#6c3fc9", show_header=False,
                  pad_edge=True, expand=False)
    table.add_column("Command", style="bold #c4a0ff", width=18)
    table.add_column("Description", style="#b0b8c0")

    table.add_row("baw", "[dim]#[/dim] [white]Interactive chat[/white] [dim](default)[/dim]")
    table.add_row("baw chat", "[dim]#[/dim] Explicit chat mode")
    table.add_row("baw status", "[dim]#[/dim] 🩺  Health overview")
    table.add_row("baw models", "[dim]#[/dim] 🤖  Model table + routing")
    table.add_row("baw config", "[dim]#[/dim] 📄  View config.yaml")
    table.add_row("baw logs", "[dim]#[/dim] 📜  Tail Docker logs (live)")
    table.add_row("baw soul", "[dim]#[/dim] 🧠  View SOUL.md")
    table.add_row("baw dashboard", "[dim]#[/dim] 📊  Live TUI dashboard")
    table.add_row("baw restart", "[dim]#[/dim] 🔄  Restart container")

    c.print(table)
    c.print()
    from rich.text import Text
    hint = Text("💡 In chat mode: type freely · ")
    hint.append("/model", style="bold #c4a0ff")
    hint.append(" to switch · ")
    hint.append("/clear", style="bold #c4a0ff")
    hint.append(" reset · ")
    hint.append("/exit", style="bold #c4a0ff")
    hint.append(" quit", style="dim")
    c.print(hint)
    c.print()


if __name__ == "__main__":
    main()
