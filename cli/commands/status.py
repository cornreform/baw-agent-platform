from __future__ import annotations
"""baw status — bot health + connector info."""
import os
import re
import subprocess
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box
from cli import console, BAW_LOGO

BAW_HOME = Path.home() / ".baw"


def _uptime() -> str:
    """Get uptime — systemd first, Docker second, proc fallback."""
    import shutil

    _HAS_SYSTEMCTL = shutil.which("systemctl") is not None
    _HAS_DOCKER = shutil.which("docker") is not None
    _SERVICE = os.environ.get("BAW_SERVICE", "baw")

    # 1. systemctl (bare-metal)
    if _HAS_SYSTEMCTL:
        try:
            result = subprocess.run(
                ["systemctl", "show", _SERVICE, "--property=ActiveEnterTimestamp"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split("\n"):
                if line.startswith("ActiveEnterTimestamp="):
                    started_str = line.split("=", 1)[1].strip()
                    if started_str:
                        import datetime
                        parts = started_str.split()
                        if len(parts) >= 3:
                            dt = datetime.datetime.fromisoformat(f"{parts[1]} {parts[2]}+08:00")
                            delta = datetime.datetime.now(datetime.timezone.utc) - dt
                            total_sec = int(delta.total_seconds())
                            d, h = divmod(total_sec, 86400)
                            h, m = divmod(h, 3600)
                            m, s = divmod(m, 60)
                            if d:
                                return f"{d}d {h}h {m}m"
                            elif h:
                                return f"{h}h {m}m"
                            return f"{m}m {s}s"
        except Exception:
            pass

    # 2. Docker inspect
    if _HAS_DOCKER:
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.StartedAt}}", "baw-telegram"],
                capture_output=True, text=True, timeout=5,
            )
            started = result.stdout.strip()
            if started:
                import datetime
                started_dt = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
                now = datetime.datetime.now(datetime.timezone.utc)
                delta = now - started_dt
                total_sec = int(delta.total_seconds())
                d, h = divmod(total_sec, 86400)
                h, m = divmod(h, 3600)
                m, s = divmod(m, 60)
                if d:
                    return f"{d}d {h}h {m}m"
                elif h:
                    return f"{h}h {m}m"
                return f"{m}m {s}s"
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Fallback: try PID file
    try:
        pid = int(subprocess.run(
            ["cat", "/tmp/baw.pid"], capture_output=True, text=True, timeout=2
        ).stdout.strip() or "0")
        if pid and Path(f"/proc/{pid}/stat").exists():
            import time
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
            "mode": cfg.get("mode", "N/A"),
            "version": _get_version(),
        }
    return {"model": "N/A", "providers": 0, "tone": "N/A", "mode": "N/A", "version": "?"}


def _get_version() -> str:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )
        return result.stdout.strip() or "?"
    except Exception:
        return "?"


def _masked_token() -> str | None:
    """Read Telegram token from telegram.env, return masked version."""
    telegram_env = BAW_HOME / "telegram.env"
    if not telegram_env.exists():
        return None
    content = telegram_env.read_text()
    m = re.search(r'BAW_TELEGRAM_TOKEN=(\S+)', content)
    if m:
        token = m.group(1)
        if len(token) > 8:
            return f"{token[:4]}...{token[-4:]}"
        return "***"
    return "***"


def cmd_status():
    console.print(BAW_LOGO)
    console.print()

    cfg = _config_summary()
    health_table = Table(box=box.ROUNDED, border_style="baw.border", show_header=False)
    health_table.add_column("Key", style="baw.key", width=18)
    health_table.add_column("Value", style="baw.value")

    health_table.add_row("Version", f"[baw.accent]{cfg['version']}[/baw.accent]")
    health_table.add_row("Status", "[baw.success]● Online[/baw.success]")
    health_table.add_row("Uptime", _uptime())
    health_table.add_row("Default Model", cfg["model"])
    health_table.add_row("Mode", cfg["mode"])
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

    console.print()
    console.print("[baw.muted]🔧 Run[/baw.muted] [baw.highlight]baw --help[/baw.highlight] [baw.muted]to see all commands[/baw.muted]")
