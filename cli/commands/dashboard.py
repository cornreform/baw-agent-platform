"""baw dashboard — btop-inspired compact TUI dashboard.
Purple + Gold theme. Thin box-drawing borders. No scrollbars.
5-second auto-refresh. Fits terminal without overflow.
"""
from __future__ import annotations
import os, json, re
from pathlib import Path
from datetime import datetime

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer
from textual.containers import Grid
from textual.binding import Binding
from rich.table import Table
from rich.panel import Panel

BAW_HOME = Path.home() / ".baw"
BOX = "round"  # thin rounded corners

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

def _bar(pct: float, width: int = 8) -> str:
    """Render a horizontal bar using block chars."""
    filled = int(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    if pct > 80:
        return f"[red]{bar}[/]"
    if pct > 50:
        return f"[yellow]{bar}[/]"
    return f"[green]{bar}[/]"

# ═══════════════════════════════════════════════════════════════════════
# Panels — compact label:value lines
# ═══════════════════════════════════════════════════════════════════════

def _render_system() -> str:
    uptime = "—"
    cpu_pct = mem_pct = 0
    try:
        with open("/proc/uptime") as f:
            sec = int(float(f.read().split()[0]))
        d, r = divmod(sec, 86400)
        h, m = divmod(r, 3600)
        m //= 60
        uptime = f"{d}d {h}h {m}m" if d else f"{h}h {m}m" if h else f"{m}m"
    except Exception:
        pass
    try:
        with open("/proc/loadavg") as f:
            load = f.read().split()[:3]
        cpu_pct = min(100, float(load[0]) * 100 / (os.cpu_count() or 1))
        mem = psutil_usage() if "psutil" in dir() else 0
    except Exception:
        pass

    return (
        f"[bold magenta]host[/]  {os.uname().nodename}\n"
        f"[bold magenta]up[/]    {uptime}\n"
        f"[bold magenta]now[/]   {datetime.now().strftime('%H:%M:%S')}\n"
        f"[bold magenta]cpu[/]   [green]{cpu_pct:.0f}%[/] {_bar(cpu_pct)}"
    )

def psutil_usage() -> float:
    """Return memory usage percentage."""
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if parts[0].rstrip(":") in ("MemTotal", "MemAvailable", "MemFree"):
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            if "MemTotal" in meminfo and "MemAvailable" in meminfo:
                used = meminfo["MemTotal"] - meminfo["MemAvailable"]
                return used / meminfo["MemTotal"] * 100
    except Exception:
        pass
    return 0

def _render_models() -> str:
    cfg = _parse_yaml(BAW_HOME / "config.yaml")
    default = cfg.get("model", {}).get("default", "—")
    fallback = cfg.get("model", {}).get("fallback", "—")
    adv = cfg.get("adversarial", {})
    devil = adv.get("devil_model", "—")
    caps = cfg.get("capabilities", {})

    lines = [
        f"[bold yellow]default[/]  {default}",
        f"[bold yellow]fallback[/] {fallback}",
        f"[bold yellow]angel[/]    [green]{default}[/]",
        f"[bold yellow]devil[/]    [red]{devil}[/]",
    ]
    routes = []
    for cap in ("stt", "tts", "vision", "image_generation"):
        cc = caps.get(cap, {})
        m = cc.get("model", "") if isinstance(cc, dict) else ""
        if m and m != default:
            routes.append(f"[dim]{cap}:[/] {m}")
    if routes:
        lines.append(f"[bold yellow]routes[/]  {' | '.join(routes)}")
    return "\n".join(lines)

def _render_connectors() -> str:
    te = BAW_HOME / "telegram.env"
    sd = BAW_HOME / "sessions"
    total = sum(len(f.read_text().splitlines()) for f in sd.glob("*.jsonl")) if sd.exists() else 0
    if te.exists() and "BAW_TELEGRAM_TOKEN" in te.read_text():
        return f"[green]● telegram[/]\n[dim]msgs: {total}[/]"
    return "[dim]○ telegram not configured[/]"

def _render_sessions() -> str:
    sd = BAW_HOME / "sessions"
    lines = []
    if sd.exists():
        files = sorted(sd.glob("*.jsonl"), key=os.path.getmtime, reverse=True)[:5]
        for f in files:
            n = len(f.read_text().splitlines())
            sid = f.stem[:12]
            t = _relative_time(os.path.getmtime(f))
            lines.append(f"[magenta]{sid}[/] [white]{n:>3}[/] [dim]{t}[/]")
    if not lines:
        lines.append("[dim]no sessions yet[/]")
    return "\n".join(lines)

def _render_memory() -> str:
    md = BAW_HOME / "memory"
    if not md.exists():
        return "[dim]no memory store[/]"
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
        f"[bold magenta]entries[/] {total}\n"
        f"[bold magenta]size[/]    {_human_size(sz)}\n"
        f"[bold magenta]score[/]   {avg:.2f}\n"
        f"[bold magenta]quality[/] [green]{high}[/] [yellow]{med}[/] [red]{low}[/]"
    )

def _render_activity() -> str:
    ld = BAW_HOME / "logs"
    if not ld.exists():
        return "[dim]no logs yet[/]"
    logs = sorted(ld.glob("*.log"), key=os.path.getmtime, reverse=True)
    if not logs:
        return "[dim]no logs yet[/]"
    content = logs[0].read_text().splitlines()
    lines = []
    for line in content[-8:]:
        short = line[:70].rsplit(" ", 1)[-1] if " " in line else line
        lines.append(f"[dim]{short[:55]}[/]")
    return "\n".join(lines) if lines else "[dim]empty log[/]"


# ═══════════════════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════════════════

class BAWDashboard(App):
    """btop-inspired compact dashboard for BAW."""

    CSS = """
    Screen {
        background: #0d0d12;
    }
    Header {
        background: #131320;
        color: magenta;
        padding: 0 1;
        text-style: bold;
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
    #sys, #models, #conn, #sessions, #memory, #activity {
        border: round #2a1535;
        background: #131320;
        padding: 0 1;
        overflow: hidden;
        min-height: 6;
    }
    #sys { border: round #8855aa; }
    #models { border: round #8855aa; }
    #conn { border: round #8855aa; }
    #sessions { border: round #8855aa; }
    #memory { border: round #8855aa; }
    #activity { border: round #8855aa; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh_data", "Refresh"),
    ]

    PANELS = {
        "sys":       ("[bold yellow]⚙ system[/]", _render_system),
        "models":    ("[bold yellow]🤖 models[/]", _render_models),
        "conn":      ("[bold yellow]📡 connect[/]", _render_connectors),
        "sessions":  ("[bold yellow]📂 sessions[/]", _render_sessions),
        "memory":    ("[bold yellow]🧠 memory[/]", _render_memory),
        "activity":  ("[bold yellow]📜 activity[/]", _render_activity),
    }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Grid(id="grid"):
            for pid, (title, _) in self.PANELS.items():
                yield Panel("", id=pid, title=title, border_style="magenta")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🖤 BAW"
        self.sub_title = "dashboard"
        self._refresh()
        self.set_interval(5, self._refresh)

    def action_refresh_data(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        for pid, (_, renderer) in self.PANELS.items():
            try:
                p = self.query_one(f"#{pid}", Panel)
                p.renderable = renderer()
                p.refresh()
            except Exception:
                pass


def cmd_dashboard():
    BAWDashboard().run()
