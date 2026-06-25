from __future__ import annotations
"""baw soul — view/edit BAW's personality (SOUL.md)."""
import os
import subprocess
from pathlib import Path
from rich.panel import Panel
from rich.markdown import Markdown
from cli import console

BAW_HOME = Path.home() / ".baw"
SOUL_PATH = BAW_HOME / "SOUL.md"


def cmd_soul(subcommand: str | None = None):
    if subcommand is None or subcommand == "show":
        _soul_show()
    elif subcommand == "edit":
        _soul_edit()
    else:
        console.print(f"[baw.error]Unknown subcommand:[/baw.error] {subcommand}")


def _soul_show():
    if not SOUL_PATH.exists():
        console.print("[baw.error]SOUL.md not found.[/baw.error]")
        return

    content = SOUL_PATH.read_text()
    md = Markdown(content)
    console.print(Panel(md, title="🧠 BAW SOUL", border_style="baw.border",
                        subtitle=f"{SOUL_PATH}"))


def _soul_edit():
    editor = os.environ.get("EDITOR", "nano")
    subprocess.call([editor, str(SOUL_PATH)])
