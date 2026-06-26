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
BANNER = """[baw.brand]██████╗ [/][baw.gold] █████╗ [/][baw.brand]██╗    ██╗[/]
[baw.brand]██╔══██╗[/][baw.gold]██╔══██╗[/][baw.brand]██║    ██║[/]
[baw.brand]██████╔╝[/][baw.gold]███████║[/][baw.brand]██║ █╗ ██║[/]
[baw.brand]██╔══██╗[/][baw.gold]██╔══██╗[/][baw.brand]██║███╗██║[/]
[baw.brand]██████╔╝[/][baw.gold]██║  ██║[/][baw.brand]╚███╔███╔╝[/]
[baw.brand]╚═════╝ [/][baw.gold]╚═╝  ╚═╝[/][baw.brand] ╚══╝╚══╝[/]
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
    "router": {
        "short": "🎯  View/edit tier → model routing preferences",
        "long": (
            "Decide which model handles each complexity tier (trivial,\n"
            "moderate, complex, expert). The router picks the FIRST model\n"
            "in your preference list that's available. You make the call —\n"
            "the code does not judge which model is 'best'.\n"
            "  show — display current preferences + availability\n"
            "  set <tier> <model> — replace the tier's preference list\n"
            "  append <tier> <model> — add a fallback model to a tier\n"
            "  reset — revert to defaults"
        ),
        "example": "baw router set expert kimi-k2.6",
        "category": "manage",
        "aliases": ["tier", "routing"],
        "usage": "baw router [show|set|append|reset]",
        "subcommands": ["show", "set", "append", "reset"],
    },
    "court": {
        "short": "⚖️  黑白法庭 — 查案件、status、夜報",
        "long": (
            "黑白法庭係 BAW 嘅核心 metaphor — 每單任務都係一單案,\n"
            "由檢察官(Devil)挑剔、被告執行、法官評分。\n"
            "  nightly  — 上一日 24h 嘅審案摘要 (Telegram 推送)\n"
            "  docket   — 現時排程狀態 (queue / running / done)\n"
            "  pickup   — 恢復因 crash 殘留嘅 running case"
        ),
        "example": "baw court nightly",
        "category": "manage",
        "aliases": [],
        "usage": "baw court [nightly|docket|pickup]",
        "subcommands": ["nightly", "docket", "pickup"],
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
    "setup": {
        "short": "⚡  Interactive setup wizard",
        "long": (
            "Walk through configuration step by step:\\n"
            "  • Default model selection\\n"
            "  • Provider API keys (Stepfun, MiniMax, etc.)\\n"
            "  • TTS / STT / Vision capabilities\\n"
            "  • Tone profile\\n"
            "Runs inside the container (needs TTY)."
        ),
        "example": "baw setup",
        "category": "manage",
        "aliases": [],
        "usage": "baw setup",
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
    "todo": {
        "short": "📋  Persistent todos, thoughts, and follow-ups",
        "long": (
            "Manage the persistent todo / thought / follow-up system.\n"
            "Three item types: task (checklist), thought (self-reflection,\n"
            "always visible), followup (scheduled for a future session,\n"
            "surfaced at boot). Persists across restarts and sessions."
        ),
        "example": "baw todo surface",
        "category": "monitor",
        "aliases": ["todos"],
        "usage": "baw todo [list|surface|add|thought|followup|done|cancel|remove|stats]",
        "subcommands": ["list", "surface", "add", "thought", "followup",
                        "done", "cancel", "remove", "stats"],
    },
    "self-test": {
        "short": "🧪  End-to-end smoke test of self-build pipeline",
        "long": (
            "Exercise BAW's self-build recipe end-to-end: path resolution,\n"
            "tool registry, HTTP fetch, dataset write, read-back. Use this\n"
            "after any self-build task to confirm BAW can still locate its\n"
            "own files, register tools, and reach the network."
        ),
        "example": "baw self-test",
        "category": "monitor",
        "aliases": ["smoke", "selftest"],
        "usage": "baw self-test [--url URL] [--paths-only] [--no-fetch]",
        "uses_argparse": True,
    },
    "preflight": {
        "short": "🛫  Capability pre-flight check (run BEFORE any scrape task)",
        "long": (
            "Step 0 of SELF_BUILD_RECIPE. Verifies BAW has the tools, network,\n"
            "disk, and path resolution it needs to start a 'scrape / build me\n"
            "a tool' task. Warns about known SPA hosts. Refuses to start\n"
            "(exits 1) when any check is BLOCKED. Run before any self-build."
        ),
        "example": "baw preflight https://example.com",
        "category": "monitor",
        "aliases": ["check"],
        "usage": "baw preflight [--url URL] [--json]",
        "uses_argparse": True,
    },
    "petrestaurants": {
        "short": "🐾  HK FEHD pet-friendly restaurant query (49/1000)",
        "long": (
            "Query the 2026-06-12 FEHD lottery result for HK pet-friendly\n"
            "restaurants. Built 2026-06-12 as proof of SELF_BUILD_RECIPE.\n"
            "Subcommands: list, stats, district, region, nearest, search."
        ),
        "example": "baw petrestaurants district 灣仔",
        "category": "data",
        "aliases": ["pet"],
        "usage": "baw petrestaurants [list|stats|district|region|nearest|search]",
        "subcommands": ["list", "stats", "district", "region", "nearest", "search"],
    },
    "restaurant": {
        "short": "🍴  Restaurant finder via OpenStreetMap (free, no API key)",
        "long": (
            "Find restaurants via OpenStreetMap Overpass API. Free, no API key,\n"
            "no signup. Filter by location (lat/lon + max_km), cuisine (OSM tag),\n"
            "amenity, or name query. Set --pet-friendly to intersect with the\n"
            "FEHD 50-restaurant pet-friendly dataset. Sub-agents default to\n"
            "Google Places; this exists because OSM is the better default for\n"
            "BAW (no key, no rate limit, no cost, 24h cache)."
        ),
        "example": "baw restaurant search --lat 22.28 --lon 114.17 --max-km 1.5",
        "category": "data",
        "aliases": ["food", "eat"],
        "usage": "baw restaurant search [--bbox S W N E] [--lat LAT --lon LON --max-km KM] [--cuisine TAG] [--query NAME] [--pet-friendly] [--amenity TAG]",
    },
    "tools": {
        "short": "🔧  Tool scaffolder / verifier",
        "long": (
            "Manage BAW tools. list shows all registered tools with file\n"
            "status. verify imports each tool and calls its handler as a\n"
            "smoke test — use this AFTER creating a new tool to confirm\n"
            "it actually loads. show prints a tool's source. doctor cross-\n"
            "checks registrations against files on disk.\n\n"
            "Always run 'baw tools verify <name>' before claiming a newly\n"
            "written tool is 'done'. Tools that fail verify are NOT done."
        ),
        "example": "baw tools verify todo",
        "category": "dev",
        "aliases": ["tool"],
        "usage": "baw tools [list|verify|show|doctor]",
        "subcommands": ["list", "verify", "show", "doctor"],
    },
    "sessions": {
        "short": "📋  Browse past chat sessions",
        "long": (
            "List recent sessions with message counts and timestamps.\n"
            "Use 'view <id>' to replay a full session transcript."
        ),
        "example": "baw sessions list",
        "category": "monitor",
        "aliases": ["history", "sess", "list", "task"],
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
            "BAW agent CLI with BAW identity (purple+gold).\n"
            "Supports web_search tool calling."
        ),
        "example": "baw tui-chat",
        "category": "interact",
        "aliases": ["tchat", "tc"],
        "usage": "baw tui-chat",
    },
    "evolve": {
        "short": "🧬  Self-evolution engine — analyze & optimize",
        "long": (
            "Run BAW's self-evolution pipeline: analyze behavior logs,\n"
            "detect patterns, and optionally auto-optimize SOUL.md + config.\n"
            "  analyze    — Scan last 7 days of behavior logs for patterns\n"
            "  optimize   — Dry-run auto-optimization (add --apply to write)\n"
            "  stats      — One-line evolution summary"
        ),
        "example": "baw evolve analyze",
        "category": "monitor",
        "aliases": ["evo", "ev"],
        "usage": "baw evolve [analyze|optimize|stats]",
        "subcommands": ["analyze", "optimize", "stats"],
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
        "usage": "baw restart [--force]",
    },
    "gateway": {
        "short": "🚦 Start/stop BAW service",
        "long": "Manage the BAW gateway service.\n  baw gateway start  — Start the service\n  baw gateway stop   — Stop the service",
        "example": "baw gateway start",
        "args": [{"name": "action", "choices": ["start", "stop"]}],
        "usage": "baw gateway <start|stop>",
    },
    "rebuild": {
        "short": "🔨  Fast rebuild + restart (cached layers)",
        "long": (
            "Rebuild BAW Docker image with layer caching and restart.\n"
            "Add --no-cache to force full rebuild. ~7s normal, ~45s full.\n"
            "Also available as host-side: baw-rebuild [--up]"
        ),
        "example": "baw rebuild",
        "category": "system",
        "aliases": ["rb"],
        "usage": "baw rebuild [--no-cache] [--no-up]",
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
    slash.add_column(style="baw.cmd", width=16, no_wrap=True)
    slash.add_column(style="baw.dim", width=28)
    slash.add_column(style="baw.cmd", width=16, no_wrap=True)
    slash.add_column(style="baw.dim", width=28)
    slash.add_row("/help", "Show all commands", "/model", "Switch active model")
    slash.add_row("/status", "System health", "/mode", "Switch execution mode")
    slash.add_row("/task list", "Saved sessions", "/task new", "Start fresh session")
    slash.add_row("/btw <text>", "Quick reply", "/search <q>", "Search memories")
    slash.add_row("/clear", "Reset chat", "/exit", "Quit chat")
    slash.add_row("/stop", "Cancel request", "/restart", "Restart engine")

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

    # Handle --version / -V
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        import subprocess as _sp
        try:
            ver = _sp.run(
                ["git", "describe", "--tags", "--always", "--dirty"],
                capture_output=True, text=True, timeout=5,
                cwd=str(Path(__file__).resolve().parent.parent),
            ).stdout.strip()
        except Exception:
            ver = "unknown"
        console.print(f"[baw.gold]baw[/baw.gold] [baw.value]{ver}[/baw.value]")
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
        sys.exit(1)

    # Extract subcommand and remaining args. Two routing styles coexist:
    #   - "positional" (default): subcommand=argv[2], args=argv[3:]
    #   - "argparse": pass argv[2:] as one list to a module that does its
    #     own argparse (used by self-test, preflight, todo, etc.)
    # The "uses_argparse" flag in each command's dict picks the style.
    cmd_meta = COMMANDS.get(canonical, {}) if canonical else {}
    if cmd_meta.get("uses_argparse"):
        args = sys.argv[2:]
        subcommand = None
    else:
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
        elif canonical == "router":
            from cli.commands.router_cmd import cmd_router
            cmd_router(subcommand, args)
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
        elif canonical == "gateway":
            action = subcommand or "start"
            from cli.commands.restart import cmd_gateway_start, cmd_gateway_stop
            if action == "stop":
                cmd_gateway_stop()
            else:
                cmd_gateway_start()
        elif canonical == "restart":
            from cli.commands.restart import cmd_restart
            force = "--force" in args if args else False
            cmd_restart(force=force)
        elif canonical == "rebuild":
            from cli.commands.rebuild import cmd_rebuild
            no_cache = "--no-cache" in args if args else False
            up = "--no-up" not in args if args else True
            cmd_rebuild(no_cache=no_cache, up=up)
        elif canonical == "skill":
            from cli.commands.skill_cmd import cmd_skill
            cmd_skill(subcommand, args)
        elif canonical == "memory":
            from cli.commands.memory_cmd import cmd_memory
            cmd_memory()
        elif canonical == "todo":
            from cli.commands.todo_cmd import main as _todo_main
            _todo_main(args if subcommand is None else [subcommand] + args)
        elif canonical == "self-test":
            from cli.commands.self_test_cmd import main as _selftest_main
            _selftest_main(args or None)
        elif canonical == "preflight":
            from cli.commands.preflight_cmd import main as _preflight_main
            _preflight_main(args or None)
        elif canonical == "petrestaurants":
            from cli.commands.petrestaurants_cmd import main as _pet_main
            _pet_main([subcommand] + args if subcommand else [])
        elif canonical == "restaurant":
            from cli.commands.restaurant_cmd import main as _restaurant_main
            _restaurant_main([subcommand] + args if subcommand else [])
        elif canonical == "tools":
            from cli.commands.tools_cmd import main as _tools_main
            _tools_main([subcommand] + args if subcommand else [])
        elif canonical == "sessions":
            from cli.commands.sessions_cmd import cmd_sessions
            cmd_sessions(subcommand, args)
        elif canonical == "setup":
            from core.setup import cmd_setup
            from pathlib import Path
            cmd_setup(data_dir=Path.home() / ".baw")
        elif canonical == "court":
            from cli.commands.court_cmd import main as _court_main
            _court_main([subcommand] + args if subcommand else [])
        elif canonical == "evolve":
            from cli.commands.evolve_cmd import main as _evolve_main
            _evolve_main([subcommand] + args if subcommand else [])
    except KeyboardInterrupt:
        console.print("\n[baw.dim]👋 Bye.[/baw.dim]")
    except Exception as e:
        console.print(f"[baw.error]Error:[/baw.error] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
