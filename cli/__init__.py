"""
BAW CLI — Beautiful terminal interface for BAW Agent Platform.
Powered by Rich for gorgeous terminal output.
"""
from rich.console import Console
from rich.theme import Theme

# ── BAW Color Theme ──
BAW_THEME = Theme({
    "baw.primary": "cyan",
    "baw.success": "green",
    "baw.warning": "yellow",
    "baw.error": "red bold",
    "baw.muted": "dim",
    "baw.accent": "magenta",
    "baw.highlight": "bright_cyan",
    "baw.logo": "bold cyan",
    "baw.heading": "bold white",
    "baw.border": "cyan",
    "baw.key": "bright_blue",
    "baw.value": "white",
    "baw.number": "bright_green",
})

console = Console(theme=BAW_THEME)

BAW_LOGO = r"""
[bold cyan]
 ██████╗  █████╗ ██╗    ██╗
 ██╔══██╗██╔══██╗██║    ██║
 ██████╔╝███████║██║ █╗ ██║
 ██╔══██╗██╔══██║██║███╗██║
 ██████╔╝██║  ██║╚███╔███╔╝
 ╚═════╝ ╚═╝  ╚═╝ ╚══╝╚══╝
[/bold cyan]
[dim]Black And White — Agent Platform[/dim]
"""
