"""BAW built-in: web search tool (wraps search provider registry)"""

from __future__ import annotations


def web_search(query: str, limit: int = 5, provider: str | None = None) -> str:
    """Search the web using BAW's search provider registry.

    Args:
        query: Search query string
        limit: Max results (default: 5)
        provider: Provider name (optional, uses config default)

    Returns:
        Formatted search results as text
    """
    try:
        # Import lazily to avoid import order issues
        from baw.core.search import search as _baw_search
        actual_provider = provider or "duckduckgo"
        results = _baw_search(query, provider=actual_provider, limit=limit)
        if not results:
            return f"[{actual_provider}] No results for: {query}"

        lines = [f"Web search results for: {query}"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "(no title)")
            url = r.get("url", "")
            snippet = r.get("snippet", "")
            lines.append(f"\n{i}. {title}")
            if url:
                lines.append(f"   URL: {url}")
            if snippet:
                lines.append(f"   {snippet}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Search error] {e}"


TOOL_DEF = {
    "name": "web_search",
    "description": "Search the web via BAW's built-in search provider (DuckDuckGo, free, no API key). "
                   "Returns formatted results with title, URL, and snippet. "
                   "Use this to look up current information, verify claims, "
                   "or find documentation. Supports custom provider via 'provider' arg.",
    "handler": web_search,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — use keywords like on a search engine"
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default: 5, max: 10)",
            },
            "provider": {
                "type": "string",
                "description": "Optional provider override (default: duckduckgo). "
                               "Use 'baw --search-provider list' to see available providers.",
            },
        },
        "required": ["query"],
    },
    "risk_level": "low",
}
