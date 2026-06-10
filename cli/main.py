"""baw CLI — Purple+Gold multi-layered terminal interface.
Run `baw` to chat, `baw --help` for full reference.
"""
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

# ── Subcommand Registry ──
# (command, short_desc, long_desc, example, category, aliases, subcommands)
COMMANDS: dict[str, dict] = {
    "chat": {
        "short": "💬  Interactive chat with BAW",
        "long": (
            "Open a REPL session with BAW's AI. Type messages freely —\n"
            "BAW responds in Traditional Chinese with streaming output.\n"
            "Slash commands available: /help /model /soul /config /clear /exit."
        ),
        "example": "baw chat",
        "category": "interact",
        "aliases": [],
        "usage": "baw [chat]",
    },
    "status": {
        "short": "🩺  System health overview",
        "long": (
            "Show live status of BAW platform: container uptime,\n"
            "active model, session count, memory usage, and all\n"
            "messaging connectors with their connection state."
        ),
        "example": "baw status",
        "category": "monitor",
        "aliases": ["health", "st"],
        "usage": "baw status",
    },
    "models": {
        "short": "🤖  AI model catalogue + capability routing",
        "long": (
            "List all configured AI models across providers, showing\n"
            "context windows, vision support, and which model is default.\n"
            "Also displays the capability routing matrix (chat → model,\n"
            "vision → model, TTS → model, etc.)."
        ),
        "example": "baw models",
        "category": "monitor",
        "aliases": ["model"],
        "usage": "baw models",
    },
    "config": {
        "short": "📄  View/edit BAW configuration",
        "long": (
            "Read or modify ~/.baw/config.yaml and ~/.baw/.env.\n"
            "Secrets in .env are masked with ***. Supports subcommands:\n"
            "  show — display current config\n"
            "  edit — open in $EDITOR\n"
            "  get <key> — read a single value\n"
            "  set <key> <val> — write a single value"
        ),
        "example": "baw config show",
        "category": "manage",
        "aliases": ["cfg"],
        "usage": "baw config [show|edit|get <k>|set <k> <v>]",
        "subcommands": ["show", "edit", "get", "set"],
    },
    "soul": {
        "short": "🧠  Read/edit BAW's core identity",
        "long": (
            "View or edit ~/.baw/SOUL.md — the behavioural rulebook\n"
            "that defines BAW's personality, decision logic, tone\n"
            "profiles, fact-check mode, and hard gates."
        ),
        "example": "baw soul show",
        "category": "manage",
        "aliases": ["personality"],
        "usage": "baw soul [show|edit]",
        "subcommands": ["show", "edit"],
    },
    "skill": {
        "short": "📦  Manage BAW skills",
        "long": (
            "List, install, or remove BAW skill files stored in\n"
            "~/.baw/skills/. Skills are YAML task definitions that\n"
            "BAW can execute autonomously."
        ),
        "example": "baw skill list",
        "category": "manage",
        "aliases": ["skills"],
        "usage": "baw skill [list|install <n>|remove <n>]",
        "subcommands": ["list", "install", "remove"],
    },
    "memory": {
        "short": "🧩  Memory store statistics",
        "long": (
            "Show BAW's memory stats: total entries, storage size,\n"
            "average memory score, and score distribution across\n"
            "high / medium / low buckets."
        ),
        "example": "baw memory",
        "category": "monitor",
        "aliases": ["mem"],
        "usage": "baw memory",
    },
    "sessions": {
        "short": "📋  Browse past chat sessions",
        "long": (
            "List recent sessions with message counts and timestamps.\n"
            "Use 'view <id>' to replay a full session transcript."
        ),
        "example": "baw sessions list",
        "category": "monitor",
        "aliases": ["history", "sess"],
        "usage": "baw sessions [list|view <id>]",
        "subcommands": ["list", "view"],
    },
    "logs": {
        "short": "📜  Live Docker log stream",
        "long": (
            "Tail BAW container logs in real time. Equivalent to\n"
            "'docker logs -f baw-telegram' with optional line limit."
        ),
        "example": "baw logs --lines 50",
        "category": "monitor",
        "aliases": ["log"],
        "usage": "baw logs [--lines N]",
    },
    "dashboard": {
        "short": "📊  Interactive TUI dashboard",
        "long": (
            "Launch a full-screen Textual TUI with live-updating\n"
            "panels: system health, model catalogue, connectors,\n"
            "session browser, memory stats, and activity feed.\n"
            "Auto-refreshes every 5 seconds."
        ),
        "example": "baw dashboard",
        "category": "monitor",
        "aliases": ["dash", "tui"],
        "usage": "baw dashboard",
    },
    "tui-chat": {
        "short": "💬 TUI chat with persistent status bar",
        "long": (
            "Full-screen Textual chat interface with persistent\n"
            "status bar showing model, provider, token usage,\n"
            "context window %, tone, and fact-check mode.\n"
            "Like Hermes CLI but with BAW identity (purple+gold).\n"
            "Supports web_search tool calling."
        ),
        "example": "baw tui-chat",
        "category": "interact",
        "aliases": ["tchat", "tc"],
        "usage": "baw tui-chat",
    },
    "restart": {
        "short": "🔄  Restart the BAW container",
        "long": (
            "Restart the baw-telegram Docker container gracefully.\n"
            "Use after config changes that require a full reload."
        ),
        "example": "baw restart",
        "category": "system",
        "aliases": ["reboot"],
        "usage": "baw restart",
    },
}

CATEGORY_META = {
    "interact": ("💬  Interact", "Chat with BAW", "baw.gold"),
    "monitor":   ("⚡  Monitor",   "Health, models, logs, sessions, dashboard", "baw.purple"),
    "manage":    ("🔧  Manage",    "Config, soul, skills", "baw.purple"),
    "system":    ("⚙️  System",    "Docker lifecycle", "baw.accent"),
}


def _show_help():
    """Rich multi-section help display."""
    console.print()
    console.print(BANNER)
    console.print()

    # ── Quick Start ──
    qs = Panel(
        "[baw.gold]baw[/baw.gold]           [baw.desc]→  Enter interactive chat (default)[/baw.desc]\n"
        "[baw.gold]baw chat[/baw.gold]       [baw.desc]→  Open chat REPL with streaming AI responses[/baw.desc]\n"
        "[baw.gold]baw --help[/baw.gold]     [baw.desc]→  Show this reference[/baw.desc]",
        title="[baw.gold]🚀  Quick Start[/baw.gold]",
        border_style="baw.accent",
        box=box.HEAVY,
        padding=(1, 3),
    )
    console.print(qs)
    console.print()

    # ── Categories ──
    for cat_key in ["interact", "monitor", "manage", "system"]:
        title_text, subtitle_text, accent_style = CATEGORY_META[cat_key]
        cmds_in_cat = [(k, v) for k, v in COMMANDS.items() if v["category"] == cat_key]

        table = Table(
            box=box.SIMPLE_HEAVY,
            border_style="baw.accent",
            show_header=False,
            padding=(1, 2),
            expand=True,
            title=Text(f"  {title_text}  ", style=accent_style),
            caption=Text(f"  {subtitle_text}  ", style="baw.dim"),
            caption_justify="left",
        )
        table.add_column("", style="baw.cmd", width=24, no_wrap=True)
        table.add_column("", style="baw.desc")

        for cmd_name, cmd_info in cmds_in_cat:
            short = cmd_info["short"]
            example = cmd_info["example"]
            table.add_row(f"  {example}", f"  {short}")
            # Add long description as indented continuation
            long = cmd_info.get("long", "")
            for line in long.strip().split("\n"):
                table.add_row("", f"  [baw.dim]{line}[/]")

        console.print(table)
        console.print()

    # ── Files reference ──
    files_ref = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    files_ref.add_column(style="baw.key", width=24, no_wrap=True)
    files_ref.add_column(style="baw.dim")
    files_ref.add_row("~/.baw/config.yaml", "Main configuration (models, providers, tone)")
    files_ref.add_row("~/.baw/.env", "API keys and secrets (masked in console)")
    files_ref.add_row("~/.baw/SOUL.md", "BAW's identity and behavioural rules")
    files_ref.add_row("~/.baw/sessions/", "Past chat transcripts (JSONL)")
    files_ref.add_row("~/.baw/memory/", "Persistent memory store")
    files_ref.add_row("~/.baw/skills/", "Autonomous task definitions (YAML)")

    footer = Panel(
        files_ref,
        title="[baw.gold]📁  Key Files[/baw.gold]",
        border_style="baw.accent",
        box=box.HEAVY,
        padding=(1, 2),
    )
    console.print(footer)

    # ── Slash commands (in-chat) ──
    slash = Table(box=None, show_header=False, padding=(0, 2), expand=True)
    slash.add_column(style="baw.cmd", width=14, no_wrap=True)
    slash.add_column(style="baw.dim", width=30)
    slash.add_column(style="baw.cmd", width=14, no_wrap=True)
    slash.add_column(style="baw.dim", width=30)
    slash.add_row("/help", "Show in-chat help", "/model", "Switch active model")
    slash.add_row("/soul", "View SOUL.md", "/config", "View config")
    slash.add_row("/session", "Session info", "/clear", "Reset chat")
    slash.add_row("/exit", "Quit chat", "", "")

    console.print()
    console.print(Panel(
        slash,
        title="[baw.gold]⌨   In-Chat Commands[/baw.gold]",
        border_style="baw.accent",
        box=box.HEAVY,
        padding=(1, 2),
    ))
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
        elif canonical == "tui-chat":
            from cli.commands.tui_chat import cmd_tui_chat
            cmd_tui_chat()
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
