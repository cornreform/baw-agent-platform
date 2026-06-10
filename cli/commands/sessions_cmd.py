"""baw sessions — browse past session transcripts."""
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box
from cli import console

BAW_HOME = Path.home() / ".baw"


def cmd_sessions(subcommand: str | None = None, args: list[str] | None = None):
    if subcommand is None or subcommand == "list":
        _sessions_list()
    elif subcommand == "view" and args:
        _session_view(args[0])
    else:
        console.print("[baw.dim]Usage: baw sessions [list|view <id>][/baw.dim]")


def _sessions_list():
    sessions_dir = BAW_HOME / "sessions"
    if not sessions_dir.exists():
        console.print("[baw.dim]No sessions found.[/baw.dim]")
        return

    files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]

    if not files:
        console.print("[baw.dim]No sessions found.[/baw.dim]")
        return

    table = Table(
        title="[baw.gold]📋  Recent Sessions[/baw.gold]",
        border_style="baw.accent",
        box=box.SIMPLE_HEAVY,
    )
    table.add_column("ID", style="baw.cmd", width=36, no_wrap=True)
    table.add_column("Lines", style="baw.muted", width=8, justify="right")
    table.add_column("Size", style="baw.dim", width=10, justify="right")

    for f in files:
        lines = sum(1 for _ in f.read_text().splitlines())
        size = f.stat().st_size
        size_str = f"{size}B" if size < 1024 else f"{size/1024:.1f}KB"
        table.add_row(f.stem, str(lines), size_str)

    console.print(table)


def _session_view(session_id: str):
    session_file = BAW_HOME / "sessions" / f"{session_id}.jsonl"
    if not session_file.exists():
        console.print(f"[baw.error]Session not found:[/baw.error] {session_id}")
        return

    console.print(f"[baw.gold]📋  Session: {session_id}[/baw.gold]\n")
    for line in session_file.read_text().splitlines()[:100]:
        console.print(f"[baw.dim]{line[:200]}[/baw.dim]")
    console.print(f"\n[baw.dim]Showing first 100 lines.[/baw.dim]")
