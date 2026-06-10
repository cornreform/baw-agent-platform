"""baw CLI — shared console with BAW Purple+Gold theme.
Uses Rich named colors for universal terminal compatibility (no hex fallback to blue).
"""
from rich.console import Console
from rich.theme import Theme

BAW_THEME = Theme({
    # Primary: purple/magenta (works on ALL terminals — 16/256/true color)
    "baw.brand":    "bold magenta",
    "baw.gold":     "bold yellow",
    "baw.purple":   "magenta",
    "baw.accent":   "dark_magenta",
    "baw.muted":    "#8b949e",
    "baw.section":  "bold magenta",
    "baw.dim":      "dim #6e7681",
    "baw.success":  "bold green",
    "baw.warning":  "bold yellow",
    "baw.error":    "bold red",
    "baw.cmd":      "bold magenta",
    "baw.desc":     "white",
    "baw.key":      "bold yellow",
    "baw.val":      "white",
    "baw.header":   "bold magenta",
    "baw.subtitle": "italic yellow",
    "baw.prompt":   "bold magenta",
    "baw.ai":       "#b0b8c0",
    # Panel/table borders (dimmed purple)
    "panel.border": "dark_magenta",
    "table.border": "dark_magenta",
    "table.header": "bold yellow",
})

console = Console(theme=BAW_THEME)
