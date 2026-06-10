"""baw dashboard — live multi-panel TUI dashboard powered by Textual.
Purple + Gold theme. 5-second auto-refresh.
Shows: system health, models, sessions, memory, connectors, activity feed.
"""
from __future__ import annotations
import os, json, time, re
from pathlib import Path
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, RichLog
from textual.binding import Binding
from rich.table import Table
from rich.panel import Panel
from rich.align import Align

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
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)}s ago"
    if diff < 3600:
        return f"{int(diff/60)}m ago"
    if diff < 86400:
        return f"{int(diff/3600)}h ago"
    return f"{int(diff/86400)}d ago"


# ── Dashboard App ────────────────────────────────────────────────────

class BAWDashboard(App):
    """Live multi-panel TUI for BAW Agent Platform."""

    CSS = """
    Screen {
        background: #0d0d12;
    }
    Header {
        background: #131320;
        color: magenta;
        dock: top;
    }
    Footer {
        background: #131320;
        color: #666;
        dock: bottom;
    }
    #grid {
        layout: grid;
        grid-size: 3 2;
        grid-gutter: 1;
        margin: 1;
        height: 1fr;
    }
    #topleft, #topcenter, #topright, #botleft, #botcenter, #botright {
        border: solid #2a1535;
        background: #131320;
        padding: 1;
        overflow: auto;
    }
    RichLog {
        background: #131320;
        min-height: 3;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="grid"):
            yield RichLog(id="topleft", highlight=True, markup=True, wrap=True)
            yield RichLog(id="topcenter", highlight=True, markup=True, wrap=True)
            yield RichLog(id="topright", highlight=True, markup=True, wrap=True)
            yield RichLog(id="botleft", highlight=True, markup=True, wrap=True)
            yield RichLog(id="botcenter", highlight=True, markup=True, wrap=True)
            yield RichLog(id="botright", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🖤 BAW Dashboard"
        self.sub_title = "Black And White"
        self._refresh()
        self.set_interval(5, self._refresh)

    def action_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self._panel_system(self.query_one("#topleft", RichLog))
        self._panel_models(self.query_one("#topcenter", RichLog))
        self._panel_connectors(self.query_one("#topright", RichLog))
        self._panel_sessions(self.query_one("#botleft", RichLog))
        self._panel_memory(self.query_one("#botcenter", RichLog))
        self._panel_activity(self.query_one("#botright", RichLog))

    # ── Panel: System ────────────────────────────────────────────────

    def _panel_system(self, log: RichLog):
        t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        t.add_column(style="bold magenta", width=14)
        t.add_column(style="white")

        # Container uptime from /proc/uptime
        uptime = "—"
        try:
            with open("/proc/uptime") as f:
                sec = int(float(f.read().split()[0]))
            d, h = divmod(sec, 86400)
            h, m = divmod(sec // 3600, 60)
            if d:
                uptime = f"{d}d {h}h"
            elif h:
                uptime = f"{h}h {m}m"
            else:
                uptime = f"{m}m"
        except Exception:
            pass

        t.add_row("🖥 Host", os.uname().nodename)
        t.add_row("🔄 Uptime", uptime)
        t.add_row("🐍 Python", f"BAW on {os.uname().sysname}")
        t.add_row("🕐 Updated", datetime.now().strftime("%H:%M:%S"))

        log.clear()
        log.write(Panel(t, title="[bold yellow]⚙ System[/]", border_style="magenta"))

    # ── Panel: Models ────────────────────────────────────────────────

    def _panel_models(self, log: RichLog):
        cfg = _parse_yaml(BAW_HOME / "config.yaml")
        providers = cfg.get("providers", {})
        caps = cfg.get("capabilities", {})
        default = cfg.get("model", {}).get("default", "—")

        t = Table(box=None, padding=(0, 1), expand=True, show_header=True)
        t.add_column("Provider", style="magenta")
        t.add_column("Model", style="white")
        t.add_column("Ctx", style="dim", justify="right")
        t.add_column("", style="yellow", width=3)

        for pname, pinfo in providers.items():
            for m in pinfo.get("models", []):
                mid = m.get("id", "?")
                ctx = m.get("context_window", 0)
                ctx_str = f"{ctx//1000}K" if ctx >= 1000 else str(ctx)
                star = "★" if mid == default else ""
                t.add_row(pname, mid, ctx_str, star)

        # Capability routing subtitle
        routes = []
        for cap in ("chat", "vision", "tts", "stt", "image_generation"):
            cap_cfg = caps.get(cap, {})
            model = cap_cfg.get("model", "") if isinstance(cap_cfg, dict) else ""
            if model:
                routes.append(f"[dim]{cap}:[/] [white]{model}[/]")

        log.clear()
        log.write(Panel(
            t,
            title="[bold yellow]🤖 Models[/]",
            border_style="magenta",
            subtitle="  ".join(routes) if routes else None,
        ))

    # ── Panel: Connectors ────────────────────────────────────────────

    def _panel_connectors(self, log: RichLog):
        telegram_env = BAW_HOME / "telegram.env"
        t = Table(show_header=False, box=None, padding=(0, 2), expand=True)
        t.add_column(style="bold", width=3)
        t.add_column(style="yellow")
        t.add_column(style="dim")

        if telegram_env.exists():
            content = telegram_env.read_text()
            if "BAW_TELEGRAM_TOKEN" in content:
                t.add_row("●", "Telegram", "Connected")
                m = re.search(r'BAW_TELEGRAM_TOKEN=(\d+):', content)
                if m:
                    t.add_row("", f"[dim]bot{m.group(1)}...[/]", "")
                # Message count from sessions
                sd = BAW_HOME / "sessions"
                msg_count = 0
                if sd.exists():
                    for f in sd.glob("*.jsonl"):
                        msg_count += len(f.read_text().splitlines())
                t.add_row("", f"[dim]Session msgs: {msg_count}[/]", "")
            else:
                t.add_row("●", "Telegram", "[red]No token[/]")
        else:
            t.add_row("○", "Telegram", "[red]Not configured[/]")

        log.clear()
        log.write(Panel(t, title="[bold yellow]📡 Connectors[/]", border_style="magenta"))

    # ── Panel: Sessions ──────────────────────────────────────────────

    def _panel_sessions(self, log: RichLog):
        sd = BAW_HOME / "sessions"
        t = Table(box=None, padding=(0, 1), expand=True, show_header=True)
        t.add_column("Session", style="magenta", width=14)
        t.add_column("Msgs", style="white", justify="right", width=5)
        t.add_column("Last", style="dim")

        if sd.exists():
            files = sorted(sd.glob("*.jsonl"), key=os.path.getmtime, reverse=True)[:8]
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
        log.write(Panel(t, title="[bold yellow]📂 Sessions[/]", border_style="magenta"))

    # ── Panel: Memory ─────────────────────────────────────────────────

    def _panel_memory(self, log: RichLog):
        mem_dir = BAW_HOME / "memory"
        t = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        t.add_column(style="bold magenta", width=14)
        t.add_column(style="white")

        if mem_dir.exists():
            store = mem_dir / "store.jsonl"
            memories = _load_jsonl(store) if store.exists() else []
            total = len(memories)
            size = sum(f.stat().st_size for f in mem_dir.rglob("*") if f.is_file())

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
        log.write(Panel(t, title="[bold yellow]🧠 Memory[/]", border_style="magenta"))

    # ── Panel: Activity ──────────────────────────────────────────────

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
            title="[bold yellow]📜 Activity[/]",
            border_style="magenta",
        ))


def cmd_dashboard():
    BAWDashboard().run()
