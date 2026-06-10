"""baw models — list and manage AI models with cost display."""
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
    table.add_column("Provider", style="baw.highlight", width=12)
    table.add_column("Model ID", style="baw.value", width=28)
    table.add_column("Context", style="baw.muted", width=10)
    table.add_column("Vision", style="baw.muted", width=7)
    table.add_column("$/1M in", style="baw.dim", width=10)
    table.add_column("$/1M out", style="baw.dim", width=10)
    table.add_column("★", style="baw.accent", width=3)

    for pname, pcfg in providers.items():
        models = pcfg.get("models", [])
        for m in models:
            is_default = "★" if m.get("id") == default_model else ""
            cost_in = _fmt_cost(m.get("cost_per_1m_input"))
            cost_out = _fmt_cost(m.get("cost_per_1m_output"))
            table.add_row(
                pname,
                m.get("id", "?"),
                _fmt_context(m.get("context_window", 0)),
                "✓" if m.get("vision") else "—",
                cost_in,
                cost_out,
                is_default,
            )

    console.print(table)

    # Capability routing
    caps = cfg.get("capabilities", {})
    if caps:
        console.print()
        cap_table = Table(title="⚡ Capability Routing", box=box.ROUNDED, border_style="baw.border")
        cap_table.add_column("Capability", style="baw.highlight")
        cap_table.add_column("Model / Method", style="baw.value")
        for cname, ccfg in caps.items():
            if isinstance(ccfg, dict):
                if "model" in ccfg:
                    cap_table.add_row(cname, ccfg["model"])
                elif "method" in ccfg:
                    cap_table.add_row(cname, f"[baw.dim]method:[/baw.dim] {ccfg['method']}")
        console.print(cap_table)

    # Adversarial models
    adv = cfg.get("adversarial", {})
    if adv.get("enabled"):
        console.print()
        adv_panel = Panel(
            f"[baw.key]😇 Angel[/baw.key]  [baw.value]{default_model}[/baw.value]\n"
            f"[baw.key]👿 Devil[/baw.key]  [baw.value]{adv.get('devil_model', '—')}[/baw.value]\n"
            f"[baw.dim]Warn threshold: {adv.get('warn_threshold', 2)}[/baw.dim]",
            title="[baw.gold]⚔ Adversarial Mode[/baw.gold]",
            border_style="baw.accent",
        )
        console.print(adv_panel)


def _fmt_context(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    elif n >= 1000:
        return f"{n / 1000:.0f}K"
    return str(n)


def _fmt_cost(v: float | None) -> str:
    if v is None or v == 0:
        return "[baw.dim]—[/baw.dim]"
    if v < 0.01:
        return f"${v:.4f}"
    return f"${v:.2f}"
