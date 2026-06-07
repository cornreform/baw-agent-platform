"""
BAW — Tool Degradation (P1)
Graceful fallback chains for failed tool executions.

Each tool has a defined degradation chain: alternative approaches
to try when the primary approach fails. Instead of giving up,
BAW walks the chain until one succeeds or all are exhausted.
"""

from __future__ import annotations
from typing import Optional


# ── Degradation Chains ─────────────────────────────────────────
# Each chain is a list of (strategy_name, transformer_fn, description)
# where transformer_fn takes (args: dict, result: str) -> new_args or None

_DEGRADATION_CHAINS: dict[str, list[dict]] = {}


def register_chain(tool_name: str, chain: list[dict]):
    """Register a degradation chain for a tool.

    Each entry in the chain:
      {
        "name": str,              # Strategy name (for logging)
        "transform": callable,    # (args: dict, result: str) -> dict or None
        "description": str,       # Human-readable description
      }

    transform receives the original args + the failure result,
    returns MODIFIED args for the retry, or None if this strategy
    doesn't apply.
    """
    _DEGRADATION_CHAINS[tool_name] = chain


def get_chain(tool_name: str) -> list[dict]:
    """Get the degradation chain for a tool. Returns empty list if none."""
    return _DEGRADATION_CHAINS.get(tool_name, [])


def next_strategy(tool_name: str, attempted: list[str],
                  args: dict, result: str) -> Optional[dict]:
    """Find the next untried strategy for a tool.

    Args:
        tool_name: Tool that failed
        attempted: List of strategy names already tried
        args: Original tool arguments
        result: Tool failure result text

    Returns:
        dict with {"name": str, "new_args": dict, "description": str}
        or None if all strategies exhausted.
    """
    chain = get_chain(tool_name)
    for entry in chain:
        if entry["name"] in attempted:
            continue
        try:
            new_args = entry["transform"](args, result)
            if new_args is not None:
                return {
                    "name": entry["name"],
                    "new_args": new_args,
                    "description": entry["description"],
                }
        except Exception:
            continue
    return None


# ── Built-in transformation functions ──────────────────────────

def _bash_retry_with_timeout(args: dict, result: str) -> Optional[dict]:
    """If timeout, retry with double the timeout."""
    if "Timeout" in result or "timed out" in result.lower():
        new = dict(args)
        new["timeout"] = min(new.get("timeout", 60) * 2, 300)
        return new
    return None


def _bash_retry_with_workdir(args: dict, result: str) -> Optional[dict]:
    """If 'No such file' or path error, try with parent dir."""
    if "No such file" in result or "not found" in result.lower():
        new = dict(args)
        if "workdir" in new and new["workdir"]:
            from pathlib import Path
            parent = Path(new["workdir"]).parent
            new["workdir"] = str(parent)
            return new
    return None


def _write_file_retry_path(args: dict, result: str) -> Optional[dict]:
    """If 'Permission denied' or path error, try /tmp."""
    if "Permission" in result or "denied" in result.lower():
        new = dict(args)
        from pathlib import Path
        import re
        orig = Path(new.get("path", ""))
        safe_name = re.sub(r'[^\w.-]', '_', orig.name) if orig.name else "output"
        new["path"] = f"/tmp/{safe_name}"
        return new
    return None


def _search_retry_simpler_query(args: dict, result: str) -> Optional[dict]:
    """If search returns nothing, try with shorter query."""
    if "No results" in result or "failed" in result.lower() or result == "" or "error" in result.lower():
        new = dict(args)
        query = new.get("query", "")
        # Strip special chars, use first few words
        import re
        words = re.findall(r'\w+', query)
        if len(words) > 3:
            new["query"] = " ".join(words[:3])
            return new
    return None


def _search_retry_different_provider(args: dict, result: str) -> Optional[dict]:
    """If search error, keep query but try a note about the error."""
    if "error" in result.lower() or "Unknown" in result:
        new = dict(args)
        new["query"] = f"{new.get('query', '')} (web search)"
        return new
    return None


# ── Register built-in chains ───────────────────────────────────

register_chain("bash", [
    {
        "name": "bash_longer_timeout",
        "transform": _bash_retry_with_timeout,
        "description": "Retry with double timeout",
    },
    {
        "name": "bash_parent_dir",
        "transform": _bash_retry_with_workdir,
        "description": "Retry with parent working directory",
    },
])

register_chain("write_file", [
    {
        "name": "write_fallback_tmp",
        "transform": _write_file_retry_path,
        "description": "Fallback to /tmp/ on permission error",
    },
])

register_chain("web_search", [
    {
        "name": "search_simpler_query",
        "transform": _search_retry_simpler_query,
        "description": "Retry with shorter query (3 key words)",
    },
])
