"""baw models — list and manage AI models."""
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box
from cli import console

BAW_HOME = Path.home() / ".baw"


def cmd_models(subcommand: str | None = None, args: list[str] | None = None):
    if subcommand is None or subcommand == "list":
        _models_list()
    else:
        console.print(f"[baw.error]Unknown subcommand:[/baw.error] {subcommand}")


def _models_list():
    import yaml
    cfg_path = BAW_HOME / "config.yaml"
    if not cfg_path.exists():
        console.print("[baw.error]Config not found.[/baw.error]")
        return

    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    providers = cfg.get("providers", {})
    default_model = cfg.get("model", {}).get("default", "N/A")

    table = Table(title="🤖 BAW AI Models", box=box.ROUNDED, border_style="baw.border")
    table.add_column("Provider", style="baw.highlight", width=15)
    table.add_column("Model ID", style="baw.value", width=30)
    table.add_column("Context", style="baw.muted", width=12)
    table.add_column("Vision", style="baw.muted", width=8)
    table.add_column("Default", style="baw.accent", width=10)

    for pname, pcfg in providers.items():
        models = pcfg.get("models", [])
        for m in models:
            is_default = "★" if m["id"] == default_model else ""
            table.add_row(
                pname,
                m["id"],
                _fmt_context(m.get("context_window", 0)),
                "✓" if m.get("vision") else "—",
                is_default,
            )

    console.print(table)

    # Capability routing
    caps = cfg.get("capabilities", {})
    if caps:
        console.print()
        cap_table = Table(title="⚡ Capability Routing", box=box.ROUNDED, border_style="baw.border")
        cap_table.add_column("Capability", style="baw.highlight")
        cap_table.add_column("Model", style="baw.value")
        for cname, ccfg in caps.items():
            if isinstance(ccfg, dict) and "model" in ccfg:
                cap_table.add_row(cname, ccfg["model"])
        console.print(cap_table)


def _fmt_context(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    elif n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)
