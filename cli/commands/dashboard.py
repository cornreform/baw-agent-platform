"""baw dashboard — compact live TUI dashboard powered by Textual.
Purple + Gold theme. 5-second auto-refresh. No scrollbars — fits terminal.
"""
from __future__ import annotations
import os, json, re
from pathlib import Path
from datetime import datetime

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer
from textual.containers import Grid
from textual.binding import Binding
from textual.color import Color
from rich.table import Table
from rich.panel import Panel

BAW_HOME = Path.home() / ".baw"

# ── Helpers ──────────────────────────────────────────────────────────

def _parse_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}

def _load_jsonl(path: Path) -> list[dict]:
    items = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    return items

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

def _relative_time(ts: float) -> str:
    import time
    diff = time.time() - ts
    if diff < 60:   return f"{int(diff)}s"
    if diff < 3600: return f"{int(diff/60)}m"
    if diff < 86400: return f"{int(diff/3600)}h"
    return f"{int(diff/86400)}d"

# ═══════════════════════════════════════════════════════════════════════
# Panels — use compact single-str renderables
# ═══════════════════════════════════════════════════════════════════════

def _panel_system() -> str:
    uptime = "—"
    try:
        with open("/proc/uptime") as f:
            sec = int(float(f.read().split()[0]))
        d, r = divmod(sec, 86400)
        h, m = divmod(r, 3600)
        m //= 60
        if d:   uptime = f"{d}d {h}h"
        elif h: uptime = f"{h}h {m}m"
        else:   uptime = f"{m}m"
    except Exception:
        pass

    lines = [
        f"[bold magenta]Host[/]    {os.uname().nodename}",
        f"[bold magenta]Uptime[/]  {uptime}",
        f"[bold magenta]Now[/]     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return "\n".join(lines)

def _panel_models() -> str:
    cfg = _parse_yaml(BAW_HOME / "config.yaml")
    default = cfg.get("model", {}).get("default", "—")
    fallback = cfg.get("model", {}).get("fallback", "—")
    adv = cfg.get("adversarial", {})
    devil = adv.get("devil_model", "—")
    caps = cfg.get("capabilities", {})

    lines = [
        f"[bold yellow]Default[/]  {default}",
        f"[bold yellow]Fallback[/] {fallback}",
        f"[bold yellow]Angel[/]    [green]{default}[/]",
        f"[bold yellow]Devil[/]    [red]{devil}[/]",
    ]
    # Cap routing — only non-default
    routes = []
    for cap in ("stt", "tts", "vision", "image_generation"):
        cc = caps.get(cap, {})
        m = cc.get("model", "") if isinstance(cc, dict) else ""
        if m and m != default:
            routes.append(f"[dim]{cap}:[/] {m}")
    if routes:
        lines.append(f"[bold yellow]Routes[/]  {' | '.join(routes)}")
    return "\n".join(lines)

def _panel_connectors() -> str:
    te = BAW_HOME / "telegram.env"
    if te.exists() and "BAW_TELEGRAM_TOKEN" in te.read_text():
        # Count msgs across sessions
        sd = BAW_HOME / "sessions"
        total = sum(len(f.read_text().splitlines()) for f in sd.glob("*.jsonl")) if sd.exists() else 0
        return (
            "[green]● Telegram connected[/]\n"
            f"[dim]Messages: {total}[/]"
        )
    return "○ [dim]Telegram not configured[/]"

def _panel_sessions() -> str:
    sd = BAW_HOME / "sessions"
    lines = []
    if sd.exists():
        files = sorted(sd.glob("*.jsonl"), key=os.path.getmtime, reverse=True)[:5]
        for f in files:
            n = len(f.read_text().splitlines())
            sid = f.stem[:12]
            t = _relative_time(os.path.getmtime(f))
            lines.append(f"[magenta]{sid}[/]  [white]{n:>3} msgs[/]  [dim]{t}[/]")
    if not lines:
        lines.append("[dim]No sessions yet[/]")
    return "\n".join(lines)

def _panel_memory() -> str:
    md = BAW_HOME / "memory"
    if not md.exists():
        return "[dim]No memory store[/]"
    store = md / "store.jsonl"
    memories = _load_jsonl(store) if store.exists() else []
    total = len(memories)
    sz = sum(f.stat().st_size for f in md.rglob("*") if f.is_file())
    scores = [m.get("score", 0) for m in memories if "score" in m]
    avg = sum(scores) / len(scores) if scores else 0
    high = sum(1 for s in scores if s > 0.7)
    med = sum(1 for s in scores if 0.3 <= s <= 0.7)
    low = sum(1 for s in scores if s < 0.3)
    return (
        f"[bold magenta]Entries[/]  {total}\n"
        f"[bold magenta]Size[/]     {_human_size(sz)}\n"
        f"[bold magenta]Score[/]    {avg:.2f}\n"
        f"[bold magenta]Quality[/]  [green]{high} high[/] [yellow]{med} med[/] [red]{low} low[/]"
    )

def _panel_activity() -> str:
    ld = BAW_HOME / "logs"
    if not ld.exists():
        return "[dim]No logs yet[/]"
    logs = sorted(ld.glob("*.log"), key=os.path.getmtime, reverse=True)
    if not logs:
        return "[dim]No logs yet[/]"
    content = logs[0].read_text().splitlines()
    lines = []
    for line in content[-8:]:
        # Strip timestamp prefix, keep message
        short = line[:80].rsplit(" ", 1)[-1] if " " in line else line
        lines.append(f"[dim]{short[:60]}[/]")
    return "\n".join(lines) if lines else "[dim]Empty log[/]"


# ═══════════════════════════════════════════════════════════════════════
# App — responsive 3×2 grid, no scroll
# ═══════════════════════════════════════════════════════════════════════

class BAWDashboard(App):
    """Compact live TUI dashboard for BAW Agent Platform."""

    CSS = """
    Screen {
        background: #0d0d12;
    }
    Header {
        background: #131320;
        color: magenta;
        padding: 0 1;
    }
    Footer {
        background: #131320;
        color: #555;
    }
    #grid {
        layout: grid;
        grid-size: 3 2;
        grid-gutter: 1 1;
        grid-columns: 1fr 1fr 1fr;
        grid-rows: 1fr 1fr;
        margin: 1 1;
    }
    #grid > .dash-panel {
        border: solid #2a1535;
        background: #131320;
        padding: 0 1;
        overflow: hidden;
        min-height: 6;
        max-height: 100%;
        content-justify: left;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_data", "Refresh"),
    ]

    PANEL_IDS = ["sys", "models", "conn", "sessions", "memory", "activity"]
    PANEL_TITLES = {
        "sys": "[bold yellow]⚙ System[/]",
        "models": "[bold yellow]🤖 Models[/]",
        "conn": "[bold yellow]📡 Connectors[/]",
        "sessions": "[bold yellow]📂 Sessions[/]",
        "memory": "[bold yellow]🧠 Memory[/]",
        "activity": "[bold yellow]📜 Activity[/]",
    }
    PANEL_RENDERERS = {
        "sys": _panel_system,
        "models": _panel_models,
        "conn": _panel_connectors,
        "sessions": _panel_sessions,
        "memory": _panel_memory,
        "activity": _panel_activity,
    }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Grid(id="grid"):
            for pid in self.PANEL_IDS:
                p = Panel("", id=pid, classes="dash-panel",
                          title=self.PANEL_TITLES[pid],
                          border_style="magenta")
                yield p
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🖤 BAW Dashboard"
        self.sub_title = "Black And White"
        self._refresh()
        self.set_interval(5, self._refresh)

    def action_refresh_data(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        for pid in self.PANEL_IDS:
            try:
                panel = self.query_one(f"#{pid}", Panel)
                content = self.PANEL_RENDERERS[pid]()
                panel.renderable = content
                panel.refresh()
            except Exception:
                pass


def cmd_dashboard():
    BAWDashboard().run()
