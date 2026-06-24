"""
BAW — Tone Switcher (P0)
Runtime tone switching via user message.

User can write:
  "改用 business tone"
  "tone: teaching"
  "切換去 casual mode"
  "formal please"

The ToneRouter detects these patterns and updates the config.
"""

from __future__ import annotations
import re
from typing import Optional

# Valid tone profiles from config
VALID_TONES = {"casual", "business", "client-doc", "teaching", "ot-rt", "stepwise"}

# Detection patterns: "tone: X", "switch to X tone", "用 X tone", "改用 X", etc.
TONE_PATTERNS = [
    re.compile(r'(?:tone|mode)[:\s]+(\w[\w-]*)', re.IGNORECASE),
    re.compile(r'(?:switch|change|set)\s+(?:to\s+)?(\w[\w-]*)\s+(?:tone|mode)', re.IGNORECASE),
    re.compile(r'(?:用|改用|轉用|切換到|改做|轉做)\s*(\w[\w-]*)\s*(?:tone|mode|模式)', re.IGNORECASE),
    re.compile(r'(\w[\w-]*)\s+tone\s+please', re.IGNORECASE),
    re.compile(r'(\w[\w-]*)\s+(?:mode|tone)(?:\s+pls|\s+please)?', re.IGNORECASE),
    re.compile(r'(?:切換|轉|改)\s*(?:做|去|到)?\s*(\w[\w-]*)', re.IGNORECASE),
]

# Also detect "ot rt" / "OTRT" / "stepwise" standalone
STANDALONE_PATTERNS = [
    (re.compile(r'\bOT\s*RT\b|\bot-rt\b|\botrt\b', re.IGNORECASE), "ot-rt"),
    (re.compile(r'\bstepwise\b', re.IGNORECASE), "stepwise"),
    (re.compile(r'\bcasual\s+mode\b|\b吹水\s+mode\b|\bchill\b', re.IGNORECASE), "casual"),
    (re.compile(r'\bformal\b|\bbusiness\b|\bprofessional\b', re.IGNORECASE), "business"),
    (re.compile(r'\bteaching\b|\b教學\b', re.IGNORECASE), "teaching"),
    (re.compile(r'\bclient\s*doc\b|\bclient-facing\b', re.IGNORECASE), "client-doc"),
]


def detect_tone_switch(user_message: str) -> Optional[str]:
    """Detect if user wants to switch tone. Returns new tone name or None."""
    # Check standalone patterns first (simpler match)
    for pattern, tone in STANDALONE_PATTERNS:
        if pattern.search(user_message):
            if tone in VALID_TONES:
                return tone

    # Check structured patterns
    for pattern in TONE_PATTERNS:
        m = pattern.search(user_message)
        if m:
            candidate = m.group(1).lower().strip()
            # Normalize common variations
            normalization = {
                "otrt": "ot-rt",
                "ot rt": "ot-rt",
                "ot_rt": "ot-rt",
                "clientdoc": "client-doc",
                "client_doc": "client-doc",
            }
            candidate = normalization.get(candidate, candidate)
            if candidate in VALID_TONES:
                return candidate

    return None


def format_tone_confirmation(old_tone: str, new_tone: str) -> str:
    """Format a confirmation message for tone switch."""
    descriptions = {
        "casual": "日常粵語口語、短句、emoji",
        "business": "客戶文件 — 合作邀約 tone、唔出個人名、冇 deadline 壓力",
        "client-doc": "Client facing — 零 comment、零 meta、直接出 artifact",
        "teaching": "教學模式 — 直接俾 .md、唔問 extra format",
        "ot-rt": "OT✅RT✅ — 做完先報、唔逐 step confirm",
        "stepwise": "逐步執行 — 每步報 result、等 confirm 先繼續",
    }
    desc = descriptions.get(new_tone, "")
    return f"[Tone] {old_tone} → {new_tone}\n{desc}" if desc else f"[Tone] {old_tone} → {new_tone}"
