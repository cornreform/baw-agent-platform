"""
BAW Search Provider: Tavily (Example)
AI-native search engine — 1,000 free credits/month.
Demonstrates how to add a paid provider with API key.
"""

import os
from core.search import SearchResult

NAME = "tavily"
DESCRIPTION = (
    "Tavily AI search — 1,000 free credits/month, "
    "citation-ready results, designed for AI agents"
)
REQUIRES_API_KEY = True
ENV_VAR = "TAVILY_API_KEY"

SETUP_GUIDE = """\
# Tavily Search — Setup Guide

**Cost:** Free tier: 1,000 credits/month. Paid: $0.008/credit after.
**API Key:** Required (free, no credit card needed)

## Step 1: Get an API Key

1. Go to https://tavily.com
2. Sign up (no credit card required for free tier)
3. Copy your API key from the dashboard

## Step 2: Add to BAW

```bash
# Add to ~/.baw/.env
echo 'TAVILY_API_KEY=your-key-here' >> ~/.baw/.env
```

## Step 3: Configure BAW

```yaml
# config.yaml
search:
  provider: tavily
```

## Verification

```bash
baw search-provider test tavily "example query"
```

## Notes

- Free tier: 1,000 searches/month
- Rate limit: ~10 req/s on free tier
- Results include full content extraction (not just snippets)
"""

API_REFERENCE = """\
# Tavily Search — API Reference

**Base URL:** https://api.tavily.com
**Docs:** https://docs.tavily.com

## Endpoint

POST https://api.tavily.com/search

## Authentication

Header: `Authorization: Bearer <TAVILY_API_KEY>`

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| query | str | (required) | Search query |
| search_depth | str | "basic" | "basic" or "advanced" |
| max_results | int | 5 | Number of results |
| include_answer | bool | false | Include an AI-generated summary |
| include_domains | list | [] | Limit to specific domains |
| exclude_domains | list | [] | Exclude specific domains |

## Result Format

Each result includes:
- title, url, content (full page text), score (relevance)

## Python SDK

```python
from tavily import TavilyClient
client = TavilyClient(api_key="<key>")
results = client.search(query="example")
```

## Pricing

| Tier | Price | Quota |
|------|-------|-------|
| Free | $0/month | 1,000 searches |
| Pay-as-you-go | $0.008/search | No cap |
"""


def search(query: str, limit: int = 5, **kwargs) -> list[SearchResult]:
    """Search Tavily and return results.

    Requires TAVILY_API_KEY environment variable.
    Install: pip install tavily-py
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY not set. "
            "Run: baw search-provider guide tavily"
        )

    try:
        from tavily import TavilyClient
    except ImportError:
        raise RuntimeError(
            "tavily-py not installed. Run: pip install tavily-py"
        )

    client = TavilyClient(api_key=api_key)
    response = client.search(
        query=query,
        max_results=limit,
        search_depth=kwargs.get("depth", "basic"),
    )

    results = []
    for r in response.get("results", []):
        results.append(SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", "")[:300],
            content=r.get("content", ""),
            source="web",
        ))
    return results
