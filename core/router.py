"""Task complexity scoring & tier-based routing.

Design goals:
- Fast trivial tasks → cheap fast model, no sub-agent overhead
- Medium tasks → primary model does it inline
- Complex tasks → delegated to sub-agent with a powerful model
- Expert tasks → multi-tier cascade (re-delegate if first sub-agent
  produces a result that needs further decomposition)

Complexity score 0-10:
- 0-3:  TRIVIAL  → fast model inline, no delegation
- 4-6:  MODERATE → primary model inline, multi-step tool use ok
- 7-9:  COMPLEX  → delegate to sub-agent w/ powerful model
- 10:   EXPERT   → multi-tier cascade (delegate-decompose-redelegate)

Score = weighted sum of:
  + tool count needed           (×1)
  + reasoning depth keywords    (×2)
  + context length              (×1, scaled)
  + Cantonese-language depth    (×1)
  + multi-step / verification   (×2)
  + external side effects       (×3)
"""
from __future__ import annotations
import os
import re
from typing import Tuple


# ── Cheap regex-based complexity features ──

# Reasoning-depth keywords (each hit +3)
_DEEP_KEYWORDS = (
    r"\b(分析|比較|評估|architect|design|debug|debugging|"
    r"設計|架構|複雜|深入|原因|root cause|trace|"
    r"睇|check|檢查|確認|調查|review|audit|"
    r"config|配置|設定|provider|model|tier|"
    r"系統|system|status|狀態|報告|report|"
    r"完整|comprehensive|deep dive|策略|strategy|"
    r"troubleshoot|診斷|diagnose|拆解|reverse.?engineer|"
    r"研究|research|explore|"
    r"trade.?off|權衡|optimi[sz]e|最佳化|"
    r"audit|審計|review)\b",
)
# Multi-step / verification (+3)
_MULTISTEP_KEYWORDS = (
    r"\b(然後|之後|跟住|第一步|step 1|first.*then|"
    r"第一|第二|第三|"
    r"step[ -]by[ -]step|iteratively|多步|流程|workflow|"
    r"verify|check.*then|再|之後|plan|規劃|"
    r"deploy.*then|build.*then|"
    r"拆解成|分成|分階段|"
    r"phase|milestone|sprint|"
    r"再.*之後|先.*再|先.*然後)\b",
)
# External side-effects (+2)
_SIDE_EFFECT_KEYWORDS = (
    r"\b(改|刪|delete|write|create.*file|deploy|"
    r"install|git push|commit|執行|run|send.*to|"
    r"apply.*change|modify|update.*config|"
    r"改.*文件|寫.*文件|刪除|安裝|部署|"
    r"commit|merge|rebase|"
    r"download.*and.*install|setup|configure)\b",
)
# Trivial / chatty (-3 — favour fast path)
_CHATTY_KEYWORDS = (
    r"^(hi|hello|你好|早晨|good morning|thanks|多謝|thank you)[.!?]?$",
)


def score_complexity(
    prompt: str,
    estimated_tool_count: int = 0,
    context_tokens: int = 0,
) -> int:
    """Return complexity score 0-10.

    Args:
        prompt: user's raw prompt
        estimated_tool_count: hint from prior call history (0 if unknown)
        context_tokens: estimated context size for this turn

    Returns:
        int score clamped to 0-10
    """
    if not prompt or not prompt.strip():
        return 0

    score = 0
    p = prompt.lower()
    plen = len(prompt)

    # Tool count contribution (each tool +1)
    score += min(estimated_tool_count, 5)

    # Reasoning depth (+3 per keyword hit, max +6)
    hits = sum(1 for pat in _DEEP_KEYWORDS if re.search(pat, p, re.IGNORECASE))
    score += min(hits * 3, 6)

    # Multi-step (+3 per match, max +6)
    hits = sum(1 for pat in _MULTISTEP_KEYWORDS if re.search(pat, p, re.IGNORECASE))
    score += min(hits * 3, 6)

    # Side effects (+2 per match, max +4)
    hits = sum(1 for pat in _SIDE_EFFECT_KEYWORDS if re.search(pat, p, re.IGNORECASE))
    score += min(hits * 2, 4)

    # ── Composite signals: specific patterns imply specific tiers ──
    # Code generation (debug/build/install/compile) → at least moderate
    if re.search(r"\b(debug|compile|build|install|deploy|script|code|python|node|js|api|"
                 r"function|class|method|module|package|library|"
                 r"寫.*code|寫.*script|寫.*function|"
                 r"出.*code|俾.*code)\b", p, re.IGNORECASE):
        score += 3
    # Architecture / design / system-level tasks → at least moderate
    if re.search(r"(架構|設計.*系統|設計.*網站|設計.*app|"
                 r"設計.*方案|design.*system|architect|"
                 r"規劃.*整體|高層次|high.?level|blueprint|"
                 r"完整.*流程|完整.*系統)", p, re.IGNORECASE):
        score += 4
    # Multi-step workflow explicit → boost to complex
    if re.search(r"(先.*再|然後.*之後|step[ -]by[ -]step|拆解成|分階段|"
                 r"first.*then|plan.*execute|規劃.*執行)", p, re.IGNORECASE):
        score += 2
    # Comparison/research/audit-style → at least moderate
    if re.search(r"(比較|對比|trade.?off|pros.*cons|advantage|缺點|"
                 r"優缺點|評估.*唔同|compare.*different|analyze.*multiple)", p, re.IGNORECASE):
        score += 2
    # List of N items / multi-output → complex
    if re.search(r"\b[1-9]\s*(個|把|段|個|sample|voice|file|step|thing|item)s?\b", p, re.IGNORECASE):
        score += 2
    # "send 俾我" / "發送" / "send.*to" / "deliver" → requires external action
    if re.search(r"(send.*俾|send.*to|發送|傳送|deliver|send\b)", p, re.IGNORECASE):
        score += 1

    # Context size: big contexts imply complex reasoning
    if context_tokens > 4000:
        score += 2
    elif context_tokens > 2000:
        score += 1

    # Trivial chat: subtract (floor at 0)
    if re.match(_CHATTY_KEYWORDS[0], prompt.strip(), re.IGNORECASE):
        score -= 3

    # Long prompt: small boost (implies more to think about)
    if plen > 500:
        score += 1

    return max(0, min(score, 10))


# ── Tier definitions ──

TIER_TRIVIAL = "trivial"     # 0-3
TIER_MODERATE = "moderate"   # 4-6
TIER_COMPLEX = "complex"     # 7-9
TIER_EXPERT = "expert"       # 10


def tier_of(score: int) -> str:
    if score <= 3:
        return TIER_TRIVIAL
    if score <= 6:
        return TIER_MODERATE
    if score <= 9:
        return TIER_COMPLEX
    return TIER_EXPERT


# ── Model assignment by tier ──
# IMPORTANT: These are PLACEHOLDERS / DEFAULTS, not authoritative rankings.
# Routing picks a model for a tier purely based on availability and
# configured preference order. The USER decides which model is "best"
# for a given tier — by editing `router.tier_preferences` in config.yaml
# or via the CLI (`baw router set <tier> <model_id>`).
#
# Defaults below are conservative and follow a fast→powerful gradient
# inside each tier so that if the first choice is unavailable we have
# a fallback. The real "tier vs model" mapping is CONFIG-DRIVEN, not
# hardcoded in code.

# P0-3 (Opus 4.8 audit): defaults must reference models that ACTUALLY exist
# in the default config.yaml providers (deepseek-v4-flash / deepseek-reasoner /
# MiniMax-M3 / MiniMax-M2.5). Previously referenced step-3.5-flash-2603 etc.
# don't exist in providers, so routing always fell through to the random
# "last resort" branch and silently picked an arbitrary chat model.
DEFAULT_TIER_PREFERENCES: dict[str, list[str]] = {
    TIER_TRIVIAL: ["deepseek-v4-flash", "MiniMax-M2.5", "MiniMax-M3"],
    TIER_MODERATE: ["deepseek-v4-flash", "MiniMax-M3", "deepseek-reasoner"],
    TIER_COMPLEX: ["deepseek-reasoner", "MiniMax-M3", "MiniMax-M2.5"],
    TIER_EXPERT: ["deepseek-reasoner", "MiniMax-M3"],
}


def get_tier_preferences(config: dict) -> dict[str, list[str]]:
    """Get tier → model preference list.

    Precedence (highest first):
    1. config['router']['tier_preferences'][<tier>]   ← user override
    2. config['model']['default']                      ← user's primary model (dynamically prepended)
    3. DEFAULT_TIER_PREFERENCES[<tier>]                ← hardcoded fallback
    """
    user_prefs = (
        config.get("router", {}).get("tier_preferences", {}) or {}
    )
    default_model = config.get("model", {}).get("default", "")
    merged = {}
    for tier in (TIER_TRIVIAL, TIER_MODERATE, TIER_COMPLEX, TIER_EXPERT):
        if tier in user_prefs and user_prefs[tier]:
            merged[tier] = list(user_prefs[tier])
        else:
            base = list(DEFAULT_TIER_PREFERENCES.get(tier, []))
            # Prepend user's default model — it IS the user's chosen provider
            if default_model and default_model != (base[:1] or [None])[0]:
                base.insert(0, default_model)
            merged[tier] = base
    return merged


# ── Rotation tracker for balanced strategy ──
_rotation_idx: dict[str, int] = {}  # tier name → next index to try


def pick_model_for_tier(tier: str, config: dict) -> str:
    """Pick a model for the tier.

    Two strategies (config.router.strategy):
      - "simple" (default): first available model in preference list
      - "balanced": weighted round-robin through available models

    The tier→model mapping is config-driven. Falls back to
    DEFAULT_TIER_PREFERENCES if user hasn't configured anything.
    Returns any chat-capable model as last resort.
    """
    available = _get_available_chat_models(config)
    preferences = get_tier_preferences(config)
    tier_prefs = preferences.get(tier, preferences.get(TIER_MODERATE, []))

    # Filter to only available models
    available_prefs = [m for m in tier_prefs if m in available]
    if not available_prefs:
        # Last resort: any chat model
        if available:
            return next(iter(available))
        return "step-3.7-flash"

    strategy = (
        config.get("router", {}).get("strategy", "simple") or "simple"
    )

    if strategy == "balanced":
        # Round-robin: cycle through available preference list
        idx = _rotation_idx.get(tier, 0)
        _rotation_idx[tier] = (idx + 1) % len(available_prefs)
        return available_prefs[idx % len(available_prefs)]

    # Simple: first available
    return available_prefs[0]


def _get_available_chat_models(config: dict) -> set[str]:
    """Return set of model IDs that have chat capability and API keys."""
    available = set()
    _SPECIALIZED = {"tts", "asr", "speech", "audio", "image", "dall-e", "whisper", "dall"}
    for pname, pcfg in config.get("providers", {}).items():
        api_key_env = pcfg.get("api_key_env", "")
        if api_key_env and not os.environ.get(api_key_env):
            continue
        for m in pcfg.get("models", []):
            mid = m.get("id", "")
            mid_lower = mid.lower()
            caps = m.get("capabilities", [])
            if any(kw in mid_lower for kw in _SPECIALIZED):
                continue
            if "chat" in caps and mid:
                available.add(mid)
    return available


# ── Decision: inline vs delegate ──

INLINE_DIRECT = "direct"       # 0-5: inline, no sub-agent mention
INLINE_WITH_HINT = "with_hint" # 6-7: inline but can delegate sub-tasks
INLINE_DELEGATE = None         # 8+: full delegate to sub-agent


def should_delegate(score: int) -> bool:
    """Return True if this task should go to a sub-agent.

    Graduated delegation:
      - 0-5:  INLINE direct — model handles everything inline
      - 6-7:  INLINE with sub-agent hint — model CAN delegate sub-tasks
      - 8-9:  DELEGATE to sub-agent
      - 10:   EXPERT — multi-tier cascade
    """
    return score >= 8


def get_inline_mode(score: int) -> str | None:
    """Return the inline execution mode for a given score.

    Returns:
      INLINE_DIRECT    -> 0-5: strict inline, no sub-agent
      INLINE_WITH_HINT -> 6-7: inline but model may delegate sub-tasks
      None             -> 8+:  full delegate path
    """
    if score <= 5:
        return INLINE_DIRECT
    if score <= 7:
        return INLINE_WITH_HINT
    return INLINE_DELEGATE


# ── Multi-tier cascade (for expert tasks) ──

def needs_multi_tier(score: int) -> bool:
    """Return True if this task should use multi-tier cascade.

    Multi-tier means: delegate to sub-agent → if result indicates
    more decomposition needed, re-delegate that sub-task to another
    sub-agent (or the same one with a new prompt).

    Only expert tasks (score=10) get full cascade.
    """
    return score >= 10


# ── Top-level router ──

class RouteDecision:
    """Result of routing decision."""

    def __init__(self, score: int, tier: str, model_id: str,
                 delegate: bool, multi_tier: bool,
                 inline_mode: str | None = INLINE_DIRECT,
                 reasoning: str = ""):
        self.score = score
        self.tier = tier
        self.model_id = model_id
        self.delegate = delegate
        self.multi_tier = multi_tier
        self.inline_mode = inline_mode
        self.reasoning = reasoning

    def __repr__(self):
        return (f"RouteDecision(score={self.score}, tier={self.tier!r}, "
                f"model={self.model_id!r}, delegate={self.delegate}, "
                f"multi_tier={self.multi_tier}, "
                f"inline_mode={self.inline_mode})")


def route_task(
    prompt: str,
    config: dict,
    estimated_tool_count: int = 0,
    context_tokens: int = 0,
) -> RouteDecision:
    """Make a routing decision for a task.

    Returns a RouteDecision with: score, tier, model, delegate?, multi_tier?
    """
    score = score_complexity(
        prompt,
        estimated_tool_count=estimated_tool_count,
        context_tokens=context_tokens,
    )
    tier = tier_of(score)
    model_id = pick_model_for_tier(tier, config)
    delegate = should_delegate(score)
    multi_tier = needs_multi_tier(score)
    inline_mode = get_inline_mode(score)

    reasons = []
    reasons.append(f"score={score}")
    reasons.append(f"tool_hint={estimated_tool_count}")
    reasons.append(f"ctx={context_tokens}")
    reasons.append(f"tier={tier}→{model_id}")
    mode_label = "INLINE_DIRECT" if inline_mode == INLINE_DIRECT else (
        "INLINE_WITH_HINT" if inline_mode == INLINE_WITH_HINT else "DELEGATE"
    )
    reasons.append(mode_label)
    if multi_tier:
        reasons.append("MULTI_TIER_CASCADE")

    return RouteDecision(
        score=score,
        tier=tier,
        model_id=model_id,
        delegate=delegate,
        multi_tier=multi_tier,
        inline_mode=inline_mode,
        reasoning=" | ".join(reasons),
    )


# ── Multi-tier cascade executor ──

def should_re_delegate(sub_result: str) -> bool:
    """Inspect a sub-agent's result. If it indicates the task needs
    further decomposition (e.g. returns a Plan with sub-steps, or
    says "需要再分" / "need to split"), return True so the orchestrator
    re-delegates each sub-step.

    This is the fix for "re-delegate if first sub-agent's result
    needs further delegation" — multi-tier cascade.
    """
    if not sub_result:
        return False
    s = sub_result.lower()
    re_delegate_signals = (
        "需要再分", "需要拆分", "拆分成", "split into",
        "first do", "first step", "step 1:", "step 1.",
        "再分派", "sub-task", "subtask", "decompose",
    )
    return any(sig in s for sig in re_delegate_signals)
