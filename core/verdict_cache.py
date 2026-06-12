"""M3-7: Verdict cache — skip prosecutor when same goal was already APPROVED.

Fable 5 spec §5 #7: 「判決快取 — 同類案由 (embedding 相似度 >0.92) 且上次 APPROVED ≥8 → 檢察官引用前案,唔重新批」

Implementation: lightweight character-bigram Jaccard similarity (no
embedding model required, so it works offline and adds zero latency).
For tasks where the user re-asks the same question (e.g. "what's BTC
price?" every minute) this skips the Devil LLM call entirely.

Returns the previous verdict/critique if similarity is high enough AND
the previous case was APPROVED with score >= 8.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

_SIMILARITY_THRESHOLD = 0.85  # Jaccard on char bigrams (slightly stricter than Fable 5's 0.92 because Jaccard is noisier than cosine on embeddings)
_MIN_APPROVED_SCORE = 8
_MAX_CASE_AGE_DAYS = 7  # don't reuse verdicts older than this


def _char_bigrams(s: str) -> set[str]:
    """Extract character bigrams from a string, lowercased.

    For Cantonese/English mix this captures word-shape similarity better
    than word-level n-grams. Pure character-level would be too noisy
    (every shared character adds to the count); bigrams are a sweet spot.
    """
    s = re.sub(r"\s+", " ", s.lower().strip())
    if len(s) < 2:
        return {s}
    return {s[i:i+2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity on character bigrams: |A ∩ B| / |A ∪ B|."""
    A = _char_bigrams(a)
    B = _char_bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def find_reusable_verdict(goal: str, tier: int) -> Optional[dict]:
    """Return a previous case dict if the goal is similar enough to a recent APPROVED case.

    Returns None if no suitable case exists. The caller should treat the
    returned dict as a "best prior verdict" reference: include the
    prosecutor's critique + judge's reason in the new case's context
    so the defendant can be told "previous run did X, repeat that".
    """
    archive_dir = Path.home() / ".baw" / "court" / "cases"
    if not archive_dir.exists():
        return None

    cutoff = time.time() - _MAX_CASE_AGE_DAYS * 86400
    best = None
    best_score = 0.0
    for p in archive_dir.glob("*.json"):
        try:
            import json
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("tier") != tier:
            continue
        if data.get("verdict") != "approved":
            continue
        if data.get("score", 0) < _MIN_APPROVED_SCORE:
            continue
        if data.get("created_at", 0) < cutoff:
            continue
        sim = jaccard(goal, data.get("goal", ""))
        if sim > best_score and sim >= _SIMILARITY_THRESHOLD:
            best = data
            best_score = sim
    return best
