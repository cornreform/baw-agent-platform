"""baw logs — view bot logs (Docker or file)."""
import subprocess
from pathlib import Path
from rich.panel import Panel
from rich.syntax import Syntax
from cli import console

BAW_HOME = Path.home() / ".baw"
LOG_PATH = BAW_HOME / "logs" / "baw.log"


def cmd_logs(lines: int = 50, follow: bool = False):
    """Show BAW logs. Tries Docker first, falls back to log file."""
    if follow:
        _logs_follow()
    else:
        _logs_show(lines)


def _logs_show(lines: int = 50):
    # Try Docker logs first
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

    # Fallback: log file
    if not LOG_PATH.exists():
        console.print(f"[baw.muted]No logs found at {LOG_PATH}[/baw.muted]")
        return

    file_lines = LOG_PATH.read_text().splitlines()[-lines:]
    syntax = Syntax("\n".join(file_lines), "log", theme="monokai",
                    line_numbers=True, background_color="default")
    console.print(Panel(syntax, title=f"📜 BAW Logs (last {lines} lines)", border_style="baw.border"))


def _logs_follow():
    console.print("[baw.muted]Tailing Docker logs... Ctrl+C to stop.[/baw.muted]")
    console.print()
    try:
        subprocess.call(["docker", "logs", "-f", "baw-telegram"])
    except FileNotFoundError:
        # Fallback to file tail
        if LOG_PATH.exists():
            subprocess.call(["tail", "-f", str(LOG_PATH)])
    except KeyboardInterrupt:
        pass
