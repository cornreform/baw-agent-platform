"""baw logs — view bot logs."""
from pathlib import Path
from rich.panel import Panel
from rich.syntax import Syntax
from cli import console

BAW_HOME = Path.home() / ".baw"
LOG_PATH = BAW_HOME / "logs" / "baw.log"


def cmd_logs(follow: bool = False):
    if follow:
        _logs_follow()
    else:
        _logs_show()


def _logs_show():
    if not LOG_PATH.exists():
        console.print("[baw.muted]No logs found at {0}[/baw.muted]".format(LOG_PATH))
        return

    # Show last 50 lines
    lines = LOG_PATH.read_text().splitlines()[-50:]
    syntax = Syntax("\n".join(lines), "log", theme="monokai",
                    line_numbers=True, background_color="default")
    console.print(Panel(syntax, title="📜 BAW Logs (last 50 lines)", border_style="baw.border"))


def _logs_follow():
    import subprocess
    console.print("[baw.muted]Tailing logs... Ctrl+C to stop.[/baw.muted]")
    console.print()
    try:
        subprocess.call(["tail", "-f", str(LOG_PATH)])
    except KeyboardInterrupt:
        pass
