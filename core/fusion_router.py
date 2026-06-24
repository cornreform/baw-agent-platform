"""BAW — Fusion Router: auto-classify tasks, route to optimal models, parallel inference.

Core idea: different task types need different models. Fusion runs multiple models
in parallel for complex tasks and synthesizes the best result.

Task Type      Best Model(s)                          Fallback
───────        ──────────────────────────              ─────────────
planning       DeepSeek Reasoner, Claude Opus          V4 Pro
coding         Claude Sonnet/Opus, GPT Codex, Qwen Coder  V4 Flash
research       Gemini Flash, V4 Flash                 V4 Flash
creative       MiniMax M3, Claude Sonnet              V4 Flash
audit          V4 Pro, Claude Opus                    V4 Flash
quick-qa       V4 Flash (direct, no fusion)           —
"""

import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

logger = logging.getLogger("baw.fusion_router")

# ── Task classification ───────────────────────────────────────

# Classification patterns — conservative: only classify clear signals
_PLANNING_PATTERNS = [
    r"(?:plan|strategy|roadmap|architecture|design\s+system)",
    r"(?:步驟|計劃|規劃|架構|設計|藍圖)",
    r"(?:multi-step|long.term|milestone|timeline)",
]
_CODING_PATTERNS = [
    r"(?:implement|code|function|class|api|endpoint|refactor|debug|fix|bug|test)",
    r"(?:寫|程式|碼|function|class|api|debug|fix|test)",
    r"(?:git|commit|push|deploy|docker|ci/cd|npm|pip)",
    r"(?:react|vue|django|flask|fastapi|next\.?js|node)",
]
_RESEARCH_PATTERNS = [
    r"(?:research|search|find|compare|analyze|investigate|review|audit)",
    r"(?:search|find|compare|分析|研究|比較|調查|審查)",
    r"(?:what.?is|how.?does|why.?does|difference|vs\.)",
]
_CREATIVE_PATTERNS = [
    r"(?:write|draft|create|design|generate|compose|imagine)",
    r"(?:內容|文案|設計|創作|生成|寫作)",
    r"(?:poem|story|article|blog|email|newsletter|post)",
]
_AUDIT_PATTERNS = [
    r"(?:audit|review|verify|validate|check|inspect|scan)",
    r"(?:審計|檢查|驗證|審查|掃描)",
    r"(?:security|vulnerability|compliance|bug|error|warning)",
]


def classify_task(prompt: str) -> str:
    """Classify task type from prompt using pattern matching.

    Returns one of: 'planning', 'coding', 'research', 'creative', 'audit', 'quick-qa'
    """
    if not prompt or len(prompt) < 20:
        return "quick-qa"

    plow = prompt.lower()

    # Score each category
    scores = {}
    for category, patterns in [
        ("planning", _PLANNING_PATTERNS),
        ("coding", _CODING_PATTERNS),
        ("research", _RESEARCH_PATTERNS),
        ("creative", _CREATIVE_PATTERNS),
        ("audit", _AUDIT_PATTERNS),
    ]:
        score = sum(1 for p in patterns if re.search(p, plow))
        if score > 0:
            scores[category] = score

    if not scores:
        return "quick-qa"

    # Return highest-scoring category
    best = None
    best_score = 0
    for cat, sc in scores.items():
        if sc > best_score:
            best_score = sc
            best = cat
    return best or "quick-qa"


# ── Model routing ─────────────────────────────────────────────

_FUSION_MODELS = {
    "planning": [
        ("deepseek", "deepseek-reasoner"),
        ("openrouter", "anthropic/claude-opus-4.8-fast"),
        ("deepseek", "deepseek-v4-pro"),
    ],
    "coding": [
        ("openrouter", "anthropic/claude-sonnet-4.6"),
        ("openrouter", "qwen/qwen3-coder-plus"),
        ("deepseek", "deepseek-v4-pro"),
    ],
    "research": [
        ("openrouter", "google/gemini-3.1-pro-preview"),
        ("deepseek", "deepseek-v4-flash"),
    ],
    "creative": [
        ("minimax", "MiniMax-M3"),
        ("openrouter", "anthropic/claude-sonnet-4.6"),
    ],
    "audit": [
        ("deepseek", "deepseek-v4-pro"),
        ("openrouter", "anthropic/claude-opus-4.8-fast"),
    ],
    "quick-qa": [
        ("deepseek", "deepseek-v4-flash"),
    ],
}


def route(task_type: str) -> list[tuple[str, str]]:
    """Return list of (provider, model) tuples for the task type."""
    return _FUSION_MODELS.get(task_type, _FUSION_MODELS["quick-qa"])


# ── Parallel execution ────────────────────────────────────────

def _call_single_model(
    provider: str,
    model_id: str,
    messages: list[dict],
    config: dict,
    timeout: int = 30,
) -> Optional[str]:
    """Call a single model and return its response text."""
    try:
        from ..llm import call_llm_with_fallback as _llm
        from ..context import Context as _Ctx

        ctx = _Ctx(system_prompt="You are BAW. Be concise and accurate.", temperature=0.3)
        for m in messages:
            if m.get("role") == "user":
                ctx.add_user(m.get("content", ""))
            elif m.get("role") == "assistant":
                ctx.add_assistant(m.get("content", ""))
            elif m.get("role") == "tool":
                ctx.add_tool_result(m.get("tool_call_id", ""), m.get("name", ""), m.get("content", ""))

        # Temporarily override model for this call
        _override_cfg = dict(config)
        _override_cfg["model"] = {"default": model_id}
        resp = _llm(_override_cfg, ctx.to_openai_messages(), temperature=0.3, max_tokens=4096)
        return (resp.response.content or "").strip() if resp and resp.response else None
    except Exception as e:
        logger.warning(f"[Fusion] {provider}/{model_id} failed: {e}")
        return None


def run_parallel(
    models: list[tuple[str, str]],
    messages: list[dict],
    config: dict,
    timeout: int = 30,
) -> list[dict]:
    """Run multiple models in parallel.

    Returns list of {provider, model, response, elapsed}.
    """
    results = []
    pool = ThreadPoolExecutor(max_workers=len(models))
    futures = {}
    _start = time.time()

    for provider, model_id in models:
        fut = pool.submit(_call_single_model, provider, model_id, messages, config, timeout)
        futures[fut] = {"provider": provider, "model": model_id}

    for fut in as_completed(futures, timeout=timeout + 5):
        meta = futures[fut]
        _t0 = time.time()
        try:
            text = fut.result(timeout=5)
            results.append({
                "provider": meta["provider"],
                "model": meta["model"],
                "response": text or "",
                "elapsed": round(time.time() - _t0, 1),
                "status": "ok" if text else "empty",
            })
        except Exception as e:
            results.append({
                "provider": meta["provider"],
                "model": meta["model"],
                "response": "",
                "elapsed": round(time.time() - _t0, 1),
                "status": f"error: {e}",
            })

    pool.shutdown(wait=False)
    return results


# ── Synthesis ─────────────────────────────────────────────────

def synthesize(responses: list[dict], original_prompt: str, config: dict) -> str:
    """Synthesize multiple model responses into one coherent answer.

    Uses the cheapest model (V4 Flash) as judge. Falls back to concatenation.
    """
    if not responses:
        return ""
    if len(responses) == 1:
        return responses[0].get("response", "")

    ok_results = [r for r in responses if r.get("status") == "ok" and r.get("response")]
    if not ok_results:
        return responses[0].get("response", "")

    if len(ok_results) == 1:
        return ok_results[0]["response"]

    # Build synthesis prompt
    parts = []
    for i, r in enumerate(ok_results):
        _resp = r["response"][:2000]  # cap per model for synthesis
        parts.append(f"<Model {i+1} ({r['model']}):>\n{_resp}")

    synthesis_prompt = (
        f"The user asked:\n{original_prompt}\n\n"
        f"Multiple models responded. Synthesize a single coherent answer "
        f"from the following responses. Be concise. Resolve contradictions. "
        f"Credit the best reasoning from each:\n\n"
        + "\n\n".join(parts)
    )

    try:
        from ..llm import call_llm_with_fallback as _llm
        from ..context import Context as _Ctx

        ctx = _Ctx(
            system_prompt="You are a synthesis judge. Produce ONE coherent answer from multiple model outputs. Be concise.",
            temperature=0.2,
        )
        ctx.add_user(synthesis_prompt)

        _override_cfg = dict(config)
        _override_cfg["model"] = {"default": "deepseek-v4-flash"}
        resp = _llm(_override_cfg, ctx.to_openai_messages(), temperature=0.2, max_tokens=4096)
        return (resp.response.content or "").strip() if resp and resp.response else ""
    except Exception as e:
        logger.warning(f"[Fusion] Synthesis failed: {e}")
        # Fallback: use the longest response (usually most complete)
        ok_results.sort(key=lambda r: len(r.get("response", "")), reverse=True)
        return ok_results[0]["response"]


def should_fuse(prompt: str, mode: str = "auto") -> bool:
    """Decide whether to use fusion (parallel models) for this prompt."""
    if mode == "tight":
        return False  # Focus mode = single model, relentless execution
    if mode == "fusion":
        return True   # Explicitly requested
    if mode == "auto":
        task_type = classify_task(prompt)
        return task_type in ("planning", "coding", "audit")  # Only fuse for heavy tasks
    return False
