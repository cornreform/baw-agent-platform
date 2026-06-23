"""
BAW built-in: session_synthesis — cross-session pattern synthesis.

The "attention" counterpart to Bigram memory (Karpathy concept):
Instead of looking 1 token back (individual memory facts), synthesize
across sessions to find repeating patterns, evolving topics, blind spots.

Reads last N days from MemoryStore, groups by keyword similarity,
cross-references KG, produces insights, stores top 3 back into memory.
"""

import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

_BAW_ROOT = str(Path(__file__).resolve().parent.parent)
if _BAW_ROOT not in sys.path:
    sys.path.insert(0, _BAW_ROOT)

from core.memory import MemoryStore
from core.evolve import get_learned_lessons_summary

_KG_FILE = Path.home() / ".baw" / "knowledge_graph.json"
_DATA_DIR = Path.home() / ".baw"


# ── helpers ───────────────────────────────────────────────────────


def _load_kg() -> dict:
    """Load KG file or return empty dict."""
    if not _KG_FILE.exists():
        return {}
    try:
        return json.loads(_KG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text (mirrors MemoryStore._keywords)."""
    import re
    result: set[str] = set()
    latin_words = set(re.findall(r'[a-zA-Z0-9_]+', text.lower()))
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "in", "on", "at", "to", "for", "of", "with", "by", "from",
        "and", "or", "but", "not", "this", "that", "these", "those",
        "it", "its", "i", "you", "he", "she", "we", "they",
        "do", "does", "did", "have", "has", "had", "can", "will",
        "would", "could", "should", "may", "might", "shall",
        "about", "into", "through", "during", "before", "after",
        "above", "below", "between", "out", "off", "over", "under",
        "again", "further", "then", "once", "here", "there",
        "when", "where", "why", "how", "all", "each", "every",
        "both", "few", "more", "most", "other", "some", "such",
        "no", "nor", "only", "own", "same", "so", "than", "too",
        "very", "just", "because", "as", "until", "while",
    }
    result.update(w for w in latin_words if w not in stop_words)
    cjk_chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
    for i in range(len(cjk_chars) - 1):
        result.add(cjk_chars[i] + cjk_chars[i+1])
    cjk_stop_chars = {
        '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
        '这', '那', '他', '她', '它', '们', '你', '会', '要', '可',
        '以', '都', '一', '个', '上', '也', '很', '到', '说', '去',
        '能', '与', '对', '等', '多', '之', '为', '所', '而', '被',
        '于', '由', '把', '让', '从', '向', '将', '没', '还', '又',
    }
    for c in cjk_chars:
        if c not in cjk_stop_chars:
            result.add(c)
    return result


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity between two keyword sets."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _entries_since(ms: MemoryStore, days_back: int) -> list[dict]:
    """Return entries created within the last N days.

    Scans the internal cache to avoid search-query limitations.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    recent = []
    for entry in getattr(ms, "_cache", []):
        created_str = entry.get("created", "")
        if not created_str:
            continue
        try:
            created_dt = datetime.fromisoformat(created_str)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if created_dt >= cutoff:
            recent.append(entry)
    return recent


def _cluster_entries(entries: list[dict], threshold: float = 0.3) -> list[list[dict]]:
    """Group entries by keyword similarity (Jaccard >= threshold)."""
    groups = []
    used = set()
    for i, e1 in enumerate(entries):
        if e1.get("id") in used:
            continue
        group = [e1]
        used.add(e1["id"])
        k1 = _keywords(e1.get("content", ""))
        for j, e2 in enumerate(entries):
            if e2.get("id") in used:
                continue
            k2 = _keywords(e2.get("content", ""))
            if _jaccard(k1, k2) >= threshold:
                group.append(e2)
                used.add(e2["id"])
        if len(group) >= 2:
            groups.append(group)
    return groups


def _cluster_topic(entries: list[dict]) -> str:
    """Derive a topic name from the most common keyword among cluster entries."""
    word_counts = Counter()
    for e in entries:
        content = e.get("content", "")
        kw = _keywords(content)
        word_counts.update(w for w in kw if len(w) > 2)
    if not word_counts:
        # fallback: use first entry's first meaningful words
        first = entries[0].get("content", "")[:60]
        return first.strip() or "untitled"
    # Top 3 keywords as topic descriptor
    top = [w for w, _ in word_counts.most_common(3)]
    return ", ".join(top)


def _cluster_sentiment(entries: list[dict]) -> str:
    """Detect whether the cluster is correction/confirmation/neutral."""
    correction_signals = {"wrong", "incorrect", "fix", "change", "stop", "error",
                          "fail", "bug", "issue", "problem", "dont", "dont't",
                          "cannot", "not", "never", "avoid", "bad", "broken"}
    confirmation_signals = {"good", "correct", "right", "yes", "works", "working",
                            "success", "solved", "fixed", "completed", "done",
                            "confirmed", "verified", "valid"}

    text = " ".join(e.get("content", "").lower() for e in entries)
    words = set(text.split())

    cor_count = len(words & correction_signals)
    conf_count = len(words & confirmation_signals)

    if cor_count > conf_count * 2 and cor_count >= 2:
        return "correction"
    elif conf_count > cor_count * 2 and conf_count >= 2:
        return "confirmation"
    elif cor_count > 0 and conf_count > 0:
        return "mixed"
    return "neutral"


def _days_spread(entries: list[dict]) -> int:
    """Count how many distinct days the entries span."""
    days = set()
    for e in entries:
        created_str = e.get("created", "")
        if created_str:
            days.add(created_str[:10])
    return len(days)


def _kg_topics() -> dict[str, list[dict]]:
    """Build a dict mapping KG subject entities to their triples.

    Returns {entity_name: [triple_dict, ...]}.
    """
    kg = _load_kg()
    triples = kg.get("triples", [])
    topics: dict[str, list[dict]] = {}
    for t in triples:
        s = t.get("s", "")
        o = t.get("o", "")
        # index both subject and object
        for name in (s, o):
            if name:
                topics.setdefault(name.lower(), []).append(t)
    return topics


def _cross_ref(cluster_kw: set[str], kg_topics: dict) -> dict:
    """Check which KG triples relate to cluster keywords.

    Returns dict with keys: count, signal_count, reference_count.
    """
    matched = []
    for entity, triples in kg_topics.items():
        # Check if any cluster keyword matches an entity keyword
        entity_kw = _keywords(entity)
        if _jaccard(cluster_kw, entity_kw) >= 0.15:
            matched.extend(triples)
    if not matched:
        return {"count": 0, "signal_count": 0, "reference_count": 0}

    # Simple heuristic: triples with relations like "is_a", "has_property", etc. are signal
    signal_rels = {"configured_with", "is_a", "has_property", "solved", "implemented",
                   "fixed", "completed", "uses", "depends_on", "connected_to"}
    noise_rels = {"mentioned_in", "tagged", "reference", "related_to"}
    signal_count = 0
    reference_count = 0
    for t in matched:
        rel = t.get("r", "")
        if rel in signal_rels:
            signal_count += 1
        elif rel in noise_rels:
            reference_count += 1
        else:
            signal_count += 1  # default: count as signal

    return {
        "count": len(matched),
        "signal_count": signal_count,
        "reference_count": reference_count,
    }


def _build_recurring_insight(topic: str, n_entries: int, n_days: int,
                              sentiment: str, xref: dict) -> dict:
    """Build insight for a recurring topic (>=5 entries, >=3 days)."""
    insight_text = (
        f"{topic}: user discussed this {n_entries} times across "
        f"{n_days} days ({sentiment}). "
    )
    if xref["count"] > 0:
        insight_text += (
            f"KG has {xref['count']} related triples "
            f"({xref['signal_count']} signal, {xref['reference_count']} reference)."
        )
        return {
            "text": insight_text,
            "type": "recurring — strong repeating topic",
            "topic": topic,
            "score": n_entries * n_days,
        }
    else:
        insight_text += "This is a blind spot — no KG triples relate to this topic."
        return {
            "text": insight_text,
            "type": "blind_spot",
            "topic": topic,
            "score": n_entries * n_days,
        }


def _build_emerging_insight(topic: str, n_entries: int, n_days: int,
                             sentiment: str, xref: dict) -> dict:
    """Build insight for an emerging topic (>=3 entries)."""
    insight_text = (
        f"{topic}: emerging topic with {n_entries} mentions "
        f"over {n_days} days ({sentiment}). "
    )
    if xref["count"] == 0:
        insight_text += "Not yet in KG — potential blind spot."
        return {
            "text": insight_text,
            "type": "blind_spot",
            "topic": topic,
            "score": n_entries,
        }
    return {
        "text": insight_text,
        "type": "emerging — discussed multiple times",
        "topic": topic,
        "score": n_entries,
    }


def _build_minor_blind_spot(topic: str, n_entries: int, xref: dict) -> dict | None:
    """Build insight for a small cluster not in KG."""
    if xref["count"] == 0:
        insight_text = (
            f"{topic}: mentioned {n_entries} times but no KG triples. "
            f"Potential blind spot or new area."
        )
        return {
            "text": insight_text,
            "type": "blind_spot",
            "topic": topic,
            "score": n_entries,
        }
    return None  # small cluster with KG coverage — low priority, skip


def _generate_insights(
    clusters: list[list[dict]],
    total_entries: int,
    kg_topics: dict,
) -> list[dict]:
    """Generate insights from clusters.

    Returns list of dict: {text, type, cluster, topic, score}.
    """
    insights = []
    seen_topics = set()

    for group in clusters:
        topic = _cluster_topic(group)
        topic_lower = topic.lower()
        if topic_lower in seen_topics:
            continue
        seen_topics.add(topic_lower)

        n_entries = len(group)
        n_days = _days_spread(group)
        sentiment = _cluster_sentiment(group)
        cluster_kw = _keywords(topic)
        xref = _cross_ref(cluster_kw, kg_topics)

        if n_entries >= 5 and n_days >= 3:
            insights.append(
                _build_recurring_insight(topic, n_entries, n_days, sentiment, xref)
            )
        elif n_entries >= 3:
            insights.append(
                _build_emerging_insight(topic, n_entries, n_days, sentiment, xref)
            )
        else:
            ins = _build_minor_blind_spot(topic, n_entries, xref)
            if ins is not None:
                insights.append(ins)

    # Sort by score descending, return top
    insights.sort(key=lambda x: x["score"], reverse=True)
    return insights[:10]  # return top 10, handler picks top 3 to store


# ── main API ──────────────────────────────────────────────────────


def _build_report_header(days_back: int, total_entries: int,
                          total_clusters: int, total_insights: int,
                          stored_count: int) -> list[str]:
    """Build the report header lines."""
    return [
        f"[SESSION_SYNTHESIS] Cross-session Analysis (last {days_back} days)",
        f"  Memory entries scanned: {total_entries}",
        f"  Topic clusters found: {total_clusters}",
        f"  Insights generated: {total_insights}",
        f"  Insights stored: {stored_count}",
        "",
    ]


def _build_cluster_description(n_entries: int, n_days: int) -> str:
    """Build a human-readable frequency description for a cluster."""
    if n_entries >= 5 and n_days >= 3:
        return f"recurring — user discussed this {n_entries} times in {n_days} days"
    elif n_entries >= 3:
        return f"emerging — user mentioned this {n_entries} times in {n_days} days"
    elif n_days > 1:
        return f"occasional — {n_entries} entries across {n_days} days"
    else:
        return f"one-off — {n_entries} entries on {n_days} day"


def _build_cluster_report_lines(clusters: list[list[dict]], kg_topics: dict,
                                 all_insights: list[dict]) -> list[str]:
    """Build report lines for each cluster (max 15)."""
    lines = []
    for i, group in enumerate(clusters[:15]):
        topic = _cluster_topic(group)
        n_entries = len(group)
        n_days = _days_spread(group)
        sentiment = _cluster_sentiment(group)
        cluster_kw = _keywords(topic)
        xref = _cross_ref(cluster_kw, kg_topics)

        freq_desc = _build_cluster_description(n_entries, n_days)

        if xref["count"] > 0:
            kg_line = (
                f"KG cross-ref: has {xref['count']} related triples "
                f"({xref['signal_count']} signal, {xref['reference_count']} reference)"
            )
        else:
            kg_line = "KG cross-ref: none — blind spot"

        lines.append(f'Topic Cluster: "{topic}"')
        lines.append(f"  Entries: {n_entries}")
        lines.append(f"  Pattern: {freq_desc}")
        lines.append(f"  Sentiment: {sentiment}")
        lines.append(f"  KG cross-ref: {kg_line}")

        matching = [ins for ins in all_insights if ins["topic"] == topic]
        if matching:
            lines.append(f'  Insight: {matching[0]["text"][:150]}')

        lines.append("")
    return lines


def _store_top_insights(ms: MemoryStore, top_insights: list[dict], store: bool) -> int:
    """Store top 3 insights back into memory. Returns count stored."""
    stored_count = 0
    if store and top_insights:
        for ins in top_insights:
            result = ms.remember(
                content=f"[Session Synthesis] Insight: {ins['text']}",
                tags=["synthesis", "insight", ins["type"]],
                source="synthesis",
            )
            if result.get("id") != "rejected":
                stored_count += 1
    return stored_count


def _build_insight_summary(all_insights: list[dict]) -> list[str]:
    """Build the top insights summary lines."""
    if not all_insights:
        return []
    lines = ["[SESSION_SYNTHESIS] Top Insights:"]
    for i, ins in enumerate(all_insights[:5], 1):
        lines.append(f"  {i}. [{ins['type']}] {ins['text'][:200]}")
    return lines


def _append_evolve_context(lines: list[str]) -> list[str]:
    """Append learned lessons from evolve system if available."""
    try:
        lessons = get_learned_lessons_summary()
        if lessons:
            lines.append("")
            lines.append("[SESSION_SYNTHESIS] Evolve context:")
            lines.append(f"  {lessons}")
    except Exception:
        pass
    return lines


def synthesize(days_back: int = 7, store: bool = True) -> str:
    """Run cross-session synthesis on MemoryStore memories.

    Args:
        days_back: How many days of memory to analyze (default 7).
        store: Whether to store top 3 insights back into memory (default True).

    Returns:
        Plain-text synthesis report.
    """
    ms = MemoryStore(_DATA_DIR)

    # 1. Collect entries from last N days
    recent = _entries_since(ms, days_back)
    total_entries = len(recent)

    if not recent:
        return (
            f"[SESSION_SYNTHESIS] Cross-session Analysis (last {days_back} days)\n"
            f"  Memory entries scanned: 0\n"
            f"  No entries found in the last {days_back} days.\n"
            f"  Try increasing days_back or adding more memories first."
        )

    # 2. Cluster by keyword similarity
    clusters = _cluster_entries(recent)
    total_clusters = len(clusters)

    # 3. Load KG topics for cross-reference
    kg_topics = _kg_topics()

    # 4. Generate insights
    all_insights = _generate_insights(clusters, total_entries, kg_topics)
    total_insights = len(all_insights)

    # 5. Store top 3 insights back into memory
    top_insights = all_insights[:3]
    stored_count = _store_top_insights(ms, top_insights, store)

    # 6. Build output
    lines = _build_report_header(days_back, total_entries, total_clusters,
                                  total_insights, stored_count)
    lines.extend(_build_cluster_report_lines(clusters, kg_topics, all_insights))
    lines.extend(_build_insight_summary(all_insights))
    lines = _append_evolve_context(lines)

    return "\n".join(lines)


# ── handler + TOOL_DEF ────────────────────────────────────────────


def _handler(args: dict) -> str:
    days_back = args.get("days_back", 7)
    store = args.get("store", True)
    return synthesize(days_back=days_back, store=store)


TOOL_DEF = {
    "name": "session_synthesis",
    "description": (
        "[SYNTHESIS] Cross-session pattern analysis across memories. "
        "Groups recent memories by topic similarity, detects recurring patterns, "
        "corrections vs confirmations, blind spots (topics not in KG), and "
        "evolving understanding. The 'attention' counterpart to Bigram memory."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "days_back": {
                "type": "integer",
                "description": "How many days of memories to analyze (default: 7).",
                "default": 7,
            },
            "store": {
                "type": "boolean",
                "description": "Store top 3 insights back into MemoryStore (default: True).",
                "default": True,
            },
        },
    },
    "risk_level": "low",
}
