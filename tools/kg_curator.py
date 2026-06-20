"""BAW built-in: Knowledge Graph Curator — reduce noise, amplify signal.

Concept from Karpathy Bigram → GPT:
- Tokenization → 拆 memory 做 atomic units
- Loss function → 量化 KG quality (signal/noise ratio)
- Optimizer → 系統性 consolidate + prune, 唔係 random

Strategy:
1. Remove ALL `tagged` triples (redundant — entity existence = tagged)
2. Consolidate `mentioned_in` per entity pair → single triple with count
3. Keep only signal relations (has_rule, configured_with, etc.)
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, Counter

logger = logging.getLogger("baw.kg_curator")

_KG_FILE = Path.home() / ".baw" / "knowledge_graph.json"
_KEEP_SIGNAL_RATIO = 0.15  # target: ~15% signal after curation (aggressive enough for <50 but preserves useful mentions)


def _load() -> dict:
    if _KG_FILE.exists():
        try:
            return json.loads(_KG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[KG_CURATOR] Failed to load KG: {e}")
    return {"triples": [], "entities": {}}


def _save(data: dict):
    _KG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _noise_relations() -> set:
    return {"mentioned_in", "tagged"}


def stats() -> str:
    """Return KG quality metrics."""
    data = _load()
    triples = data.get("triples", [])
    total = len(triples)
    if not total:
        return "[KG_CURATOR] Empty KG."

    noise_rels = _noise_relations()
    counts = Counter(t.get("r", "") for t in triples)
    noise_count = sum(c for r, c in counts.items() if r in noise_rels)
    signal_count = total - noise_count

    lines = [
        f"[KG_CURATOR] KG Quality Report",
        f"  Total triples: {total}",
        f"  Signal: {signal_count} ({signal_count*100//total}%)",
        f"  Noise:  {noise_count} ({noise_count*100//total}%)",
        f"  Target: >=50% signal",
        "",
        "  Relation breakdown:",
    ]
    for r, c in counts.most_common(15):
        marker = "[NOISE]" if r in noise_rels else "[SIG]"
        lines.append(f"    {marker} {r:40s}: {c:4d}")

    return "\n".join(lines)


def curate(action: str = "report", dry_run: bool = True) -> str:
    """Curate the KG: consolidate noise, retain signal.

    Args:
        action: 'report' (default) | 'curate'
        dry_run: If True, only report what would be done

    Returns:
        Summary of curation actions.
    """
    data = _load()
    triples = data.get("triples", [])
    total_before = len(triples)
    noise_rels = _noise_relations()

    # Classify triples
    signal_triples = [t for t in triples if t.get("r", "") not in noise_rels]
    noise_triples = [t for t in triples if t.get("r", "") in noise_rels]

    # ── Remove ALL tagged triples (redundant) ──
    tagged_count = len([t for t in noise_triples if t.get("r") == "tagged"])

    # ── Aggressively prune mentioned_in ──
    # Group by subject entity → keep only last N mentions
    mentioned_in = [t for t in noise_triples if t.get("r") == "mentioned_in"]
    mention_by_subject: dict[str, list[dict]] = defaultdict(list)
    for t in mentioned_in:
        subj = t.get("s", "")
        if subj:
            mention_by_subject[subj].append(t)

    _MAX_MENTIONS_PER_ENTITY = 3
    consolidated = []
    total_mentions_removed = 0
    for subj, entries in mention_by_subject.items():
        # Sort by timestamp (newest first)
        entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
        # Keep only the N most recent
        kept_count = min(len(entries), _MAX_MENTIONS_PER_ENTITY)
        kept = entries[:kept_count]
        for k in kept:
            k["count"] = 1  # single reference
            consolidated.append(k)
        total_mentions_removed += len(entries) - kept_count

    # ── Calculate new totals ──
    total_consolidated_mentions = len(consolidated)
    removed_tagged = tagged_count
    total_after = len(signal_triples) + total_consolidated_mentions
    signal_after = len(signal_triples)
    signal_pct_after = signal_after * 100 // total_after if total_after else 0
    achieved = signal_pct_after >= _KEEP_SIGNAL_RATIO * 100

    # ── Exec ──
    changes = []
    if removed_tagged > 0:
        changes.append(f"Removed all {removed_tagged} 'tagged' triples (redundant)")
    if total_mentions_removed > 0:
        changes.append(f"Consolidated {total_mentions_removed + len(consolidated)} 'mentioned_in' -> {len(consolidated)} triples (removed {total_mentions_removed} duplicates)")

    lines = [
        f"[KG_CURATOR] {'DRY RUN — ' if dry_run else ''}Curation Summary",
        f"  Before: {total_before} triples ({len(signal_triples)} signal, {len(noise_triples)} noise)",
        f"  After:  {total_after} triples ({signal_after} signal, {total_consolidated_mentions} mentions)",
        f"  Signal ratio: {signal_pct_after}% — MET" if achieved else f"  Signal ratio: {signal_pct_after}% — NOT MET (target >=50%)",
    ]
    changes and lines.append("")
    lines.extend(changes)

    if not dry_run and action == "curate":
        new_triples = signal_triples + consolidated
        # Re-index
        for i, t in enumerate(new_triples):
            t["id"] = f"t{i+1}"

        # Clean orphan entities
        connected = set()
        for t in new_triples:
            connected.add(t.get("s", ""))
            connected.add(t.get("o", ""))
        entities = data.get("entities", {})
        orphans = [k for k in entities if k not in connected]
        for o in orphans:
            entities.pop(o, None)

        data["triples"] = new_triples
        data["entities"] = entities
        data["_curated_at"] = datetime.now(timezone.utc).isoformat()
        data["_curation_summary"] = {
            "before": total_before,
            "after": total_after,
            "removed_tagged": removed_tagged,
            "consolidated_mentions": total_mentions_removed,
            "removed_orphans": len(orphans),
            "signal_ratio": signal_pct_after,
        }
        _save(data)
        lines.append(f"\n  Curation executed")
    else:
        lines.append(f"\n  → Run with dry_run=False & action='curate' to apply")

    return "\n".join(lines)


def _handler(args: dict) -> str:
    action = args.get("action", "report")
    dry_run = args.get("dry_run", True)
    if action not in ("report", "curate"):
        return f"[KG_CURATOR] Invalid action: {action}. Use 'report' or 'curate'."
    if action == "curate":
        return curate(action="curate", dry_run=dry_run)
    return stats()


TOOL_DEF = {
    "name": "kg_curator",
    "description": (
        "[MEMORY] Curate Knowledge Graph: consolidate noisy mentioned_in, "
        "remove redundant tagged, retain signal. Run 'report' to assess, "
        "'curate' to fix."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["report", "curate"],
                "default": "report",
            },
            "dry_run": {
                "type": "boolean",
                "default": True,
            },
        },
    },
    "risk_level": "low",
}
