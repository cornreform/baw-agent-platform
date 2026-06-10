"""baw skill — list/install/manage BAW skills."""
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box
from cli import console

BAW_HOME = Path.home() / ".baw"


def cmd_skill(subcommand: str | None = None, args: list[str] | None = None):
    if subcommand is None or subcommand == "list":
        _skill_list()
    elif subcommand == "install":
        console.print("[baw.gold]Install[/baw.gold] — coming soon")
    elif subcommand == "remove":
        console.print("[baw.gold]Remove[/baw.gold] — coming soon")
    else:
        console.print(f"[baw.error]Unknown:[/baw.error] {subcommand}")


def _skill_list():
    skills_dir = BAW_HOME / "skills"
    if not skills_dir.exists():
        console.print("[baw.dim]No skills directory found.[/baw.dim]")
        return

    table = Table(
        title="[baw.gold]📦  BAW Skills[/baw.gold]",
        border_style="baw.accent",
        box=box.SIMPLE_HEAVY,
    )
    table.add_column("Skill", style="baw.cmd", width=30)
    table.add_column("Type", style="baw.muted", width=15)
    table.add_column("Size", style="baw.dim", width=10)

    for f in sorted(skills_dir.glob("*.yaml")):
        size = f.stat().st_size
        size_str = f"{size}B" if size < 1024 else f"{size/1024:.1f}KB"
        table.add_row(f.stem, "YAML", size_str)

    for f in sorted(skills_dir.glob("*.md")):
        size = f.stat().st_size
        size_str = f"{size}B" if size < 1024 else f"{size/1024:.1f}KB"
        table.add_row(f.stem, "Markdown", size_str)

    if table.row_count == 0:
        console.print("[baw.dim]No skills installed.[/baw.dim]")
    else:
        console.print(table)
