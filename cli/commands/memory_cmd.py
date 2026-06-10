"""baw memory — memory stats."""
import json
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box
from cli import console

BAW_HOME = Path.home() / ".baw"


def cmd_memory():
    mem_dir = BAW_HOME / "memory"
    store_file = mem_dir / "store.jsonl"
    edges_file = mem_dir / "edges.json"

    entries = 0
    edges = 0
    size_bytes = 0

    if store_file.exists():
        entries = sum(1 for _ in store_file.read_text().splitlines())
        size_bytes += store_file.stat().st_size

    if edges_file.exists():
        try:
            data = json.loads(edges_file.read_text())
            edges = len(data.get("edges", []))
        except Exception:
            pass
        size_bytes += edges_file.stat().st_size

    # Also count any other files in memory dir
    if mem_dir.exists():
        # Add sizes of all files (already counted store + edges above,
        # this picks up any extra files like index, cache, etc.)
        total = sum(f.stat().st_size for f in mem_dir.rglob("*") if f.is_file())
        # If total differs from our sum, use the larger value (catches extra files)
        if total > size_bytes:
            size_bytes = total

    size_str = f"{size_bytes/1000:.1f} KB" if size_bytes < 1_000_000 else f"{size_bytes/1_000_000:.1f} MB"

    panel = Panel(
        f"[baw.key]Entries[/baw.key]  [baw.val]{entries}[/baw.val]\n"
        f"[baw.key]Edges[/baw.key]    [baw.val]{edges}[/baw.val]\n"
        f"[baw.key]Size[/baw.key]     [baw.val]{size_str}[/baw.val]",
        title="[baw.gold]🧩  Memory Stats[/baw.gold]",
        border_style="baw.accent",
        box=box.HEAVY,
        padding=(1, 3),
    )
    console.print(panel)
