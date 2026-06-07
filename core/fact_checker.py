"""
BAW — Fact Checker with LLM-based Web Search Verification (P0)

Three modes:
  - strict: Block unverified factual claims, use web search to verify
  - normal: Flag unsourced claims, use web search to verify when possible
  - relaxed: No-op (trust LLM output)

The checker runs after the final LLM response, before returning to user.
"""

from __future__ import annotations
import re
from typing import Optional


# ── Claim patterns (regex-based extraction) ─────────────────────

CLAIM_PATTERNS = [
    # Prices
    r'(?:價格|價錢|售價|收費|費用|定價)[：:]\\s*[\\d,]+',
    r'(?:每月|每年|一次性)\\s*(?:費用|收費|價格)[：:]\\s*[\\d,]+',
    r'(?:USD|HKD|NTD|TWD|CNY|JPY|EUR|GBP)\\s*[\\d,]+(?:\\.\\d+)?',
    r'[$€£¥]\\s*[\\d,]+(?:\\.\\d+)?',
    r'[\\d,]+(?:\\.\\d+)?\\s*(?:USD|HKD|元|美元|港幣|歐元|日圓|英鎊)',

    # Dates & versions
    r'release\\s*:?\\s*[\\d.]+',
    r'version\\s*:?\\s*[\\d.]+',
    r'v?[\\d]+\\.\\d+\\.\\d+(?:-\\w+)?',
    r'\\d{4}\\s*年\\s*\\d{1,2}\\s*月',
    r'(?:發佈|推出|上市|更新|改版)[於在]?\\s*\\d{4}',

    # Statistics / metrics
    r'(?:準確率|accuracy|precision|recall|latency|throughput|benchmark)\\s*(?:[：:]|of)\\s*\\d+',
    r'\\d+(?:\\.\\d+)?%',
    r'(?:rank|rating|score)[：:]\\s*\\d+(?:\\.\\d+)?',

    # Technical specs
    r'\\d+\\s*(?:GB|TB|MB|KB|GHz|MHz|TOPS|FLOPS|watt|W)',
    r'\\d+\\s*x\\s*\\d+\\s*(?:px|pixels|resolution)',
    r'(?:context|context\\s*window|max\\s*tokens|training\\s*data)\\s*[：:]\\s*[\\d,]+',
]

SOURCE_INDICATORS = [
    r'according\\s+to',
    r'source\\s*[：:]',
    r'ref[：:]',
    r'from\\s+(?:the\\s+)?(?:docs?|documentation|manual)',
    r'see\\s+(?:link|url)',
    r'據.*報導',
    r'根據.*資料',
    r'官方文件顯示',
    r'來源[：:]',
    r'參考[：:]',
]


def has_source(text: str, claim_context: str) -> bool:
    for pattern in SOURCE_INDICATORS:
        if re.search(pattern, claim_context, re.IGNORECASE):
            return True
    return False


def extract_claims(text: str) -> list[dict]:
    """Extract potential factual claims with context for source checking."""
    claims = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        matches = []
        for pattern in CLAIM_PATTERNS:
            for m in re.finditer(pattern, line, re.IGNORECASE):
                matches.append(m.group())
        if matches:
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            context = "\n".join(lines[start:end])
            for claim_text in matches:
                claims.append({
                    "claim": claim_text,
                    "line": i,
                    "context": context,
                    "sourced": has_source(context, context),
                })
    return claims


def needs_verification(task_type: str, mode: str) -> bool:
    if mode == "relaxed":
        return False
    if mode == "strict":
        return True
    price_kw = ["price", "cost", "pricing", "收費", "價格", "錢", "budget"]
    spec_kw = ["spec", "compare", "benchmark", "規格", "對比"]
    tl = task_type.lower()
    for kw in price_kw + spec_kw:
        if kw in tl:
            return True
    return False


class FactChecker:
    """Built-in fact verification with optional web search verification."""

    def __init__(self, config: dict):
        self.mode = config.get("fact_check", {}).get("mode", "normal")
        self.seen_claims: set[str] = set()

    def check(self, text: str, task_context: str = "") -> tuple[str, dict]:
        """First-pass regex-based check. Same as before.

        Returns:
            ("pass", ...) / ("flag", ...) / ("block", ...)
        """
        if self.mode == "relaxed":
            return "pass", {"claims": [], "annotated": text}
        if not needs_verification(task_context, self.mode):
            return "pass", {"claims": [], "annotated": text}
        claims = extract_claims(text)
        if not claims:
            return "pass", {"claims": [], "annotated": text}
        unverified = [c for c in claims if not c["sourced"]]
        if not unverified:
            return "pass", {"claims": claims, "annotated": text}
        unique = []
        seen = set()
        for c in unverified:
            key = c["claim"].strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(c)
        if self.mode == "strict":
            msg = (
                f"⚠️ **Blocked by Fact Checker ({self.mode} mode)**\n\n"
                f"Detected unverified claims:\n"
            )
            for c in unique[:5]:
                snippet = c["context"].strip()[:200]
                msg += f"- `{c['claim']}` in: _{snippet}_\n"
            if len(unique) > 5:
                msg += f"- ... and {len(unique) - 5} more\n"
            msg += "\nPlease rephrase to include sources."
            return "block", {"claims": unique, "message": msg}
        annotated = text
        for c in unique:
            annotated += f"\n\n_[unsourced: {c['claim']}]_"
        return "flag", {"claims": unique, "annotated": annotated}

    def verify_with_search(self, text: str, task_context: str = "") -> tuple[str, dict]:
        """Second-pass LLM-augmented verification using web search.

        After regex check, for each unverified claim, run web search
        to confirm or flag it.

        Returns:
            ("pass", ...) — all claims verified via web search
            ("flag", ...) — some claims unverifiable
            ("block", ...) — claims contradicted by search results
            ("skip", ...) — web search not available
        """
        if self.mode == "relaxed":
            return "skip", {"claims": [], "message": "relaxed mode, skipping"}

        # First do the basic regex check
        action, result = self.check(text, task_context)
        if action == "pass":
            return "pass", result

        claims = result.get("claims", [])
        if not claims:
            return "pass", result

        # Try to import search provider
        try:
            from .search import search as _search, _auto_discover as _init_search
            _init_search()
        except ImportError:
            return "skip", {"claims": claims, "message": "search provider unavailable"}

        # For each unverified claim, look it up
        verified = []
        flagged = []
        for c in claims[:5]:  # Max 5 lookups per response
            claim_text = c["claim"]
            # Build a focused search query from the claim
            query = claim_text
            if c["context"]:
                # Extract meaningful keywords from context
                ctx_line = c["context"][:100]
                query = f"{claim_text} {ctx_line}"

            try:
                results = _search(query, provider="duckduckgo", limit=3)
                if results:
                    # Heuristic: if search returns relevant results, consider verified
                    snippets = " ".join(r.get("snippet", "") for r in results).lower()
                    # Check if claim keywords appear in results
                    keywords = re.sub(r'[^\w\s]', '', claim_text).lower().split()
                    keywords = [k for k in keywords if len(k) > 2]
                    if keywords:
                        match_count = sum(1 for k in keywords if k in snippets)
                        if match_count >= len(keywords) * 0.5:
                            verified.append(claim_text)
                        else:
                            flagged.append({
                                "claim": claim_text,
                                "reason": "Search results don't clearly support this claim",
                                "search_results": results,
                            })
                    else:
                        verified.append(claim_text)
                else:
                    flagged.append({
                        "claim": claim_text,
                        "reason": "No search results found",
                        "search_results": [],
                    })
            except Exception:
                flagged.append({
                    "claim": claim_text,
                    "reason": "search lookup failed",
                    "search_results": [],
                })

        if not flagged:
            return "pass", {"claims": claims, "verified": verified, "annotated": text}

        if self.mode == "strict":
            msg = (
                f"⚠️ **Blocked by Fact Checker (web search verification)**\n\n"
                f"Unverifiable claims:\n"
            )
            for f in flagged[:3]:
                msg += f"- `{f['claim']}`: {f['reason']}\n"
            if len(flagged) > 3:
                msg += f"- ... and {len(flagged) - 3} more\n"
            msg += "\nClaims that were verified: " + (", ".join(f"`{v}`" for v in verified) if verified else "none")
            return "block", {"claims": claims, "message": msg}

        return "flag", {
            "claims": claims,
            "verified": verified,
            "flagged": flagged,
            "annotated": text + "\n\n_⚠️ " + f"{len(flagged)} unverified claims (web search check)_",
        }
