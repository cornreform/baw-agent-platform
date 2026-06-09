"""BAW built-in: browser — web automation (stub).

Requires Playwright or similar browser automation to be installed.
Currently returns a configuration hint.
"""


def browser_navigate(url: str) -> str:
    """Navigate to a URL in the browser."""
    return _stub("browser")


def _stub(tool: str) -> str:
    return (
        f"[{tool}] Not configured. To enable browser automation:\n"
        f"  pip install playwright\n"
        f"  playwright install chromium\n"
        f"Then set in ~/.baw/config.yaml:\n"
        f"  tools:\n"
        f"    browser:\n"
        f"      enabled: true"
    )


TOOL_DEF = {
    "name": "browser",
    "description": (
        "Browser automation — navigate, click, type, screenshot web pages. "
        "Currently NOT configured — returns setup instructions. "
        "Use web_search + web_extract as alternatives."
    ),
    "handler": browser_navigate,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to navigate to",
            },
        },
        "required": ["url"],
    },
    "risk_level": "medium",
}
