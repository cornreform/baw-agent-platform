"""baw dashboard — live TUI dashboard powered by Textual."""
from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Label, DataTable
from textual.reactive import reactive
from textual.timer import Timer

BAW_HOME = Path.home() / ".baw"


class StatusCard(Static):
    """A card displaying a key metric."""
    value = reactive("—")
    label_text = ""

    def __init__(self, label: str, value: str = "—", color: str = "cyan"):
        super().__init__()
        self.label_text = label
        self.value = value
        self.color = color

    def render(self) -> str:
        return f"[bold {self.color}]{self.value}[/bold {self.color}]\n[dim]{self.label_text}[/dim]"


class BAWDashboard(App):
    """Live TUI dashboard for BAW Agent Platform."""

    CSS = """
    Screen {
        background: #0d1117;
    }
    Header {
        background: #161b22;
        color: #58a6ff;
    }
    Footer {
        background: #161b22;
        color: #8b949e;
    }
    #metrics {
        height: 6;
        margin: 1 2;
    }
    StatusCard {
        width: 1fr;
        height: 5;
        padding: 1 2;
        border: solid #30363d;
        content-align: center middle;
    }
    #main {
        margin: 0 2;
    }
    #left-panel {
        width: 2fr;
    }
    #right-panel {
        width: 1fr;
    }
    DataTable {
        height: 12;
        border: solid #30363d;
        margin-bottom: 1;
    }
    #activity {
        height: 1fr;
        border: solid #30363d;
        margin-top: 1;
    }
    #connectors {
        height: 8;
        border: solid #30363d;
        padding: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Horizontal(
            StatusCard("Uptime", "—", "green"),
            StatusCard("Sessions", "—", "blue"),
            StatusCard("Messages", "—", "yellow"),
            StatusCard("Memory", "—", "magenta"),
            id="metrics",
        )
        with Horizontal(id="main"):
            with Vertical(id="left-panel"):
                yield DataTable(id="sessions-table")
            with Vertical(id="right-panel"):
                yield Static("[bold]📡 Connectors[/bold]", id="connectors")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(5, self._refresh)

    def _refresh(self) -> None:
        self._update_metrics()
        self._update_connectors()

    def _update_metrics(self) -> None:
        cards = self.query(StatusCard)
        cards_t = list(cards)

        # Uptime
        uptime = _get_uptime()
        cards_t[0].value = uptime

        # Sessions
        sessions_dir = BAW_HOME / "sessions"
        if sessions_dir.exists():
            count = len(list(sessions_dir.glob("*.jsonl")))
            cards_t[1].value = str(count)
        else:
            cards_t[1].value = "0"

        # Messages (approximate from sessions)
        total_msgs = 0
        if sessions_dir.exists():
            for f in sessions_dir.glob("*.jsonl"):
                total_msgs += len(f.read_text().splitlines())
        cards_t[2].value = str(total_msgs)

        # Memory
        mem_dir = BAW_HOME / "memory"
        if mem_dir.exists():
            size = sum(f.stat().st_size for f in mem_dir.rglob("*") if f.is_file())
            if size > 1_000_000:
                cards_t[3].value = f"{size / 1_000_000:.1f} MB"
            else:
                cards_t[3].value = f"{size / 1000:.0f} KB"

    def _update_connectors(self) -> None:
        connector = self.query_one("#connectors", Static)
        lines = ["[bold]📡 Connectors[/bold]", ""]

        telegram_env = BAW_HOME / "telegram.env"
        if telegram_env.exists():
            token_line = telegram_env.read_text().strip()
            if "BAW_TELEGRAM_TOKEN" in token_line:
                lines.append("[green]● Telegram[/green] [dim]Connected[/dim]")
                lines.append(f"[dim]   bot881955...npk[/dim]")
            else:
                lines.append("[red]● Telegram[/red] [dim]No token[/dim]")
        else:
            lines.append("[red]● Telegram[/red] [dim]Not configured[/dim]")

        connector.update("\n".join(lines))


def _get_uptime() -> str:
    try:
        with open("/proc/uptime") as f:
            uptime_sec = float(f.read().split()[0])
        m, s = divmod(int(uptime_sec), 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        if d:
            return f"{d}d {h}h"
        elif h:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "—"


def cmd_dashboard():
    app = BAWDashboard()
    app.run()
