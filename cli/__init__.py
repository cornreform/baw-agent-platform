"""baw CLI — shared console with BAW Purple+Gold theme."""
from rich.console import Console
from rich.theme import Theme

BAW_THEME = Theme({
    "baw.brand": "bold #c77dff",
    "baw.gold": "bold #f0c040",
    "baw.purple": "#9b5de5",
    "baw.accent": "#7b2d8e",
    "baw.muted": "#8b949e",
    "baw.section": "bold #c77dff",
    "baw.dim": "dim #6e7681",
    "baw.success": "bold #3fb950",
    "baw.warning": "bold #d29922",
    "baw.error": "bold #f85149",
    "baw.highlight": "bold #f0c040",
    "baw.cmd": "bold #c77dff",
    "baw.desc": "#e6edf3",
    "baw.key": "bold #f0c040",
    "baw.val": "#e6edf3",
    "baw.header": "bold #c77dff on #1a1025",
    "panel.border": "#7b2d8e",
    "table.border": "#7b2d8e",
    "table.header": "bold #f0c040",
})

console = Console(theme=BAW_THEME)
