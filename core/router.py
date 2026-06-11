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
import re
from typing import Tuple


# ── Cheap regex-based complexity features ──

# Reasoning-depth keywords (each hit +3)
_DEEP_KEYWORDS = (
    r"\b(分析|比較|評估|architect|design|debug|debugging|"
    r"設計|架構|複雜|深入|原因|root cause|trace|"
    r"compare|evaluate|analyze|reason|explain why|"
    r"完整|comprehensive|deep dive|策略|strategy|"
    r"troubleshoot|診斷|diagnose|拆解|reverse.?engineer|"
    r"研究|research|explore|"
    r"trade.?off|權衡|optimi[sz]e|最佳化|"
    r"audit|審計|review)\b",
)
# Multi-step / verification (+3)
_MULTISTEP_KEYWORDS = (
    r"\b(然後|之後|跟住|第一步|step 1|first.*then|"
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

DEFAULT_TIER_PREFERENCES: dict[str, list[str]] = {
    TIER_TRIVIAL: ["step-3.5-flash-2603", "step-3.5-flash", "MiniMax-M2.5"],
    TIER_MODERATE: ["step-3.7-flash", "step-3.5-flash-2603", "MiniMax-M3"],
    TIER_COMPLEX: ["kimi-k2.6", "step-3.7-flash", "MiniMax-M3"],
    TIER_EXPERT: ["kimi-k2.6", "MiniMax-M3", "step-3.7-flash"],
}


def get_tier_preferences(config: dict) -> dict[str, list[str]]:
    """Get tier → model preference list.

    Precedence (highest first):
    1. config['router']['tier_preferences'][<tier>]   ← user override
    2. DEFAULT_TIER_PREFERENCES[<tier>]              ← hardcoded default
    """
    user_prefs = (
        config.get("router", {}).get("tier_preferences", {}) or {}
    )
    merged = {}
    for tier in (TIER_TRIVIAL, TIER_MODERATE, TIER_COMPLEX, TIER_EXPERT):
        merged[tier] = list(user_prefs.get(tier) or DEFAULT_TIER_PREFERENCES.get(tier, []))
    return merged


def pick_model_for_tier(tier: str, config: dict) -> str:
    """Pick the first AVAILABLE model for the tier.

    The tier→model mapping is config-driven. If the user hasn't
    configured anything, falls back to DEFAULT_TIER_PREFERENCES.
    This function makes NO quality judgement — it just picks the
    first model in the preference list that's configured and
    has chat capability.
    """
    available = set()
    for pname, pcfg in config.get("providers", {}).items():
        for m in pcfg.get("models", []):
            mid = m.get("id", "")
            caps = m.get("capabilities", [])
            if "chat" in caps and mid:
                available.add(mid)

    preferences = get_tier_preferences(config)
    for candidate in preferences.get(tier, preferences.get(TIER_MODERATE, [])):
        if candidate in available:
            return candidate
    # Last resort: any chat model
    if available:
        return next(iter(available))
    return "step-3.7-flash"


# ── Decision: inline vs delegate ──

def should_delegate(score: int) -> bool:
    """Return True if this task should go to a sub-agent.

    Inline (model itself uses tools):
      - trivial  (0-3)
      - moderate (4-6)
    Delegated (sub-agent does it):
      - complex  (7-9)
      - expert   (10)
    """
    return score >= 7


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
                 reasoning: str = ""):
        self.score = score
        self.tier = tier
        self.model_id = model_id
        self.delegate = delegate
        self.multi_tier = multi_tier
        self.reasoning = reasoning

    def __repr__(self):
        return (f"RouteDecision(score={self.score}, tier={self.tier!r}, "
                f"model={self.model_id!r}, delegate={self.delegate}, "
                f"multi_tier={self.multi_tier})")


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

    reasons = []
    reasons.append(f"score={score}")
    reasons.append(f"tool_hint={estimated_tool_count}")
    reasons.append(f"ctx={context_tokens}")
    reasons.append(f"tier={tier}→{model_id}")
    reasons.append(f"{'DELEGATE' if delegate else 'INLINE'}")
    if multi_tier:
        reasons.append("MULTI_TIER_CASCADE")

    return RouteDecision(
        score=score,
        tier=tier,
        model_id=model_id,
        delegate=delegate,
        multi_tier=multi_tier,
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
