"""baw router — view and edit tier → model preferences.

Lets the user decide which model handles each complexity tier.
The code makes NO quality judgement — it only picks the first
model in your preference list that exists in providers.

Usage:
  baw router show
  baw router set trivial step-3.5-flash-2603
  baw router set expert kimi-k2.6
  baw router append complex agnes-2.0-flash
  baw router reset
"""
import yaml
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from cli import console

BAW_HOME = Path.home() / ".baw"
CONFIG_PATH = BAW_HOME / "config.yaml"

TIERS = ("trivial", "moderate", "complex", "expert")


def cmd_router(subcommand: str | None = None, args: list[str] | None = None):
    if subcommand is None or subcommand == "show":
        _router_show()
    elif subcommand == "set":
        _router_set(args, replace=True)
    elif subcommand == "append":
        _router_set(args, replace=False)
    elif subcommand == "reset":
        _router_reset()
    else:
        console.print(f"[baw.error]Unknown router subcommand:[/baw.error] {subcommand}")
        console.print("[baw.muted]Try: router show | set <tier> <model> | append <tier> <model> | reset[/baw.muted]")


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        console.print("[baw.error]Config not found. Run baw setup first.[/baw.error]")
        import sys; sys.exit(1)
    return yaml.safe_load(CONFIG_PATH.read_text()) or {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False))


def _router_show():
    """Display current tier preferences with status indicators."""
    cfg = _load_config()
    user_prefs = cfg.get("router", {}).get("tier_preferences", {}) or {}

    # Get all configured models
    available = set()
    for pname, pcfg in cfg.get("providers", {}).items():
        for m in pcfg.get("models", []):
            mid = m.get("id", "")
            caps = m.get("capabilities", [])
            if "chat" in caps and mid:
                available.add(mid)

    # Lazy import to get defaults
    from core.router import DEFAULT_TIER_PREFERENCES

    table = Table(title="🎯 Tier → Model Routing", show_header=True, header_style="baw.head")
    table.add_column("Tier", style="baw.key", width=10)
    table.add_column("Configured?", width=12)
    table.add_column("Preference Order (left = preferred)", style="baw.value")

    for tier in TIERS:
        if tier in user_prefs:
            order = user_prefs[tier]
            configured = "✓ user"
        else:
            order = DEFAULT_TIER_PREFERENCES.get(tier, [])
            configured = "default"
        # Annotate each model: ✓ available, ✗ not in providers
        cells = []
        for m in order:
            mark = "✓" if m in available else "✗"
            cells.append(f"{mark} {m}")
        table.add_row(tier, configured, " → ".join(cells) or "(empty)")

    console.print()
    console.print(table)
    console.print()
    console.print(
        "[baw.muted]Configure with:[/baw.muted] [baw.cmd]baw router set <tier> <model>[/baw.cmd]  "
        "(e.g. [baw.cmd]baw router set expert kimi-k2.6[/baw.cmd])"
    )
    console.print(
        "[baw.muted]Add fallback:[/baw.muted]     [baw.cmd]baw router append <tier> <model>[/baw.cmd]"
    )
    console.print(
        "[baw.muted]Reset all:[/baw.muted]        [baw.cmd]baw router reset[/baw.cmd]"
    )


def _router_set(args: list[str] | None, replace: bool = True):
    """Set or append a model to a tier's preference list.

    Args:
        args: [tier, model_id, ...]
        replace: if True, replace whole list. If False, append.
    """
    if not args or len(args) < 2:
        verb = "set" if replace else "append"
        console.print(f"[baw.error]Usage:[/baw.error] baw router {verb} <tier> <model_id> [more...]")
        return

    tier = args[0]
    if tier not in TIERS:
        console.print(f"[baw.error]Unknown tier:[/baw.error] {tier}")
        console.print(f"[baw.muted]Valid tiers: {', '.join(TIERS)}[/baw.muted]")
        return

    cfg = _load_config()
    cfg.setdefault("router", {}).setdefault("tier_preferences", {})

    if replace or tier not in cfg["router"]["tier_preferences"]:
        cfg["router"]["tier_preferences"][tier] = list(args[1:])
    else:
        # Append, dedupe
        existing = list(cfg["router"]["tier_preferences"][tier])
        for m in args[1:]:
            if m not in existing:
                existing.append(m)
        cfg["router"]["tier_preferences"][tier] = existing

    _save_config(cfg)
    verb = "Set" if replace else "Appended to"
    console.print(
        f"[baw.success]✓[/baw.success] {verb} [baw.key]{tier}[/baw.key]: "
        f"[baw.value]{' → '.join(cfg['router']['tier_preferences'][tier])}[/baw.value]"
    )


def _router_reset():
    """Remove all user tier_preferences (revert to defaults)."""
    cfg = _load_config()
    if "router" in cfg and "tier_preferences" in cfg["router"]:
        cfg["router"].pop("tier_preferences", None)
    _save_config(cfg)
    console.print("[baw.success]✓[/baw.success] Reset to defaults. Run [baw.cmd]baw router show[/baw.cmd] to verify.")
