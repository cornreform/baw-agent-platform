"""
BAW — Slash Commands (P1)
Interactive mode commands for quick operations.
"""

from __future__ import annotations
import sys
import subprocess
from pathlib import Path
from typing import Optional


def handle_slash(command: str, args: list[str],
                 config: dict, data_dir: Path, verbose: bool = False) -> Optional[str]:
    """Route a slash command and return a response string.

    Returns None if the command doesn't trigger any action.
    """
    cmd = command.lower()

    if cmd in ("h", "help", "?"):
        return _cmd_help()

    if cmd in ("v", "version"):
        return _cmd_version(data_dir)

    if cmd in ("s", "status"):
        return _cmd_status(config, data_dir)

    if cmd in ("r", "remember"):
        text = " ".join(args)
        if not text:
            return "Usage: /remember <text>"
        return _cmd_remember(text, data_dir)

    if cmd in ("q", "search"):
        query = " ".join(args)
        if not query:
            return "Usage: /search <query>"
        return _cmd_search(query, data_dir)

    if cmd in ("m", "model"):
        model_id = " ".join(args)
        if not model_id:
            return "Usage: /model <id>  (see config.yaml for available models)"
        return _cmd_model(model_id, config)

    if cmd in ("t", "tone"):
        tone = " ".join(args)
        if not tone:
            return "Usage: /tone <profile>  (casual / business / teaching / client-doc / ot-rt / stepwise)"
        return _cmd_tone(tone, config)

    if cmd in ("d", "dream"):
        return _cmd_dream(data_dir)

    if cmd in ("c", "clear"):
        _cmd_clear()
        return ""

    if cmd in ("provider", "sp", "search-provider"):
        return _cmd_search_provider(args, data_dir)

    if cmd == "tools":
        return _cmd_tools()

    # Not a slash command
    return None


def _cmd_help() -> str:
    lines = [
        "BAW Slash Commands:",
        "",
        "  /help, /h, /?     Show this help",
        "  /version, /v      Show BAW version + last commit",
        "  /status, /s       Show BAW status (model, memory, tools)",
        "  /remember, /r     Store a memory entry",
        "  /search, /q       Search memory",
        "  /model, /m        Switch model (check config.yaml)",
        "  /tone, /t         Switch tone profile",
        "  /dream, /d        Run weekly self-curation",
        "  /tools            List available tools",
        "  /provider, /sp    Search provider: list | api <name> | test <name> <query>",
        "  /clear, /c        Clear screen",
        "  Ctrl+D            Exit interactive mode",
        "",
        "Anything else is sent to the BAW agent as a prompt.",
    ]
    return "\n".join(lines)


def _cmd_version(data_dir: Path) -> str:
    try:
        log = subprocess.run(
            ["git", "log", "--oneline", "-3"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path.home() / "baw"),
        ).stdout.strip()
    except Exception:
        log = "unknown"
    return f"BAW v1.0.0\n{log}\nRepo: github.com/cornreform/baw-agent-platform"


def _cmd_status(config: dict, data_dir: Path) -> str:
    from .memory import MemoryStore
    from .tools import list_tools

    # Ensure tools are registered
    try:
        from ..tools import register_all
        register_all()
    except ImportError:
        pass

    mem = MemoryStore(data_dir)
    stats = mem.stats()
    model_cfg = config.get("model", {})
    tone = config.get("tone", {}).get("default", "casual")
    fact_mode = config.get("fact_check", {}).get("mode", "normal")

    lines = [
        "BAW Status",
        f"  Memory: {stats['total']} entries",
    ]
    if stats['total'] > 0:
        lines.append(f"  Avg score: {stats['avg_score']:.2f}")
    model_default = model_cfg.get("default", "?")
    model_fallback = model_cfg.get("fallback", "none")
    lines.append(f"  Model: {model_default} (fallback: {model_fallback})")
    lines.append(f"  Tone: {tone}")
    lines.append(f"  Fact check: {fact_mode}")
    tools = [t.name for t in list_tools()]
    lines.append(f"  Tools ({len(tools)}): {', '.join(tools)}")

    # Git status
    try:
        dirty = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path.home() / "baw"),
        ).stdout.strip()
        lines.append(f"  Git: {'dirty (' + str(len(dirty.split(chr(10)))) + ' files)' if dirty else 'clean'}")
    except Exception:
        pass

    return "\n".join(lines)


def _cmd_remember(text: str, data_dir: Path) -> str:
    from .memory import MemoryStore
    mem = MemoryStore(data_dir)
    result = mem.remember(text)
    return f"Remembered (score: {result['score']:.2f})"


def _cmd_search(query: str, data_dir: Path) -> str:
    from .memory import MemoryStore
    mem = MemoryStore(data_dir)
    results = mem.search(query)
    if not results:
        return "No results found."
    lines = []
    for r in results:
        lines.append(f"[{r['score']:.2f}] {r['content'][:100]}")
    return "\n".join(lines)


def _cmd_model(model_id: str, config: dict) -> str:
    config.setdefault("model", {})["default"] = model_id
    return f"Model set to: {model_id}"


def _cmd_tone(tone: str, config: dict) -> str:
    valid = ("casual", "business", "teaching", "client-doc", "ot-rt", "stepwise")
    if tone not in valid:
        return f"Invalid tone: {tone}. Valid: {', '.join(valid)}"
    config.setdefault("tone", {})["default"] = tone
    return f"Tone set to: {tone}"


def _cmd_dream(data_dir: Path) -> str:
    from .dream import dream
    report = dream(data_dir)
    if report["changes"]:
        import json
        return json.dumps(report, ensure_ascii=False, indent=2)
    return "No changes needed."


def _cmd_clear():
    import os
    os.system("clear" if sys.platform != "win32" else "cls")


def _cmd_search_provider(args: list[str], data_dir: Path) -> str:
    from .search import (
        list_providers, get_setup_guide, get_api_reference, search,
    )
    from .search import _auto_discover as _init_search
    _init_search()

    if not args:
        return "Usage: /provider list | api <name> | test <name> <query>"

    action = args[0]

    if action == "list":
        providers = list_providers()
        if not providers:
            return "No search providers registered."
        lines = ["Available Search Providers:"]
        for p in providers:
            key_status = "API key required" if p["requires_api_key"] else "No API key needed"
            lines.append(f"  {p['name']}: {p['description']}")
            lines.append(f"    {key_status}")
            if p["env_var"]:
                lines.append(f"    Env var: {p['env_var']}")
        return "\n".join(lines)

    if action == "api" and len(args) >= 2:
        try:
            return get_api_reference(args[1])
        except ValueError as e:
            return f"Error: {e}"

    if action == "test" and len(args) >= 3:
        provider = args[1]
        query = " ".join(args[2:])
        try:
            results = search(query, provider=provider, limit=3)
            if not results:
                return f"No results from {provider}."
            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"[{i}] {r['title']}")
                lines.append(f"     URL: {r['url']}")
                lines.append(f"     {r['snippet'][:200]}")
            lines.append(f"\n{len(results)} results from {provider}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    return "Usage: /provider list | api <name> | test <name> <query>"


def _cmd_tools() -> str:
    from .tools import list_tools
    try:
        from ..tools import register_all
        register_all()
    except ImportError:
        pass
    tools = list_tools()
    if not tools:
        return "No tools registered."
    lines = ["Available tools:"]
    for t in tools:
        lines.append(f"  {t.name}: {t.description[:80]}")
    return "\n".join(lines)
