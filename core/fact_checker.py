"""
BAW — Built-in Fact Checker (P0)
Post-hoc claim verification layer.

Three modes:
  - strict: Block unverified factual claims (price/spec/date/statistics)
  - normal: Flag/mark unsourced claims, let through
  - relaxed: No-op (trust LLM output)

The checker runs after the final LLM response, before returning to user.
"""

from __future__ import annotations
import re
from typing import Optional, Callable


# Patterns that indicate a factual claim needing verification
CLAIM_PATTERNS = [
    # Prices: $X, USD X, HKD X, X 元, 價格 X
    # NOTE: Use [$] char class — plain \$ is treated as EOL anchor in this env
    r'(?:價格|價錢|售價|收費|費用|定價)[：:]\s*[\d,]+',
    r'(?:每月|每年|一次性)\s*(?:費用|收費|價格)[：:]\s*[\d,]+',
    r'(?:USD|HKD|NTD|TWD|CNY|JPY|EUR|GBP)\s*[\d,]+(?:\.\d+)?',
    r'[$€£¥]\s*[\d,]+(?:\.\d+)?',
    r'[\d,]+(?:\.\d+)?\s*(?:USD|HKD|元|美元|港幣|歐元|日圓|英鎊)',

    # Dates & versions
    r'release\s*:?\s*[\d.]+',
    r'version\s*:?\s*[\d.]+',
    r'v?[\d]+\.\d+\.\d+(?:-\w+)?',  # semver
    r'\d{4}\s*年\s*\d{1,2}\s*月',
    r'(?:發佈|推出|上市|更新|改版)[於在]?\s*\d{4}',

    # Statistics / metrics
    r'(?:準確率|accuracy|precision|recall|latency|throughput|benchmark)\s*(?:[：:]|of)\s*\d+',
    r'\d+(?:\.\d+)?%',
    r'(?:rank|rating|score)[：:]\s*\d+(?:\.\d+)?',

    # Technical specs
    r'\d+\s*(?:GB|TB|MB|KB|GHz|MHz|TOPS|FLOPS|watt|W)',
    r'\d+\s*x\s*\d+\s*(?:px|pixels|resolution)',
    r'(?:context|context\s*window|max\s*tokens|training\s*data)\s*[：:]\s*[\d,]+',
]

# Patterns that indicate the statement is sourced/attributed (safe)
SOURCE_INDICATORS = [
    r'according\s+to',
    r'source\s*[：:]',
    r'ref[：:]',
    r'from\s+(?:the\s+)?(?:docs?|documentation|manual)',
    r'see\s+(?:link|url)',
    r'據.*報導',
    r'根據.*資料',
    r'官方文件顯示',
    r'來源[：:]',
    r'參考[：:]',
]


def has_source(text: str, claim_context: str) -> bool:
    """Check if a section of text around a claim has source attribution."""
    for pattern in SOURCE_INDICATORS:
        if re.search(pattern, claim_context, re.IGNORECASE):
            return True
    return False


def extract_claims(text: str) -> list[dict]:
    """Extract potential factual claims with context window for source checking."""
    claims = []
    lines = text.split("\n")

    for i, line in enumerate(lines):
        matches = []
        for pattern in CLAIM_PATTERNS:
            for m in re.finditer(pattern, line, re.IGNORECASE):
                matches.append(m.group())

        if matches:
            # Grab surrounding context (±2 lines) for source check
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
    """Determine if verification is needed based on task type and mode."""
    if mode == "relaxed":
        return False
    if mode == "strict":
        return True
    # normal mode: only verify price/spec tasks
    price_keywords = ["price", "cost", "pricing", "收費", "價格", "錢", "budget"]
    spec_keywords = ["spec", "compare", "benchmark", "規格", "對比"]
    task_lower = task_type.lower()
    for kw in price_keywords + spec_keywords:
        if kw in task_lower:
            return True
    return False


class FactChecker:
    """Built-in fact verification layer.

    Usage in agent loop (after final LLM response):
        checker = FactChecker(config)
        action, result = checker.check(response_text, task_context)
        if action == "block":
            # Rewrite response without unverified claims
            ...
        elif action == "flag":
            # Append unsourced markers
            response_text = result["annotated"]
    """

    def __init__(self, config: dict):
        self.mode = config.get("fact_check", {}).get("mode", "normal")
        self.seen_claims: set[str] = set()

    def check(self, text: str, task_context: str = "") -> tuple[str, dict]:
        """Check a response for unverified factual claims.

        Returns:
            ("pass", {"claims": [], "annotated": text})
            ("flag", {"claims": [...], "annotated": annotated_text})
            ("block", {"claims": [...], "message": "..."})
        """
        if self.mode == "relaxed":
            return "pass", {"claims": [], "annotated": text}

        if not needs_verification(task_context, self.mode):
            return "pass", {"claims": [], "annotated": text}

        claims = extract_claims(text)
        if not claims:
            return "pass", {"claims": [], "annotated": text}

        # Filter out already-sourced claims
        unverified = [c for c in claims if not c["sourced"]]
        if not unverified:
            return "pass", {"claims": claims, "annotated": text}

        # Deduplicate
        unique = []
        seen = set()
        for c in unverified:
            key = c["claim"].strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(c)

        if self.mode == "strict":
            # Block the response entirely
            message = (
                f"⚠️ **Blocked by Fact Checker ({self.mode} mode)**\n\n"
                f"Detected unverified claims:\n"
            )
            for c in unique[:5]:
                # Show the line around the claim
                snippet = c["context"].strip()[:200]
                message += f"- `{c['claim']}` in: _{snippet}_\n"
            if len(unique) > 5:
                message += f"- ... and {len(unique) - 5} more\n"
            message += (
                "\nPlease rephrase to include sources (reference links, "
                "official documentation, or web search results)."
            )
            return "block", {"claims": unique, "message": message}

        # normal mode: annotate with unsourced markers
        annotated = text
        for c in unique:
            marker = f"_[unsourced: {c['claim']}]_"
            annotated += f"\n\n{marker}"

        return "flag", {"claims": unique, "annotated": annotated}
