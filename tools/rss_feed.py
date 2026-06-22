"""BAW built-in: RSS Feed Reader — fetch and parse RSS/Atom feeds

Fully self-contained, uses feedparser (Python library, no external API).

Returns structured feed entries with title, link, published date,
and summary for each item. Pairs with cronjob for feed monitoring.
"""

from core.tools import register


def rss_feed(feed_url: str, max_items: int = 10) -> str:
    """Fetch and parse an RSS/Atom feed.

    Args:
        feed_url: URL of the RSS or Atom feed
        max_items: Maximum number of entries to return (default: 10)

    Returns:
        Structured feed data in JSON format with feed metadata
        and entries (title, link, published, summary).
    """
    try:
        import feedparser
    except ImportError:
        return '{"error": "feedparser not installed. Run: pip install feedparser"}'

    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        return f'{{"error": "Failed to parse feed: {e}"}}'

    if feed.bozo and not feed.entries:
        return f'{{"error": "Feed parse error: {feed.bozo_exception}"}}'

    result = {
        "feed_title": feed.feed.get("title", "Untitled Feed"),
        "feed_link": feed.feed.get("link", ""),
        "feed_description": feed.feed.get("subtitle", ""),
        "total_entries": len(feed.entries),
        "entries": [],
    }

    for entry in feed.entries[:max_items]:
        result["entries"].append({
            "title": entry.get("title", "Untitled"),
            "link": entry.get("link", ""),
            "published": entry.get("published", entry.get("updated", "Unknown date")),
            "summary": entry.get("summary", "")[:500],
        })

    import json
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "rss_feed",
    "description": (
        "Fetch and parse an RSS or Atom feed. "
        "Returns structured JSON with feed metadata and entries "
        "(title, link, published date, summary). "
        "Use for monitoring blogs, changelogs, news sources. "
        "Pair with cronjob for periodic feed monitoring. "
        "Fully self-contained — uses feedparser locally, no external API."
    ),
    "handler": rss_feed,
    "parameters": {
        "type": "object",
        "properties": {
            "feed_url": {
                "type": "string",
                "description": "URL of the RSS or Atom feed to fetch",
            },
            "max_items": {
                "type": "integer",
                "description": "Maximum number of entries to return (default: 10)",
            },
        },
        "required": ["feed_url"],
    },
    "risk_level": "low",
}
