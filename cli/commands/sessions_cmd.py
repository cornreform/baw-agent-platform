"""baw sessions — browse past session transcripts."""
import json
from datetime import datetime
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from cli import console

BAW_HOME = Path.home() / ".baw"


def cmd_sessions(subcommand: str | None = None, args: list[str] | None = None):
    if subcommand is None or subcommand == "list":
        _sessions_list()
    elif subcommand == "view" and args:
        _session_view(args[0])
    elif subcommand == "search" and args:
        _session_search(args[0])
    else:
        console.print("[baw.dim]Usage: baw sessions [list|view <id>|search <term>][/baw.dim]")


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
    console.print("[baw.dim]Use 'baw sessions view <id>' or 'baw sessions search <term>'[/baw.dim]")


def _session_view(session_id: str):
    session_file = BAW_HOME / "sessions" / f"{session_id}.jsonl"
    if not session_file.exists():
        console.print(f"[baw.error]Session not found:[/baw.error] {session_id}")
        return

    console.print(f"\n[baw.gold]📋  Session: {session_id}[/baw.gold]\n")

    for i, line in enumerate(session_file.read_text().splitlines(), 1):
        try:
            msg = json.loads(line)
            role = msg.get("role", "?")
            content = msg.get("content", "")[:300]
            ts = msg.get("timestamp", "")
            time_str = ""
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    time_str = f" [baw.dim]{dt.strftime('%H:%M:%S')}[/]"
                except Exception:
                    pass

            if role == "user":
                console.print(f"[baw.gold]⚡[/] {content}{time_str}")
            elif role == "assistant":
                console.print(f"[baw.purple]🖤 BAW[/] {content}{time_str}")
            elif role == "tool":
                tool_name = msg.get("name", "") or msg.get("tool_name", "")
                preview = content[:100]
                console.print(f"  [baw.dim]🔧 {tool_name}: {preview}[/]{time_str}")
            else:
                console.print(f"[baw.dim]{role}: {content[:200]}[/]{time_str}")
        except json.JSONDecodeError:
            console.print(f"[baw.dim]{line[:200]}[/]")

        if i >= 200:
            console.print(f"\n[baw.dim]Showing first 200 messages. Session has more.[/baw.dim]")
            break


def _session_search(term: str):
    sessions_dir = BAW_HOME / "sessions"
    if not sessions_dir.exists():
        console.print("[baw.dim]No sessions found.[/baw.dim]")
        return

    results = []
    for f in sessions_dir.glob("*.jsonl"):
        content = f.read_text()
        if term.lower() in content.lower():
            count = content.lower().count(term.lower())
            results.append((f, count))

    if not results:
        console.print(f"[baw.dim]No sessions containing '{term}'.[/baw.dim]")
        return

    results.sort(key=lambda x: -x[1])
    table = Table(title=f"[baw.gold]🔍 Sessions containing '{term}'[/baw.gold]",
                  border_style="baw.accent", box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="baw.cmd", width=36)
    table.add_column("Matches", style="baw.value", justify="right")
    for f, count in results[:20]:
        table.add_row(f.stem, str(count))

    console.print(table)
