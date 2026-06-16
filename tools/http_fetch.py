"""BAW built-in: browser-aware HTTP fetch for self-build tasks.

Wraps three strategies with auto-detection so sub-agents (and me) stop
using the wrong tool for the wrong site:

  1. ``urllib.request`` stdlib — fast, no deps, works for static HTML
  2. ``requests + BeautifulSoup`` — works for most static sites
  3. Browser-rendered (web_extract) — works for Next.js / Gatsby / React SPAs

A real Next.js / Gatsby / React SPA returns a tiny static shell
from ``urllib`` (often <8 KB) and the content lives in JavaScript that
runs in the browser. The previous pet-restaurant sub-agent (2026-06-12)
fell into exactly this trap: it used ``urllib``, got 7,254 bytes of
empty shell, and reported "0 rows / upstream limit" — wrong by a factor
of *capability*, not by luck.

This module exposes one stable interface — ``http_fetch(url)`` — that
auto-detects the strategy:

  - If the URL response contains ``<div id="__next">`` or
    ``<script id="__NEXT_DATA__">`` or ``<div id="___gatsby">`` or
    ``window.__INITIAL_STATE__`` → return "USE_BROWSER_FETCH" plus
    the cache filename the caller should write to.
  - If the response body is < 1 KB or contains only an empty root
    element with all content in <script> tags → same.
  - Otherwise → return the parsed text and let the caller use it.

Result is a tuple ``(strategy, content_or_marker, info)`` where
``strategy`` is one of ``"urllib" | "requests" | "BROWSER_REQUIRED" | "ERROR"``.

Always prefer the local mirror file once it exists. The companion
``docs/SELF_BUILD_RECIPE.md`` Step 2 spells this out.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

# SPA / CSR fingerprints — if ANY of these appear in the raw HTML,
# the page is client-rendered and urllib/requests will return an
# empty shell. Caller MUST mirror via browser fetch.
_SPA_FINGERPRINTS = [
    r'<div\s+id="__next"',                      # Next.js (app router)
    r'<script\s+id="__NEXT_DATA__"',            # Next.js (pages router)
    r'<div\s+id="___gatsby"',                   # Gatsby
    r'window\.__INITIAL_STATE__',               # Generic React SSR
    r'window\.__PRELOADED_STATE__',            # Generic React SSR
    r'data-react-helmet="true"',                # CRA
    r'<div\s+id="root"',                        # CRA / generic React mount point
    r'chunks?/[a-z0-9_-]+\.js',                 # Webpack chunk pattern, no content
]

_SPA_REGEX = re.compile("|".join(_SPA_FINGERPRINTS), re.IGNORECASE)


def detect_spa(html: str) -> bool:
    """Return True if the HTML response looks like a client-rendered SPA.

    The fingerprint check is intentionally conservative — false positives
    just mean "use browser fetch" which still works, but false negatives
    mean we miss real SPAs and the tool silently returns zero rows.
    """
    if not html:
        return False
    # Strip obvious script tags first — if ALL the visible text is in
    # <script> tags and the body is <2 KB, it's a SPA shell.
    text_only = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text_only = re.sub(r"<style[^>]*>.*?</style>", "", text_only, flags=re.DOTALL | re.IGNORECASE)
    visible = re.sub(r"<[^>]+>", " ", text_only).strip()
    if _SPA_REGEX.search(html):
        return True
    if len(html) < 8192 and len(visible) < 300 and "<script" in html.lower():
        # Tiny shell, almost all content in <script> tags → SPA.
        return True
    return False


def fetch_with_urllib(url: str, timeout: int = 15) -> Tuple[str, str]:
    """Fetch a URL with stdlib urllib. Returns (status, body)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return ("ok", body)
    except urllib.error.HTTPError as e:
        return (f"http_{e.code}", str(e))
    except Exception as e:
        return ("error", str(e))


def fetch_with_requests(url: str, timeout: int = 15) -> Tuple[str, str]:
    """Fetch a URL with requests + BeautifulSoup. Returns (status, body)."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return ("missing_dep", "requests / beautifulsoup4 not installed")
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "noscript", "iframe", "form", "button", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return ("ok", "\n".join(lines))
    except Exception as e:
        return ("error", str(e))


def http_fetch(url: str, prefer_browser: bool = False) -> Dict[str, Any]:
    """Fetch a URL with the right strategy for the page type.

    Returns a dict with:
      - ``strategy``: "urllib" | "requests" | "BROWSER_REQUIRED" | "ERROR"
      - ``body``: raw HTML (urllib/requests) or empty when BROWSER_REQUIRED
      - ``text``: extracted plain text (requests) or empty
      - ``is_spa``: bool — whether the page is a client-rendered SPA
      - ``mirror_path``: suggested path under ``data/`` to save the
        browser-rendered markdown for offline parsing
      - ``next_steps``: human-readable instructions for the caller

    When ``strategy == "BROWSER_REQUIRED"``, the caller MUST mirror the
    page to ``mirror_path`` using a browser-render tool
    ``web_extract`` does this in production). The mirror file then
    becomes the parse input.
    """
    # Strategy 1: urllib
    status, body = fetch_with_urllib(url)
    if status == "ok":
        if detect_spa(body):
            mirror = _suggest_mirror_path(url)
            return {
                "strategy": "BROWSER_REQUIRED",
                "body": body,
                "text": "",
                "is_spa": True,
                "mirror_path": mirror,
                "next_steps": (
                    f"Page is a client-rendered SPA (Next.js / Gatsby / React). "
                    f"urllib returned {len(body)} bytes of empty shell. "
                    f"Mirror to {mirror} using a browser-render tool "
                    f"(web_extract, or save the rendered HTML manually), "
                    f"then re-run the parser on the mirror file."
                ),
            }
        return {
            "strategy": "urllib",
            "body": body,
            "text": body,
            "is_spa": False,
            "mirror_path": None,
            "next_steps": "urllib returned content; parse it directly.",
        }

    # Strategy 2: requests + bs4 (slightly better at cookies/redirects)
    status, body_or_text = fetch_with_requests(url)
    if status == "ok":
        return {
            "strategy": "requests",
            "body": "",
            "text": body_or_text,
            "is_spa": False,
            "mirror_path": None,
            "next_steps": "requests + bs4 returned content; parse it directly.",
        }

    return {
        "strategy": "ERROR",
        "body": "",
        "text": "",
        "is_spa": False,
        "mirror_path": None,
        "next_steps": (
            f"All fetch strategies failed. urllib: {status}. "
            f"requests: {status}. Check the URL, network, or mirror manually."
        ),
    }


def _suggest_mirror_path(url: str) -> str:
    """Suggest a stable mirror path under data/ for a URL."""
    from core.paths import data_dir
    safe = re.sub(r"[^a-z0-9]+", "_", url.lower()).strip("_")
    safe = safe[:64] or "mirror"
    return str(data_dir() / f"{safe}_source.md")


def read_mirror(mirror_path: str) -> Optional[str]:
    """Read a previously-mirrored file. Returns None if it doesn't exist.

    Self-build tools should ALWAYS prefer ``read_mirror()`` over a fresh
    network call — the mirror is the source of truth, the network is a
    one-time setup cost.
    """
    p = Path(mirror_path)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    return None


# ── Tool registration ────────────────────────────────────────

TOOL_DEF = {
    "name": "http_fetch",
    "description": (
        "Browser-aware HTTP fetch. Auto-detects Next.js / Gatsby / React SPAs "
        "and returns BROWSER_REQUIRED with a mirror path. For static sites, "
        "returns urllib or requests text content. Always prefer read_mirror() "
        "over re-fetching."
    ),
    "handler": http_fetch,
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to fetch."},
            "prefer_browser": {
                "type": "boolean",
                "default": False,
                "description": "If True, skip urllib/requests and assume browser fetch.",
            },
        },
        "required": ["url"],
    },
    "risk_level": "low",
}


if __name__ == "__main__":
    # Quick CLI:  python tools/http_fetch.py <url>
    if len(sys.argv) < 2:
        print("Usage: python tools/http_fetch.py <url>")
        sys.exit(2)
    import json
    out = http_fetch(sys.argv[1])
    print(json.dumps(out, indent=2)[:2000])
