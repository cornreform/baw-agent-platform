"""baw status — bot health + connector info."""
import os
import time
import re
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box
from cli import console, BAW_LOGO

BAW_HOME = Path.home() / ".baw"


def _uptime() -> str:
    """Read process uptime from /proc."""
    try:
        pid = int(os.popen("cat /tmp/baw.pid 2>/dev/null || echo 0").read().strip())
        if pid:
            stat = Path(f"/proc/{pid}/stat").read_text().split()
            start_ticks = int(stat[21])
            uptime_sec = time.time() - (start_ticks / os.sysconf(os.sysconf_names['SC_CLK_TCK']))
            m, s = divmod(int(uptime_sec), 60)
            h, m = divmod(m, 60)
            d, h = divmod(h, 24)
            if d:
                return f"{d}d {h}h {m}m"
            elif h:
                return f"{h}h {m}m"
            return f"{m}m {s}s"
    except Exception:
        pass
    return "unknown"


def _session_count() -> int:
    sessions_dir = BAW_HOME / "sessions"
    if sessions_dir.exists():
        return len(list(sessions_dir.glob("*.jsonl")))
    return 0


def _memory_size() -> str:
    mem_dir = BAW_HOME / "memory"
    if mem_dir.exists():
        total = sum(f.stat().st_size for f in mem_dir.rglob("*") if f.is_file())
        if total > 1_000_000:
            return f"{total / 1_000_000:.1f} MB"
        return f"{total / 1000:.0f} KB"
    return "0 KB"


def _config_summary() -> dict:
    import yaml
    cfg_path = BAW_HOME / "config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        return {
            "model": cfg.get("model", {}).get("default", "N/A"),
            "providers": len(cfg.get("providers", {})),
            "tone": cfg.get("tone", {}).get("default", "N/A"),
        }
    return {"model": "N/A", "providers": 0, "tone": "N/A"}


def _masked_token() -> str:
    """Read Telegram token from telegram.env, return masked version."""
    telegram_env = BAW_HOME / "telegram.env"
    if not telegram_env.exists():
        return None
    content = telegram_env.read_text()
    m = re.search(r'BAW_TELEGRAM_TOKEN=(bot\d+):([A-Za-z0-9\-_]+)', content)
    if m:
        bot_id = m.group(1)
        token = m.group(2)
        if len(token) > 8:
            return f"{bot_id}:{token[:4]}...{token[-4:]}"
        return f"{bot_id}:***"
    return "***"


def cmd_status():
    console.print(BAW_LOGO)
    console.print()

    # ── Health panel ──
    cfg = _config_summary()
    health_table = Table(box=box.ROUNDED, border_style="baw.border", show_header=False)
    health_table.add_column("Key", style="baw.key", width=18)
    health_table.add_column("Value", style="baw.value")

    health_table.add_row("Status", "[baw.success]● Online[/baw.success]")
    health_table.add_row("Uptime", _uptime())
    health_table.add_row("Default Model", cfg["model"])
    health_table.add_row("Providers", str(cfg["providers"]))
    health_table.add_row("Active Tone", cfg["tone"])
    health_table.add_row("Sessions", str(_session_count()))
    health_table.add_row("Memory", _memory_size())

    console.print(Panel(health_table, title="🩺 BAW Health", border_style="baw.border"))

    # ── Connector panel ──
    conn_table = Table(box=box.ROUNDED, border_style="baw.border")
    conn_table.add_column("Connector", style="baw.highlight")
    conn_table.add_column("Status", style="baw.success")
    conn_table.add_column("Token", style="baw.muted")

    token_display = _masked_token()
    has_token = token_display is not None
    conn_table.add_row(
        "Telegram",
        "[baw.success]✓ Connected[/baw.success]" if has_token else "[baw.error]✗ No token[/baw.error]",
        token_display or "N/A",
    )

    console.print(Panel(conn_table, title="📡 Connectors", border_style="baw.border"))

    # ── Quick tip ──
    console.print()
    console.print("[baw.muted]🔧 Run[/baw.muted] [baw.highlight]baw --help[/baw.highlight] [baw.muted]to see all commands[/baw.muted]")
