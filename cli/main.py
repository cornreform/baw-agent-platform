"""baw CLI — Purple+Gold multi-layered terminal interface."""
from __future__ import annotations
import sys
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from cli import console  # shared themed console

BAW_HOME = Path.home() / ".baw"

# ── ASCII Art Banner ──
BANNER = """[baw.brand]
    ██████╗  █████╗ ██╗    ██╗
    ██╔══██╗██╔══██╗██║    ██║
    ██████╔╝███████║██║ █╗ ██║
    ██╔══██╗██╔══██╗██║███╗██║
    ██████╔╝██║  ██║╚███╔███╔╝
    ╚═════╝ ╚═╝  ╚═╝ ╚══╝╚══╝[/baw.brand]
[baw.gold]    Black And White — Agent Platform[/baw.gold]
"""

CHAT_BANNER = """[baw.brand]
 ██████╗  █████╗ ██╗    ██╗    ██████╗██╗  ██╗ █████╗ ████████╗
 ██╔══██╗██╔══██╗██║    ██║   ██╔════╝██║  ██║██╔══██╗╚══██╔══╝
 ██████╔╝███████║██║ █╗ ██║   ██║     ███████║███████║   ██║
 ██╔══██╗██╔══██║██║███╗██║   ██║     ██╔══██║██╔══██║   ██║
 ██████╔╝██║  ██║╚███╔███╔╝   ╚██████╗██║  ██║██║  ██║   ██║
 ╚═════╝ ╚═╝  ╚═╝ ╚══╝╚══╝     ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝[/baw.brand]
"""

# ── Subcommand Registry ──
# Each entry: (command, description, category, aliases)
# Categories: interact, monitor, manage, system
COMMANDS = {
    "chat": {
        "desc": "💬  Enter interactive chat mode (default when no args)",
        "category": "interact",
        "aliases": [],
        "usage": "baw chat",
    },
    "status": {
        "desc": "🩺  Live health overview — uptime, sessions, memory, connectors",
        "category": "monitor",
        "aliases": ["health", "st"],
        "usage": "baw status",
    },
    "models": {
        "desc": "🤖  Model table + capability routing matrix",
        "category": "monitor",
        "aliases": ["model"],
        "usage": "baw models",
    },
    "config": {
        "desc": "📄  View/edit config.yaml + .env (masked secrets)",
        "category": "manage",
        "aliases": ["cfg"],
        "usage": "baw config [show|edit|get <key>|set <key> <val>]",
        "subcommands": ["show", "edit", "get", "set"],
    },
    "soul": {
        "desc": "🧠  Read/edit BAW's SOUL.md — core behavioral rules",
        "category": "manage",
        "aliases": ["personality"],
        "usage": "baw soul [show|edit]",
        "subcommands": ["show", "edit"],
    },
    "skill": {
        "desc": "📦  List/install/manage BAW skills",
        "category": "manage",
        "aliases": ["skills"],
        "usage": "baw skill [list|install <name>|remove <name>]",
        "subcommands": ["list", "install", "remove"],
    },
    "memory": {
        "desc": "🧩  Memory stats — size, entries, graph edges",
        "category": "monitor",
        "aliases": ["mem"],
        "usage": "baw memory",
    },
    "sessions": {
        "desc": "📋  Browse past session transcripts",
        "category": "monitor",
        "aliases": ["history", "sess"],
        "usage": "baw sessions [list|view <id>]",
        "subcommands": ["list", "view"],
    },
    "logs": {
        "desc": "📜  Tail Docker logs in real-time (live streaming)",
        "category": "monitor",
        "aliases": ["log"],
        "usage": "baw logs [--lines N]",
    },
    "dashboard": {
        "desc": "📊  Launch interactive TUI dashboard (Textual)",
        "category": "monitor",
        "aliases": ["dash", "tui"],
        "usage": "baw dashboard",
    },
    "restart": {
        "desc": "🔄  Restart the BAW Docker container",
        "category": "system",
        "aliases": ["reboot"],
        "usage": "baw restart",
    },
}

CATEGORY_META = {
    "interact": ("💬  Interact", "Chat with BAW"),
    "monitor": ("⚡  Monitor", "Health, models, logs, sessions"),
    "manage": ("🔧  Manage", "Config, soul, skills"),
    "system": ("⚙️  System", "Docker lifecycle"),
}


def _show_help():
    """Rich multi-section help display."""
    console.print()
    console.print(BANNER)
    console.print()

    # ── Section: Quick Start ──
    qs = Panel(
        "[baw.gold]baw[/baw.gold]       [baw.desc]→  Enter interactive chat[/baw.desc]\n"
        "[baw.gold]baw chat[/baw.gold]   [baw.desc]→  Explicit chat mode[/baw.desc]\n"
        "[baw.gold]baw --help[/baw.gold] [baw.desc]→  This help[/baw.desc]",
        title="[baw.gold]🚀  Quick Start[/baw.gold]",
        border_style="baw.accent",
        box=box.HEAVY,
        padding=(1, 3),
    )
    console.print(qs)
    console.print()

    # ── Categories ──
    for cat_key in ["interact", "monitor", "manage", "system"]:
        meta = CATEGORY_META[cat_key]
        cmds_in_cat = [(k, v) for k, v in COMMANDS.items() if v["category"] == cat_key]

        section_title = Text(f"  {meta[0]}  ", style="baw.section")
        section_subtitle = Text(f"  {meta[1]}  ", style="baw.dim")

        table = Table(
            box=box.SIMPLE_HEAVY,
            border_style="baw.accent",
            show_header=False,
            padding=(0, 2),
            expand=True,
            title=section_title,
            caption=section_subtitle,
            caption_justify="left",
        )
        table.add_column("CMD", style="baw.cmd", width=22, no_wrap=True)
        table.add_column("DESC", style="baw.desc")

        for cmd_name, cmd_info in cmds_in_cat:
            usage = cmd_info["usage"]
            desc = cmd_info["desc"]
            table.add_row(f"  {usage}", f"  {desc}")

        console.print(table)
        console.print()

    # ── Footer ──
    footer = Panel(
        "[baw.dim]In chat: type messages freely · /help for commands · /model to switch · /exit to quit[/baw.dim]\n"
        "[baw.dim]Config: ~/.baw/config.yaml · Secrets: ~/.baw/.env · Soul: ~/.baw/SOUL.md[/baw.dim]",
        border_style="baw.accent",
        box=box.HEAVY,
        padding=(0, 2),
    )
    console.print(footer)
    console.print()


def _resolve_cmd(raw: str) -> str | None:
    """Resolve aliases to canonical command name."""
    raw = raw.lower()
    if raw in COMMANDS:
        return raw
    for cmd_name, cmd_info in COMMANDS.items():
        if raw in cmd_info.get("aliases", []):
            return cmd_name
    return None


def main():
    # Handle --help / -h
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        sys.argv.pop(1)
        _show_help()
        return

    # Parse subcommand
    if len(sys.argv) < 2:
        # Default: interactive chat
        from cli.commands.chat import cmd_chat
        cmd_chat()
        return

    cmd = sys.argv[1]

    # Resolve aliases
    canonical = _resolve_cmd(cmd)
    if canonical is None:
        console.print(f"[baw.error]Unknown command:[/baw.error] {cmd}")
        console.print("[baw.dim]Run [baw.gold]baw --help[/baw.gold] to see available commands.[/baw.dim]")
        return

    # Extract subcommand and remaining args
    subcommand = sys.argv[2] if len(sys.argv) > 2 else None
    args = sys.argv[3:] if len(sys.argv) > 3 else []

    # Route
    try:
        if canonical == "chat":
            from cli.commands.chat import cmd_chat
            cmd_chat()
        elif canonical == "status":
            from cli.commands.status import cmd_status
            cmd_status()
        elif canonical == "models":
            from cli.commands.models import cmd_models
            cmd_models(subcommand, args)
        elif canonical == "config":
            from cli.commands.config_cmd import cmd_config
            cmd_config(subcommand, args)
        elif canonical == "soul":
            from cli.commands.soul import cmd_soul
            cmd_soul(subcommand)
        elif canonical == "logs":
            from cli.commands.logs import cmd_logs
            cmd_logs()
        elif canonical == "dashboard":
            from cli.commands.dashboard import cmd_dashboard
            cmd_dashboard()
        elif canonical == "restart":
            from cli.commands.restart import cmd_restart
            cmd_restart()
        elif canonical == "skill":
            from cli.commands.skill_cmd import cmd_skill
            cmd_skill(subcommand, args)
        elif canonical == "memory":
            from cli.commands.memory_cmd import cmd_memory
            cmd_memory()
        elif canonical == "sessions":
            from cli.commands.sessions_cmd import cmd_sessions
            cmd_sessions(subcommand, args)
    except KeyboardInterrupt:
        console.print("\n[baw.dim]👋 Bye.[/baw.dim]")
    except Exception as e:
        console.print(f"[baw.error]Error:[/baw.error] {e}")


if __name__ == "__main__":
    main()
