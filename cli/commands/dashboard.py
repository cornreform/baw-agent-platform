"""baw dashboard — responsive TUI dashboard powered by Textual.
Purple + Gold theme. 2-column layout adapts to small terminals.
Panels auto-scroll when content overflows.
"""
from __future__ import annotations
import os, json, time, re
from pathlib import Path
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Header, Footer, RichLog, Label
from textual.binding import Binding
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
    diff = time.time() - ts
    if diff < 60: return f"{int(diff)}s ago"
    if diff < 3600: return f"{int(diff/60)}m ago"
    if diff < 86400: return f"{int(diff/3600)}h ago"
    return f"{int(diff/86400)}d ago"


# ── Dashboard App ────────────────────────────────────────────────────

class BAWDashboard(App):
    """Responsive 2-column dashboard with scrolling panels."""

    CSS = """
    Screen { background: #0d0d12; }
    Header { background: #131320; color: magenta; dock: top; }
    Footer { background: #131320; color: #666; dock: bottom; }

    #main {
        layout: vertical;
        height: 1fr;
        margin: 0 1;
    }

    .row {
        height: 1fr;
        layout: horizontal;
    }

    .panel {
        width: 1fr;
        height: 1fr;
        border: solid #2a1535;
        background: #131320;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="main"):
            with Horizontal(classes="row"):
                yield Label(id="panel_system", classes="panel")
                yield Label(id="panel_sessions", classes="panel")
            with Horizontal(classes="row"):
                yield Label(id="panel_models", classes="panel")
                yield Label(id="panel_memory", classes="panel")
            with Horizontal(classes="row"):
                yield Label(id="panel_connectors", classes="panel")
                yield Label(id="panel_activity", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🖤 BAW Dashboard"
        self.sub_title = "Black And White — r:refresh q:quit"
        self._refresh()
        self.set_interval(5, self._refresh)

    def action_refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#panel_system", Label).update(self._render_system())
        self.query_one("#panel_sessions", Label).update(self._render_sessions())
        self.query_one("#panel_models", Label).update(self._render_models())
        self.query_one("#panel_memory", Label).update(self._render_memory())
        self.query_one("#panel_connectors", Label).update(self._render_connectors())
        self.query_one("#panel_activity", Label).update(self._render_activity())

    # ═══════════════════════════════════════════════════════════════════
    # Panel: System
    # ═══════════════════════════════════════════════════════════════════
    def _render_system(self) -> str:
        uptime = "—"
        try:
            with open("/proc/uptime") as f:
                sec = int(float(f.read().split()[0]))
            d, h = divmod(sec // 3600, 24)
            h, m = divmod(sec // 60, 60)
            if d: uptime = f"{d}d {h}h"
            elif h: uptime = f"{h}h {m}m"
            else: uptime = f"{m}m"
        except Exception: pass

        lines = [
            f"Host     [bold yellow]{os.uname().nodename}[/]",
            f"System   [bold yellow]{os.uname().sysname}[/]",
            f"Uptime   [bold yellow]{uptime}[/]",
            f"Updated  [dim]{datetime.now().strftime('%H:%M:%S')}[/]",
        ]
        return self._mk_panel("⚙  System", "\n".join(lines))

    # ═══════════════════════════════════════════════════════════════════
    # Panel: Sessions
    # ═══════════════════════════════════════════════════════════════════
    def _render_sessions(self) -> str:
        sd = BAW_HOME / "sessions"
        lines = []
        if sd.exists():
            files = sorted(sd.glob("*.jsonl"), key=os.path.getmtime, reverse=True)[:6]
            for f in files:
                cnt = len(f.read_text().splitlines())
                sid = f.stem[:14]
                ago = _relative_time(os.path.getmtime(f))
                lines.append(f"[magenta]{sid}[/]  [yellow]{cnt}[/] msgs  [dim]{ago}[/]")
        if not lines:
            lines = ["[dim]No sessions yet[/]"]
        return self._mk_panel("📂  Sessions", "\n".join(lines))

    # ═══════════════════════════════════════════════════════════════════
    # Panel: Models
    # ═══════════════════════════════════════════════════════════════════
    def _render_models(self) -> str:
        cfg = _parse_yaml(BAW_HOME / "config.yaml")
        providers = cfg.get("providers", {})
        caps = cfg.get("capabilities", {})
        default = cfg.get("model", {}).get("default", "—")
        lines = []
        for pname, pinfo in providers.items():
            for m in pinfo.get("models", []):
                mid = m.get("id", "?")
                ctx = m.get("context_window", 0)
                ctx_s = f"{ctx//1000}K" if ctx >= 1000 else str(ctx)
                star = " ★" if mid == default else ""
                lines.append(f"[yellow]{pname}[/]/[magenta]{mid}[/] [dim]{ctx_s}[/]{star}")

        routes = []
        for cap in ("chat", "vision", "tts", "stt", "image_generation"):
            cc = caps.get(cap, {})
            if isinstance(cc, dict) and cc.get("model"):
                routes.append(f"[dim]{cap}:[/] [white]{cc['model']}[/]")
        if routes:
            lines.append("")
            lines.append("[bold yellow]Capability Routing[/]")
            lines.append("  ".join(routes))

        return self._mk_panel("🤖  Models", "\n".join(lines))

    # ═══════════════════════════════════════════════════════════════════
    # Panel: Memory
    # ═══════════════════════════════════════════════════════════════════
    def _render_memory(self) -> str:
        mem_dir = BAW_HOME / "memory"
        lines = []
        if mem_dir.exists():
            store = mem_dir / "store.jsonl"
            memories = _load_jsonl(store) if store.exists() else []
            total = len(memories)
            size = sum(f.stat().st_size for f in mem_dir.rglob("*") if f.is_file())
            scores = [m.get("score", 0) for m in memories if "score" in m]
            avg = sum(scores) / len(scores) if scores else 0
            high = sum(1 for s in scores if s > 0.7)
            med = sum(1 for s in scores if 0.3 <= s <= 0.7)
            low = sum(1 for s in scores if s < 0.3)

            lines = [
                f"Total      [yellow]{total}[/]",
                f"Size       [yellow]{_human_size(size)}[/]",
                f"Avg Score  [yellow]{avg:.2f}[/]",
                f"",
                f"High (>0.7)   [green]{high}[/]",
                f"Med  (0.3-7)  [yellow]{med}[/]",
                f"Low  (<0.3)   [red]{low}[/]",
            ]
        else:
            lines = ["[dim]No memory store[/]"]
        return self._mk_panel("🧠  Memory", "\n".join(lines))

    # ═══════════════════════════════════════════════════════════════════
    # Panel: Connectors
    # ═══════════════════════════════════════════════════════════════════
    def _render_connectors(self) -> str:
        telegram_env = BAW_HOME / "telegram.env"
        lines = []
        if telegram_env.exists():
            content = telegram_env.read_text()
            if "BAW_TELEGRAM_TOKEN" in content:
                m = re.search(r'BAW_TELEGRAM_TOKEN=([0-9]+)', content)
                tok = f"bot{m.group(1)[:6]}..." if m else "—"
                sd = BAW_HOME / "sessions"
                msgs = sum(len(f.read_text().splitlines()) for f in sd.glob("*.jsonl")) if sd.exists() else 0
                lines = [
                    "[green]● Telegram[/] [dim]Connected[/]",
                    f"  [dim]{tok}[/]",
                    f"  [dim]Session msgs: {msgs}[/]",
                ]
            else:
                lines = ["[red]● Telegram[/] [dim]No token set[/]"]
        else:
            lines = ["[red]○ Telegram[/] [dim]Not configured[/]"]
        return self._mk_panel("📡  Connectors", "\n".join(lines))

    # ═══════════════════════════════════════════════════════════════════
    # Panel: Activity
    # ═══════════════════════════════════════════════════════════════════
    def _render_activity(self) -> str:
        logs_dir = BAW_HOME / "logs"
        lines = []
        if logs_dir.exists():
            for lf in sorted(logs_dir.glob("*.log"), key=os.path.getmtime, reverse=True)[:1]:
                for line in lf.read_text().splitlines()[-8:]:
                    lines.append(f"[dim]{line[:90]}[/]")
        if not lines:
            lines = ["[dim]No log entries yet[/]"]
        return self._mk_panel("📜  Activity", "\n".join(lines))

    @staticmethod
    def _mk_panel(title: str, body: str) -> str:
        return f"[bold yellow]{title}[/]\n[dim]─────────────────────────────[/]\n{body}"


def cmd_dashboard():
    BAWDashboard().run()
