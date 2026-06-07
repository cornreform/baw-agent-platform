"""
BAW Search Provider: DuckDuckGo
FREE — no API key required, works out of the box.
Rate limit: ~1-2 requests/second (unofficial).
"""

from baw.core.search import SearchResult

NAME = "duckduckgo"
DESCRIPTION = "DuckDuckGo search — free, no API key required, works immediately"
REQUIRES_API_KEY = False
ENV_VAR = None

SETUP_GUIDE = """\
# DuckDuckGo Search — Setup Guide

**Cost:** Free
**API Key:** Not required
**Rate Limit:** ~1-2 requests per second (unofficial)

## Installation

DuckDuckGo search is built into BAW. No additional setup needed.

The `duckduckgo-search` Python library is required:
```bash
pip install duckduckgo-search
```

## Configuration

No configuration needed. It's the default search provider.

## Upgrade Path

If you hit rate limits, switch to a paid provider like Tavily or Brave:
```yaml
# config.yaml
search:
  provider: tavily  # or: brave, exa, etc.
```

See `baw search-provider guide tavily` for Tavily setup.
"""

API_REFERENCE = """\
# DuckDuckGo Search — API Reference

**Library:** `duckduckgo-search` v8.x
**Docs:** https://pypi.org/project/duckduckgo-search/

## Endpoint

No official API — uses DuckDuckGo's HTML search results via HTTP requests.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| query | str | (required) | Search query |
| limit | int | 5 | Max results (1-20) |
| region | str | "wt-wt" | Region code (e.g. "hk-tzh" for HK Chinese) |
| safesearch | str | "moderate" | "on", "moderate", or "off" |

## Result Format

```python
SearchResult(
    title="Page Title",
    url="https://example.com/page",
    snippet="Search result snippet text...",
    content="",  # Empty — summary only, no full page content
    source="web",
)
```

## Limitations

- No full page content extraction (only snippets)
- Rate limited (~1-2 req/s)
- May break if DuckDuckGo changes their HTML structure
- No SLA or guarantee
"""


def search(query: str, limit: int = 5, **kwargs) -> list[SearchResult]:
    """Search DuckDuckGo and return results."""
    from ddgs import DDGS

    results = []
    try:
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(query, max_results=limit)):
                if i >= limit:
                    break
                results.append(SearchResult(
                    title=r.get("title", ""),
                    url=r.get("href", ""),
                    snippet=r.get("body", ""),
                    source="web",
                ))
    except Exception as e:
        raise RuntimeError(f"DuckDuckGo search failed: {e}")

    return results
