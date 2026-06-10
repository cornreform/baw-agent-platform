"""baw memory — memory stats."""
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
    size_kb = 0

    if store_file.exists():
        entries = sum(1 for _ in store_file.read_text().splitlines())
        size_kb += store_file.stat().st_size
    if edges_file.exists():
        import json
        try:
            data = json.loads(edges_file.read_text())
            edges = len(data.get("edges", []))
            size_kb += len(edges_file.read_text().encode())
        except Exception:
            pass
    if mem_dir.exists():
        size_kb = sum(f.stat().st_size for f in mem_dir.rglob("*") if f.is_file())

    panel = Panel(
        f"[baw.key]Entries[/baw.key]  [baw.val]{entries}[/baw.val]\n"
        f"[baw.key]Edges[/baw.key]    [baw.val]{edges}[/baw.val]\n"
        f"[baw.key]Size[/baw.key]     [baw.val]{size_kb/1000:.1f} KB[/baw.val]",
        title="[baw.gold]🧩  Memory Stats[/baw.gold]",
        border_style="baw.accent",
        box=box.HEAVY,
        padding=(1, 3),
    )
    console.print(panel)
