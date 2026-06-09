"""BAW built-in: web_extract — extract web page content as markdown.

Like Hermes web_extract: fetches a URL and returns clean markdown text.
Uses readability-lxml for content extraction when available, falls back to plain HTML-to-text.
"""
import re
from pathlib import Path


def web_extract(urls: str) -> str:
    """Extract content from one or more web page URLs.

    Args:
        urls: Comma-separated list of URLs to fetch (max 3).

    Returns:
        Markdown text from each URL, or error messages.
    """
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    if not url_list:
        return "Error: at least one URL is required"
    if len(url_list) > 3:
        return "Error: max 3 URLs per call"

    results = []
    for url in url_list[:3]:
        try:
            content = _extract_one(url)
            results.append(f"## {url}\n\n{content[:8000]}")
        except Exception as e:
            results.append(f"## {url}\n\n❌ Error: {e}")

    return "\n\n---\n\n".join(results)


def _extract_one(url: str) -> str:
    """Extract content from a single URL. Tries readability, falls back to basic."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BAW/1.0 (bot; +https://github.com/cornreform/baw)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL error: {e.reason}"
    except Exception as e:
        return f"Fetch error: {e}"

    # Try readability
    try:
        from readability import Document
        doc = Document(html)
        title = doc.title() or ""
        content = doc.summary()
        # Strip HTML tags, leave basic markdown-like formatting
        content = _html_to_text(content)
        result = f"{title}\n{content}" if title else content
        return result[:8000] if result else "(empty page)"
    except ImportError:
        pass

    # Fallback: basic HTML-to-text
    text = _html_to_text(html)
    return text[:8000] if text.strip() else "(empty page)"


def _html_to_text(html: str) -> str:
    """Basic HTML to plain text conversion."""
    # Remove scripts and styles
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace common block elements with newlines
    for tag in ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br"]:
        html = re.sub(f"</?{tag}[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Remove remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    # Decode entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Collapse whitespace
    html = re.sub(r"\n\s*\n", "\n\n", html)
    html = re.sub(r" {2,}", " ", html)
    return html.strip()


TOOL_DEF = {
    "name": "web_extract",
    "description": (
        "Extract content from web page URLs as markdown text. "
        "Use this to READ the full content of a page found via web_search. "
        "Supports up to 3 URLs per call. Results are truncated at 8000 chars each. "
        "URLs must be comma-separated."
    ),
    "handler": web_extract,
    "parameters": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "string",
                "description": "Comma-separated list of URLs to extract (max 3)",
            },
        },
        "required": ["urls"],
    },
    "risk_level": "low",
}
