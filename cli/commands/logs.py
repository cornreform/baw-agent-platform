"""baw logs — view bot logs (journalctl, Docker, or file)."""
import subprocess
import shutil
from pathlib import Path
from rich.panel import Panel
from rich.syntax import Syntax
from cli import console

BAW_HOME = Path.home() / ".baw"
LOG_PATH = BAW_HOME / "logs" / "baw.log"
_SERVICE = "baw"
_HAS_DOCKER = shutil.which("docker") is not None
_HAS_JOURNALCTL = shutil.which("journalctl") is not None
_IS_BARE = Path("/run/systemd/system").exists()


def cmd_logs(lines: int = 50, follow: bool = False):
    if follow:
        _logs_follow()
    else:
        _logs_show(lines)


def _logs_show(lines: int = 50):
    # 1. Bare-metal: journalctl
    if _IS_BARE and _HAS_JOURNALCTL:
        try:
            result = subprocess.run(
                ["journalctl", "-u", _SERVICE, "-n", str(lines), "--no-pager", "-q"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                syntax = Syntax(result.stdout, "log", theme="monokai",
                                line_numbers=True, background_color="default")
                console.print(Panel(syntax, title=f"📜 BAW Logs [bare] (last {lines} lines)",
                                    border_style="baw.border"))
                return
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 2. Docker logs
    if _HAS_DOCKER:
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(lines), "baw-telegram"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                syntax = Syntax(result.stdout, "log", theme="monokai",
                                line_numbers=True, background_color="default")
                console.print(Panel(syntax, title=f"📜 BAW Docker Logs (last {lines} lines)",
                                    border_style="baw.border"))
                return
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 3. Fallback: log file
    if not LOG_PATH.exists():
        console.print(f"[baw.muted]No logs found[/baw.muted]")
        return

    file_lines = LOG_PATH.read_text().splitlines()[-lines:]
    syntax = Syntax("\n".join(file_lines), "log", theme="monokai",
                    line_numbers=True, background_color="default")
    console.print(Panel(syntax, title=f"📜 BAW Logs (last {lines} lines)", border_style="baw.border"))


def _logs_follow():
    # 1. Bare-metal: journalctl -f
    if _IS_BARE and _HAS_JOURNALCTL:
        console.print("[baw.muted]Tailing journalctl... Ctrl+C to stop.[/baw.muted]")
        try:
            subprocess.call(["journalctl", "-u", _SERVICE, "-f", "--no-pager"])
        except KeyboardInterrupt:
            pass
        return

    # 2. Docker logs -f
    if _HAS_DOCKER:
        console.print("[baw.muted]Tailing Docker logs... Ctrl+C to stop.[/baw.muted]")
        try:
            subprocess.call(["docker", "logs", "-f", "baw-telegram"])
        except FileNotFoundError:
            pass
        except KeyboardInterrupt:
            pass
        return

    # 3. Fallback: file tail
    console.print(f"[baw.muted]Tailing {LOG_PATH}... Ctrl+C to stop.[/baw.muted]")
    if LOG_PATH.exists():
        try:
            subprocess.call(["tail", "-f", str(LOG_PATH)])
        except KeyboardInterrupt:
            pass
