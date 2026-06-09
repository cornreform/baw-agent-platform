"""BAW built-in: web page text extraction via requests + BeautifulSoup"""

import requests
from bs4 import BeautifulSoup
import re


def web_extract(url: str, strip_lines: bool = True, max_chars: int = 8000) -> str:
    """Fetch a web page and extract plain text content using requests + BeautifulSoup.

    Strips scripts, styles, and excessive whitespace. Returns readable plain text.

    Args:
        url: Full URL to fetch (http/https)
        strip_lines: Remove empty lines for compact output (default: True)
        max_chars: Max characters to return (default: 8000). Use 0 for no limit.

    Returns:
        Extracted plain text content from the web page.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return f"Error: request to {url} timed out"
    except requests.exceptions.RequestException as e:
        return f"Error: {e}"

    # Parse HTML and extract text
    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header",
                      "noscript", "iframe", "form", "button", "aside"]):
        tag.decompose()

    # Get text, join lines
    text = soup.get_text(separator="\n")

    # Clean whitespace
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if strip_lines and not line:
            continue
        lines.append(line)

    cleaned = "\n".join(lines)

    # Collapse multiple blank lines (if not strip_lines)
    if not strip_lines:
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # Truncate if needed
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "\n\n[... truncated, full content exceeds limit]"

    return cleaned if cleaned.strip() else f"No extractable text found at {url}"


TOOL_DEF = {
    "name": "web_extract",
    "description": (
        "Fetch a web page and extract plain text using requests + BeautifulSoup. "
        "Use this to read articles, documentation, or any HTML content "
        "when you need the raw text without rendering. "
        "Strips scripts, styles, nav bars, and other non-content elements. "
        "Returns clean, readable text."
    ),
    "handler": web_extract,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to fetch and extract text from (e.g. 'https://example.com/page')",
            },
            "strip_lines": {
                "type": "boolean",
                "description": "Remove empty lines for compact output (default: True)",
            },
            "max_chars": {
                "type": "integer",
                "description": (
                    "Max characters to return (default: 8000). "
                    "Use 0 for no limit (full page)."
                ),
            },
        },
        "required": ["url"],
    },
    "risk_level": "low",
}
