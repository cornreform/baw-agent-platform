"""
BAW Memory Curator — think before you remember.

Intercepts every memory write and decides:
1. Is this worth remembering? (value classification)
2. Does this correct/update existing memory? (conflict detection)
3. Should we save new, update existing, merge, or discard?
4. What priority score should it get?

Without this gate, BAW saves everything indiscriminately — install logs,
transient status, and critical user preferences all get equal treatment.
"""

import re
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("baw.memory_curator")

# ── Content classifiers ──────────────────────────────────────────

class Classification:
    PREFERENCE = "preference"       # User likes/dislikes, style choices, workflow prefs
    CONFIG = "config"               # System setup, provider config, API keys (redacted)
    BUG = "bug"                     # Bug reports, error patterns, workarounds
    INSTALL = "install"             # Package installs, tool setup, dependencies
    DISCOVERY = "discovery"         # New knowledge, research findings, architecture insight
    FACT = "fact"                   # Durable facts about the system/environment
    COMMAND = "command"             # Successful CLI commands, working recipes
    CORRECTION = "correction"       # Fixing a previous incorrect assumption
    TRANSIENT = "transient"         # Temp info, session state, progress updates
    NOISE = "noise"                 # Trivial chatter, tool output, system logs

    ALL = [PREFERENCE, CONFIG, BUG, INSTALL, DISCOVERY, FACT, COMMAND, CORRECTION, TRANSIENT, NOISE]


# ── Keywords and patterns for each classification ──

_CLASSIFIER_RULES = [
    # --- Noise (drop immediately) ---
    (Classification.NOISE, [
        r"^(?:ok|done|完成|yes|no|收到|明白|瞭|好的|好嘅)$",
        r"^tool.*(?:call|result|output).*:?\s*$",
        r"^\d+/\d+\s+(?:tests?|checks?|steps?)",
        r"^\[\w+\]\s*$",
        r"^(?:Memory|Note|Fact)\s+(?:saved|stored|recorded)",
        r"^(?:Updating|Updated|Reloaded|Refreshed)",
        r"^(?:Checking|Waiting|Polling|Monitoring)",
        r"^(?:already\s+)?(?:exists|present|configured)",
    ]),
    # --- Preference (high value) ---
    (Classification.PREFERENCE, [
        r"(?:prefer|preference|like|喜歡|偏好|鍾意|唔鍾意|討厭|想要|寧願)",
        r"(?:tone|style|format|language|粵語|英文|語氣)",
        r"(?:簡潔|詳細|精簡|長度|長短)",
        r"(?:每次|永遠|always|never|always|通常|usually)",
        r"(?:output|輸出).*(?:format|格式|方式)",
    ]),
    # --- Correction (high value) ---
    (Classification.CORRECTION, [
        r"(?:correct|修正|更正|改正|fix|fixing|fixed)",
        r"(?:wrong|錯誤|錯|incorrect|not correct)",
        r"(?:我之前話|I previously said|更正之前)",
        r"(?:actually|其實|事實上)",
    ]),
    # --- Bug (high value) ---
    (Classification.BUG, [
        r"(?:bug|error|fail|issue|problem|問題|出錯|壞|爛)",
        r"(?:traceback|exception|crash|timeout|silent fail)",
        r"(?:root cause|原因|根本原因)",
        r"(?:workaround|繞過|解決方法|fix for)",
    ]),
    # --- Config (medium-high value) ---
    (Classification.CONFIG, [
        r"(?:config|setting|setup|配置|設定|provider|endpoint)",
        r"(?:model|provider|base_url|api_key)",
        r"(?:installed|setup|deploy|deployment)",
    ]),
    # --- Discovery (medium value) ---
    (Classification.DISCOVERY, [
        r"(?:發現|discover|found out|learned|學到|了解到)",
        r"(?:architecture|架構|design|設計)",
        r"(?:recommend|建議|suggest|提議)",
    ]),
    # --- Fact (medium value) ---
    (Classification.FACT, [
        r"(?:事實|fact|note|知道|注意)",
        r"(?:runs on|works with|compatible|supports)",
        r"(?:version|version|current|latest)",
    ]),
    # --- Command (medium value) ---
    (Classification.COMMAND, [
        r"(?:command|cmd|cli|terminal|bash|script)",
        r"(?:run|execute|execute) .*(?:成功|success|ok|done)",
    ]),
    # --- Install (medium-low value) ---
    (Classification.INSTALL, [
        r"(?:install|安裝|setup|set up|build)",
        r"(?:package|module|dependency|deps)",
    ]),
    # --- Transient (low value, save but decay fast) ---
    (Classification.TRANSIENT, [
        r"(?:current|now|this session|today)",
        r"(?:progress|status|正在|進行中)",
        r"(?:task|job|run|batch)",
    ]),
]


def classify(content: str) -> tuple[str, float]:
    """Classify content and return (classification, confidence 0-1).

    Uses multiple matching rules; highest confidence wins.
    """
    best_class = Classification.TRANSIENT
    best_conf = 0.0

    for cls, patterns in _CLASSIFIER_RULES:
        for pat in patterns:
            m = re.search(pat, content, re.IGNORECASE)
            if m:
                # Match length relative to content length = confidence
                match_len = len(m.group(0))
                conf = min(1.0, match_len / max(len(content), 1) * 3 + 0.3)
                if conf > best_conf:
                    best_class = cls
                    best_conf = conf

    # Default: if content is very short, it's noise
    if len(content.strip()) < 10 and best_conf < 0.5:
        return Classification.NOISE, 0.8

    # Correction supercedes other classifications if strong match
    if best_class in (Classification.PREFERENCE, Classification.BUG, Classification.FACT):
        # These are high-value — don't downgrade
        pass

    return best_class, round(min(best_conf, 1.0), 2)


def value_score(cls: str) -> float:
    """Return decay-resistant priority score based on classification.

    Score range: 0.0 (forget immediately) to 1.0 (permanent).
    Used as base for entry.score in MemoryStore.
    """
    SCORES = {
        Classification.PREFERENCE: 0.85,
        Classification.CORRECTION: 0.80,
        Classification.BUG:        0.75,
        Classification.CONFIG:     0.70,
        Classification.FACT:       0.65,
        Classification.DISCOVERY:  0.60,
        Classification.COMMAND:    0.55,
        Classification.INSTALL:    0.45,
        Classification.TRANSIENT:  0.30,
        Classification.NOISE:      0.05,
    }
    return SCORES.get(cls, 0.50)


# ── Conflict detection ──

def detect_conflicts(content: str, existing_entries: list[dict]) -> Optional[dict]:
    """Check if new content conflicts with or updates existing memories.

    Returns:
        {
            "type": "update" | "duplicate" | "contradiction" | None,
            "target_id": str,
            "reason": str,
        }
    """
    if not existing_entries:
        return None

    content_norm = content.strip().lower()
    content_kw = set(re.findall(r'[a-zA-Z0-9_\u4e00-\u9fff]+', content_norm))

    for entry in existing_entries:
        existing = entry.get("content", "").strip().lower()
        if not existing:
            continue

        # — Exact match = duplicate —
        if existing == content_norm:
            return {
                "type": "duplicate",
                "target_id": entry["id"],
                "reason": "Exact duplicate content",
            }

        existing_kw = set(re.findall(r'[a-zA-Z0-9_\u4e00-\u9fff]+', existing))

        # — High keyword overlap = potential update —
        if len(content_kw) > 3 and len(existing_kw) > 3:
            overlap = content_kw & existing_kw
            jaccard = len(overlap) / max(len(content_kw | existing_kw), 1)

            # Check for correction signals in content
            correction_signals = ["actually", "更正", "修正", "正確是", "不過", "但係"]
            negation_signals = ["not", "but", "no", "don't", "唔係", "不是"]
            has_correction_signal = any(s in content_norm for s in correction_signals + negation_signals)

            if jaccard >= 0.6:
                if has_correction_signal:
                    return {
                        "type": "update",
                        "target_id": entry["id"],
                        "reason": f"Same topic ({jaccard:.0%} keyword overlap) with correction signals — should update old entry",
                    }
                elif jaccard >= 0.85:
                    return {
                        "type": "duplicate",
                        "target_id": entry["id"],
                        "reason": f"High similarity ({jaccard:.0%} keyword overlap)",
                    }

            # Broader match: correction signal + at least 2 overlapping keywords
            if has_correction_signal and len(overlap) >= 2 and jaccard >= 0.15:
                return {
                    "type": "update",
                    "target_id": entry["id"],
                    "reason": f"Correction detected ({jaccard:.0%} overlap, {len(overlap)} shared keywords)",
                }

            if jaccard >= 0.4 and _detect_contradiction(content, existing):
                return {
                    "type": "contradiction",
                    "target_id": entry["id"],
                    "reason": f"Contradicts existing memory ({jaccard:.0%} overlap)",
                }

    return None


def _detect_contradiction(a: str, b: str) -> bool:
    """Rough contradiction detection via negation markers."""
    negation_markers = [
        ("not", "is"), ("don't", "do"), ("doesn't", "does"),
        ("cannot", "can"), ("won't", "will"),
        ("唔係", "係"), ("不是", "是"),
        ("no", "yes"), ("false", "true"),
        ("不要", "要"), ("不需要", "需要"),
    ]
    a_lower = a.lower()
    b_lower = b.lower()
    for neg, pos in negation_markers:
        if (neg in a_lower and pos in b_lower) or (neg in b_lower and pos in a_lower):
            return True
    return False


# ── Main curator API ──

def curate(content: str, *, tags: list[str] = None, source: str = "agent",
           existing_entries: list[dict] = None) -> dict:
    """Curate a memory write attempt. Returns a decision dict.

    Args:
        content: The text to be remembered.
        tags: Proposed tags.
        source: Who created this memory.
        existing_entries: Recent memory entries for conflict detection.

    Returns:
        {
            "action": "save" | "update" | "merge" | "discard" | "flag",
            "content": str (maybe modified),
            "target_id": str | None (for update/merge),
            "tags": list[str],
            "source": str,
            "classification": str,
            "score": float,
            "reason": str,
        }
    """
    # 1. Classify
    cls, conf = classify(content)
    score = value_score(cls)

    # 2. Noise gate — discard obvious noise
    if cls == Classification.NOISE and conf >= 0.6:
        return {
            "action": "discard",
            "content": content,
            "target_id": None,
            "tags": tags or [],
            "source": source,
            "classification": cls,
            "score": 0.0,
            "reason": f"Noise (conf={conf}) — not worth remembering",
        }

    # 3. Conflict detection
    conflict = None
    if existing_entries:
        conflict = detect_conflicts(content, existing_entries)

    if conflict:
        if conflict["type"] == "duplicate":
            return {
                "action": "discard",
                "content": content,
                "target_id": conflict["target_id"],
                "tags": tags or [],
                "source": source,
                "classification": cls,
                "score": score,
                "reason": f"Duplicate — {conflict['reason']}",
            }
        elif conflict["type"] == "update":
            # Update existing entry content (merge)
            merged_content = content
            return {
                "action": "update",
                "content": merged_content,
                "target_id": conflict["target_id"],
                "tags": tags or [],
                "source": source,
                "classification": cls,
                "score": min(1.0, score + 0.10),  # Correction gets bonus
                "reason": f"Update — {conflict['reason']}",
            }
        elif conflict["type"] == "contradiction":
            # Flag for user review
            return {
                "action": "flag",
                "content": content,
                "target_id": conflict["target_id"],
                "tags": tags or [],
                "source": source,
                "classification": cls,
                "score": score,
                "reason": f"Contradicts mem_{conflict['target_id']} — {conflict['reason']}",
            }

    # 4. Save normally with curator-assigned score
    # Clean tags based on classification
    curator_tags = tags or []
    if cls not in curator_tags:
        curator_tags.append(cls)

    return {
        "action": "save",
        "content": content,
        "target_id": None,
        "tags": curator_tags,
        "source": source,
        "classification": cls,
        "score": score,
        "reason": f"New {cls} (conf={conf}) — score={score}",
    }
