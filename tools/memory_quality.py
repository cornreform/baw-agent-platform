"""BAW built-in: Memory Quality — composite 'memory loss score'.

Inspired by Karpathy's Bigram LLM loss function concept:
- KG signal/noise ratio as loss component
- Memory store health as loss component
- Fragmentation as loss component
- Composite loss = lower is better

Uses kg_curator.stats() for KG and MemoryStore.stats() for memory.
"""

import json
import sys
from collections import Counter
from pathlib import Path

_BAW_ROOT = str(Path(__file__).resolve().parent.parent)
if _BAW_ROOT not in sys.path:
    sys.path.insert(0, _BAW_ROOT)

from tools.kg_curator import stats as kg_stats
from core.memory import MemoryStore

_KG_FILE = Path.home() / ".baw" / "knowledge_graph.json"
_NOISE_RELS = {"mentioned_in", "tagged"}


# ── helpers ───────────────────────────────────────────────────────


def _load_kg() -> dict:
    """Load KG file or return empty dict."""
    if not _KG_FILE.exists():
        return {}
    try:
        return json.loads(_KG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _kg_metrics(data: dict) -> tuple[int, int, int, float]:
    """Return (total, signal_count, noise_count, signal_ratio_pct)."""
    triples = data.get("triples", [])
    total = len(triples)
    if not total:
        return (0, 0, 0, 0.0)
    counts = Counter(t.get("r", "") for t in triples)
    noise_count = sum(c for r, c in counts.items() if r in _NOISE_RELS)
    signal_count = total - noise_count
    signal_pct = round(signal_count / total * 100, 1)
    return (total, signal_count, noise_count, signal_pct)


def _memory_metrics() -> dict:
    """Return MemoryStore stats dict."""
    data_dir = Path.home() / ".baw"
    return MemoryStore(data_dir).stats()


def _compute_loss(total_kg: int, noise_count: int, avg_score: float, edges: int) -> float:
    """Composite loss score. Lower = better.

    Formula:
      noise_ratio * 0.5 + (1 - avg_memory_score) * 0.3 + fragmentation_penalty * 0.2

    - noise_ratio = noise / total_kg (0-1, higher = worse)
    - avg_memory_score from memory store (0-1, lower = worse)
    - fragmentation_penalty = 0.1 if no edges, 0 otherwise
    """
    noise_ratio = noise_count / total_kg if total_kg > 0 else 1.0
    score_penalty = 1.0 - avg_score
    frag_penalty = 0.1 if edges == 0 else 0.0
    return round(noise_ratio * 0.5 + score_penalty * 0.3 + frag_penalty * 0.2, 4)


# ── public API ────────────────────────────────────────────────────


def quality_report() -> str:
    """Full plain-text quality report covering KG + memory store + loss score.

    No emoji, no ** markdown, pure ASCII text.
    """
    kg_data = _load_kg()
    total_kg, sig_count, noise_count, sig_pct = _kg_metrics(kg_data)
    mem = _memory_metrics()
    total_mem = mem.get("total", 0)
    avg_score = mem.get("avg_score", 0)
    high_score = mem.get("high_score", 0)
    edges = mem.get("edges", 0)
    loss = _compute_loss(total_kg, noise_count, avg_score, edges)

    lines = [
        "=" * 50,
        "[MEMORY_QUALITY] System Quality Report",
        "=" * 50,
        "",
        "[MEMORY_QUALITY] Knowledge Graph",
        f"  Total triples: {total_kg}",
        f"  Signal:  {sig_count} ({sig_pct}%)",
        f"  Noise:   {noise_count}",
        "",
        "[MEMORY_QUALITY] Memory Store",
        f"  Total entries: {total_mem}",
        f"  Avg score:     {avg_score}",
        f"  High score:    {high_score}",
        f"  Edges:         {edges}",
        "",
        "-" * 50,
        f"  Composite Loss Score: {loss}  (lower is better)",
        "  = noise_ratio*0.5 + (1-avg_score)*0.3 + frag_penalty*0.2",
        "=" * 50,
    ]
    return "\n".join(lines)


def loss_score() -> str:
    """Return just the composite loss score as a string."""
    kg_data = _load_kg()
    _, _, noise_count, _ = _kg_metrics(kg_data)
    total_kg = len(kg_data.get("triples", []))
    mem = _memory_metrics()
    avg_score = mem.get("avg_score", 0)
    edges = mem.get("edges", 0)
    loss = _compute_loss(total_kg, noise_count, avg_score, edges)
    return str(loss)


# ── handler + TOOL_DEF ────────────────────────────────────────────


def _handler(args: dict) -> str:
    action = args.get("action", "both")
    if action == "report":
        return quality_report()
    elif action == "loss":
        return loss_score()
    else:  # "both" (default)
        report = quality_report()
        ls = loss_score()
        return report + "\n" + ls


TOOL_DEF = {
    "name": "memory_quality",
    "description": (
        "[MEMORY] Report memory system quality: KG signal/noise, memory store health, "
        "and composite loss score (lower = better). Inspired by Karpathy Bigram LLM loss."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["report", "loss", "both"],
                "default": "both",
            },
        },
    },
    "risk_level": "low",
}
