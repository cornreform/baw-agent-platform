"""
BAW — Search Provider Registry
Open architecture: anyone can add a search provider by writing one file.

To add a new search provider:
  1. Create search_providers/<name>.py
  2. Export: NAME, DESCRIPTION, SETUP_GUIDE, API_REFERENCE, search()
  3. Call register_search_provider("name", module) in __init__.py

Example provider:
  search_providers/duckduckgo.py  — FREE, no API key, built-in
  search_providers/tavily.py      — example: paid with 1,000 free/month

CLI:
  baw search-provider list         → show all providers
  baw search-provider guide <name>  → show setup guide
  baw search-provider api <name>    → show API reference
"""

from __future__ import annotations
from typing import Optional, Callable

# Registry: name -> module
_providers: dict[str, object] = {}

# Built-in providers shipped with BAW
_BUILTIN_PROVIDERS = ["duckduckgo"]


class SearchResult:
    """Standard search result format across all providers."""

    def __init__(
        self,
        title: str,
        url: str,
        snippet: str = "",
        content: str = "",
        source: str = "web",
    ):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.content = content
        self.source = source

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.content,
            "source": self.source,
        }

    def __repr__(self):
        return f"SearchResult(title={self.title!r}, url={self.url!r})"


def register_search_provider(name: str, module: object):
    """Register a search provider module.

    The module must export:
      NAME: str
      DESCRIPTION: str
      SETUP_GUIDE: str (markdown)
      API_REFERENCE: str (markdown)
      search(query, limit=5, **kwargs) -> list[SearchResult]

    Also exports are optional:
      REQUIRES_API_KEY: bool (default: False)
      ENV_VAR: str (API key env var name, if applicable)
    """
    _providers[name] = module


def get_provider(name: str) -> Optional[object]:
    """Get a registered provider module by name."""
    return _providers.get(name)


def list_providers() -> list[dict]:
    """List all registered providers with metadata."""
    results = []
    for name, mod in sorted(_providers.items()):
        results.append({
            "name": name,
            "description": getattr(mod, "DESCRIPTION", ""),
            "requires_api_key": getattr(mod, "REQUIRES_API_KEY", False),
            "env_var": getattr(mod, "ENV_VAR", None),
        })
    return results


def search(
    query: str,
    provider: str = "duckduckgo",
    limit: int = 5,
    **kwargs,
) -> list[dict]:
    """Search using the specified provider.

    Args:
        query: Search query string
        provider: Provider name (default: duckduckgo)
        limit: Max results
        **kwargs: Provider-specific parameters

    Returns:
        list of SearchResult dicts

    Raises:
        ValueError: If provider not found
        RuntimeError: If search fails
    """
    mod = get_provider(provider)
    if not mod:
        available = ", ".join(_providers.keys())
        raise ValueError(
            f"Unknown search provider: '{provider}'. "
            f"Available: {available}"
        )

    try:
        results = mod.search(query, limit=limit, **kwargs)
        return [r.to_dict() if isinstance(r, SearchResult) else r for r in results]
    except Exception as e:
        raise RuntimeError(f"Search provider '{provider}' failed: {e}")


def get_setup_guide(name: str) -> str:
    """Get the setup guide for a provider."""
    mod = get_provider(name)
    if not mod:
        raise ValueError(f"Unknown provider: {name}")
    return getattr(mod, "SETUP_GUIDE", "No setup guide available.")


def get_api_reference(name: str) -> str:
    """Get the API reference for a provider."""
    mod = get_provider(name)
    if not mod:
        raise ValueError(f"Unknown provider: {name}")
    return getattr(mod, "API_REFERENCE", "No API reference available.")


def _auto_discover():
    """Auto-discover and register built-in providers.
    
    This is called explicitly during BAW startup (in CLI or run_agent),
    not at import time, to avoid import path issues.
    """
    import importlib
    for name in _BUILTIN_PROVIDERS:
        try:
            mod = importlib.import_module(f"search_providers.{name}")
            register_search_provider(name, mod)
        except ImportError:
            pass  # Provider module not found, skip
