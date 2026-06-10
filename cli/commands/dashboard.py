"""baw dashboard — live multi-panel TUI dashboard powered by Textual.

Purple (#8b5cf6) + Gold (#e2b714) theme. 5-second auto-refresh.
Shows: system health, models, sessions, memory, connectors, activity feed.
"""
from __future__ import annotations
import os, json, time, glob as gmod
from pathlib import Path
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.widgets import Header, Footer, Static, Label, RichLog
from textual.reactive import reactive
from textual.binding import Binding
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.console import RenderableType
from rich.align import Align

BAW_HOME = Path.home() / ".baw"
PURPLE = "#8b5cf6"
GOLD = "#e2b714"
DARK_BG = "#0d0d12"
PANEL_BG = "#131320"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_yaml_config(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}

def _load_jsonl(path: Path) -> list[dict]:
    items = []
    if path.exists():
        for line in path.read_text().splitlines():
            try: items.append(json.loads(line))
            except Exception: pass
    return items

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

def _relative_time(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60: return f"{int(diff)}s ago"
    if diff < 3600: return f"{int(diff/60)}m ago"
    if diff < 86400: return f"{int(diff/3600)}h ago"
    return f"{int(diff/86400)}d ago"

def _get_container_uptime() -> str:
    try:
        with open("/proc/uptime") as f:
            sec = int(float(f.read().split()[0]))
        d, h = divmod(sec, 86400)
        h, m = divmod(h % 24, 60)
        if d: return f"{d}d {h}h"
        if h: return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "—"

def _get_cpu() -> str:
    try:
        with open("/proc/loadavg") as f:
            return f.read().split()[0]
    except Exception:
        return "—"

def _get_memory() -> str:
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    mem[k.strip()] = int(v.strip().split()[0])
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", 0)
        used = total - avail
        pct = (used / total * 100) if total else 0
        return f"{_human_size(used * 1024)} / {_human_size(total * 1024)} ({pct:.0f}%)"
    except Exception:
        return "—"


# ── Dashboard App ────────────────────────────────────────────────────────────

class BAWDashboard(App):
    """Live multi-panel TUI for BAW Agent Platform."""

    CSS = f"""
    Screen {{
        background: {DARK_BG};
    }}
    Header {{
        background: {PANEL_BG};
        color: {PURPLE};
        dock: top;
    }}
    Footer {{
        background: {PANEL_BG};
        color: #666;
        dock: bottom;
    }}
    #grid {{
        layout: grid;
        grid-size: 3 2;
        grid-gutter: 1;
        margin: 1;
        height: 1fr;
    }}
    #top-left, #top-center, #top-right, #bottom-left, #bottom-center, #bottom-right {{
        border: solid {PANEL_BG};
        background: {PANEL_BG};
        padding: 1;
        overflow: auto;
    }}
    #top-left {{ row-span: 2; }}
    ScrollView {{ background: {PANEL_BG}; }}
    RichLog {{ background: {PANEL_BG}; min-height: 3; }}
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh Now"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="grid"):
            yield RichLog(id="top-left", highlight=True, markup=True, wrap=True)
            yield RichLog(id="top-center", highlight=True, markup=True, wrap=True)
            yield RichLog(id="top-right", highlight=True, markup=True, wrap=True)
            yield RichLog(id="bottom-left", highlight=True, markup=True, wrap=True)
            yield RichLog(id="bottom-center", highlight=True, markup=True, wrap=True)
            yield RichLog(id="bottom-right", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🖤 BAW Dashboard"
        self.sub_title = f"{PURPLE} Black And White"
        self._refresh()
        self.set_interval(5, self._refresh)

    def action_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self._panel_system(self.query_one("#top-left", RichLog))
        self._panel_models(self.query_one("#top-center", RichLog))
        self._panel_connectors(self.query_one("#top-right", RichLog))
        self._panel_sessions(self.query_one("#bottom-left", RichLog))
        self._panel_memory(self.query_one("#bottom-center", RichLog))
        self._panel_activity(self.query_one("#bottom-right", RichLog))

    # ── Panel: System ────────────────────────────────────────────────────────

    def _panel_system(self, log: RichLog):
        t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        t.add_column(style=f"bold {PURPLE}", width=14)
        t.add_column(style="white")
        t.add_row("🖥 Host", os.uname().nodename)
        t.add_row("🔄 Uptime", _get_container_uptime())
        t.add_row("📊 CPU Load", _get_cpu())
        t.add_row("🧠 Memory", _get_memory())
        t.add_row("🐍 Python", f"BAW on {os.uname().sysname}")
        t.add_row("🕐 Refreshed", datetime.now().strftime("%H:%M:%S"))
        log.clear()
        log.write(Panel(t, title="[bold #e2b714]⚙ System[/]", border_style=PURPLE))

    # ── Panel: Models ────────────────────────────────────────────────────────

    def _panel_models(self, log: RichLog):
        cfg = _parse_yaml_config(BAW_HOME / "config.yaml")
        providers = cfg.get("providers", {})
        caps = cfg.get("capabilities", {})
        default = cfg.get("model", {}).get("default", "—")

        t = Table(box=None, padding=(0, 1), expand=True, show_header=True)
        t.add_column("Provider", style=PURPLE)
        t.add_column("Model", style="white")
        t.add_column("Ctx", style="dim", justify="right")
        t.add_column("", style=GOLD, width=3)

        for pname, pinfo in providers.items():
            for m in pinfo.get("models", []):
                mid = m.get("id", "?")
                ctx = m.get("context_window", 0)
                ctx_str = f"{ctx//1000}K" if ctx >= 1000 else str(ctx)
                star = "★" if mid == default else ""
                t.add_row(pname, mid, ctx_str, star)

        # Capability routing
        routes = []
        for cap in ("chat", "vision", "tts", "stt", "image_generation"):
            model = caps.get(cap, {}).get("model", "") if isinstance(caps.get(cap), dict) else ""
            if model:
                routes.append(f"[dim]{cap}:[/] [white]{model}[/]")

        panel_content = t
        if routes:
            panel_content = Align.center(t)
            log.clear()
            log.write(Panel(
                panel_content,
                title=f"[bold {GOLD}]🤖 Models[/]",
                border_style=PURPLE,
                subtitle="\n".join(routes),
            ))
        else:
            log.clear()
            log.write(Panel(t, title=f"[bold {GOLD}]🤖 Models[/]", border_style=PURPLE))

    # ── Panel: Connectors ────────────────────────────────────────────────────

    def _panel_connectors(self, log: RichLog):
        telegram_env = BAW_HOME / "telegram.env"
        t = Table(show_header=False, box=None, padding=(0, 2), expand=True)
        t.add_column(style="bold", width=3)
        t.add_column(style=GOLD)
        t.add_column(style="dim")

        if telegram_env.exists():
            content = telegram_env.read_text()
            if "BAW_TELEGRAM_TOKEN" in content:
                t.add_row("●", "Telegram", "Connected")
                # Extract partial token
                import re
                m = re.search(r'BAW_TELEGRAM_TOKEN=(\d+):', content)
                if m:
                    t.add_row("", f"[dim]bot{m.group(1)}...[/]", "")
                # Try to count recent messages
                sessions_dir = BAW_HOME / "sessions"
                msg_count = 0
                if sessions_dir.exists():
                    for f in sessions_dir.glob("*.jsonl"):
                        msg_count += len(f.read_text().splitlines())
                t.add_row("", f"[dim]Session msgs: {msg_count}[/]", "")
            else:
                t.add_row("●", "Telegram", "[red]No token[/]")
        else:
            t.add_row("○", "Telegram", "[red]Not configured[/]")

        log.clear()
        log.write(Panel(t, title=f"[bold {GOLD}]📡 Connectors[/]", border_style=PURPLE))

    # ── Panel: Sessions ──────────────────────────────────────────────────────

    def _panel_sessions(self, log: RichLog):
        sessions_dir = BAW_HOME / "sessions"
        t = Table(box=None, padding=(0, 1), expand=True, show_header=True)
        t.add_column("Session", style=PURPLE, width=14)
        t.add_column("Msgs", style="white", justify="right", width=5)
        t.add_column("Last Active", style="dim")

        if sessions_dir.exists():
            files = sorted(sessions_dir.glob("*.jsonl"), key=os.path.getmtime, reverse=True)[:8]
            for f in files:
                lines = f.read_text().splitlines()
                sid = f.stem[:16]
                mtime = _relative_time(os.path.getmtime(f))
                t.add_row(sid, str(len(lines)), mtime)
            if not files:
                t.add_row("[dim]No sessions[/]", "", "")
        else:
            t.add_row("[dim]No sessions[/]", "", "")

        log.clear()
        log.write(Panel(t, title=f"[bold {GOLD}]📂 Sessions[/]", border_style=PURPLE))

    # ── Panel: Memory ─────────────────────────────────────────────────────────

    def _panel_memory(self, log: RichLog):
        mem_dir = BAW_HOME / "memory"
        t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        t.add_column(style=f"bold {PURPLE}", width=14)
        t.add_column(style="white")

        if mem_dir.exists():
            store = mem_dir / "store.jsonl"
            edges = mem_dir / "edges.json"
            memories = _load_jsonl(store) if store.exists() else []
            total = len(memories)
            size = sum(f.stat().st_size for f in mem_dir.rglob("*") if f.is_file())

            # Score distribution
            scores = [m.get("score", 0) for m in memories if "score" in m]
            avg_score = sum(scores) / len(scores) if scores else 0
            high = sum(1 for s in scores if s > 0.7)
            med = sum(1 for s in scores if 0.3 <= s <= 0.7)
            low = sum(1 for s in scores if s < 0.3)

            t.add_row("Total", str(total))
            t.add_row("Size", _human_size(size))
            t.add_row("Avg Score", f"{avg_score:.2f}")
            t.add_row("High (>0.7)", f"[green]{high}[/]")
            t.add_row("Med  (0.3-0.7)", f"[yellow]{med}[/]")
            t.add_row("Low  (<0.3)", f"[red]{low}[/]")
        else:
            t.add_row("Status", "[dim]No memory store[/]")

        log.clear()
        log.write(Panel(t, title=f"[bold {GOLD}]🧠 Memory[/]", border_style=PURPLE))

    # ── Panel: Activity ──────────────────────────────────────────────────────

    def _panel_activity(self, log: RichLog):
        logs_dir = BAW_HOME / "logs"
        lines = []
        if logs_dir.exists():
            log_files = sorted(logs_dir.glob("*.log"), key=os.path.getmtime, reverse=True)
            for lf in log_files[:1]:
                content = lf.read_text().splitlines()
                for line in content[-12:]:
                    lines.append(f"[dim]{line[:100]}[/]")
        if not lines:
            lines = ["[dim]No log entries yet[/]"]

        log.clear()
        log.write(Panel(
            "\n".join(lines),
            title=f"[bold {GOLD}]📜 Activity[/]",
            border_style=PURPLE,
        ))


def cmd_dashboard():
    BAWDashboard().run()
