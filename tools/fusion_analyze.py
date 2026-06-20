"""BAW built-in: fusion_analyze — multi-model deliberation with smart model selection.

Queries multiple LLM providers in parallel, with mode-based model selection.
In AUTO mode: intelligently selects which models to fuse based on task complexity.
Supports cross-validation: cheap models validate each other's conclusions.

Inspired by arXiv:2605.22502 — compiling agentic workflows into LLM weights.
Multiple cheap models working together can match frontier quality at 128-462x less cost.
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger("baw.fusion_analyze")

# ── Provider cost tiers (estimated per-token relative cost) ──
_CHEAP_PROVIDERS = ["deepseek", "openrouter", "stepfun"]
_MID_PROVIDERS = ["minimax", "moonshot"]
_EXPENSIVE_PROVIDERS = ["xai"]

# ── Timeout per model call (seconds) ──
_PER_MODEL_TIMEOUT = 30


def _select_models_for_mode(config: dict, mode: str, question: str) -> list[dict]:
    """Intelligently select which models to query based on mode and task.

    auto:    Judge complexity from question, pick proportionally
    quick:   Only cheapest providers (deepseek, openrouter, stepfun)
    all:     Every configured provider (original behavior)
    deep:    All providers + cross-validation round
    """
    providers = config.get("providers", {})
    selected = []

    def add_provider(name: str, limit: int = 1):
        if name not in providers:
            return
        models = providers[name].get("models", [])
        for m in models[:limit]:
            caps = m.get("capabilities", [])
            if caps and "chat" not in caps:
                continue
            selected.append({"provider": name, "model": m.get("id", ""), "config": m})

    if mode == "quick":
        for p in _CHEAP_PROVIDERS:
            add_provider(p)
        return selected

    if mode == "all":
        for name in providers:
            add_provider(name, limit=2)
        return selected

    if mode == "deep":
        for name in providers:
            add_provider(name, limit=2)
        return selected

    # ── AUTO mode: judge complexity from question ──
    q_len = len(question or "")
    if q_len < 50:
        # Very short question → quick fusion: 2 cheapest
        for p in _CHEAP_PROVIDERS[:2]:
            add_provider(p)
    elif q_len < 200:
        # Medium question → cheap + mid providers
        for p in _CHEAP_PROVIDERS:
            add_provider(p)
        for p in _MID_PROVIDERS[:1]:
            add_provider(p)
    else:
        # Complex question → all tiers
        for p in _CHEAP_PROVIDERS:
            add_provider(p)
        for p in _MID_PROVIDERS:
            add_provider(p)
        for p in _EXPENSIVE_PROVIDERS:
            add_provider(p)

    return selected


def _try_model(config: dict, provider_name: str, model_id: str,
               question: str, results: list, lock: threading.Lock,
               is_cross_validation: bool = False) -> None:
    """Try one model, append result to shared results list."""
    if not model_id:
        return

    try:
        from core.llm import get_model

        model = get_model(config, model_id)
        if not model:
            return

        if is_cross_validation:
            system_prompt = (
                "You are validating another AI's response. "
                "Check for factual errors, logical flaws, and omissions. "
                "Be critical but fair. List: (a) confirmed correct, "
                "(b) questionable, (c) missing important points."
            )
        else:
            system_prompt = (
                "You are participating in a multi-model deliberation. "
                "Answer the user's question clearly and concisely. "
                "Focus on what you know best. Be honest about uncertainty."
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]

        from core.llm import _call_with_timeout as _call

        response = _call(
            model=model,
            messages=messages,
            tools=None,
            temperature=0.7,
            max_tokens=2048,
        )

        with lock:
            results.append({
                "provider": provider_name,
                "model": model_id,
                "response": response[:8000] if response else "[empty response]",
                "status": "ok",
                "error": None,
                "round": "cross_val" if is_cross_validation else "initial",
            })
        logger.info(f"[Fusion] {provider_name}/{model_id}: OK{' (cross-val)' if is_cross_validation else ''}")

    except Exception as e:
        with lock:
            results.append({
                "provider": provider_name,
                "model": model_id,
                "response": None,
                "status": "error",
                "error": str(e)[:200],
                "round": "cross_val" if is_cross_validation else "initial",
            })
        logger.debug(f"[Fusion] {provider_name}/{model_id}: {e}")


def _cross_validate(config: dict, question: str, initial_results: list[dict],
                    mode: str) -> list[dict]:
    """Run cross-validation round for deep/auto complex modes.

    Takes the most divergent responses and has another model validate them.
    This implements: '幾重嘅Model去互相認證就算用平啲嘅Model都可以達到好高嘅效果'
    """
    if mode not in ("deep", "auto"):
        return initial_results
    if len(initial_results) < 2:
        return initial_results

    # Only cross-validate if task is complex enough
    if len(question or "") < 100:
        return initial_results

    # Pick the most different responses for cross-validation
    ok_results = [r for r in initial_results if r["status"] == "ok" and r["response"]]
    if len(ok_results) < 2:
        return initial_results

    # Use a provider NOT in the original set for validation
    providers_used = {r["provider"] for r in ok_results}
    available = [p for p in _CHEAP_PROVIDERS + _MID_PROVIDERS if p not in providers_used]
    if not available:
        # Fall back to first provider
        available = list(providers_used)

    cross_lock = threading.Lock()
    cross_results = []
    threads = []
    val_question = (
        f"Original question: {question}\n\n"
        f"Review the responses below and identify errors, contradictions, or gaps:\n"
    )
    for r in ok_results[:2]:
        val_question += f"\n--- {r['provider']}/{r['model']} ---\n{r['response'][:2000]}\n"
    val_question += "\n\nList: (a) confirmed correct, (b) questionable claims, (c) what's missing."

    for p in available[:1]:  # One cross-validator is enough
        if p not in config.get("providers", {}):
            continue
        models = config["providers"][p].get("models", [])
        for m in models[:1]:
            mid = m.get("id", "")
            if not mid:
                continue
            t = threading.Thread(
                target=_try_model,
                args=(config, p, mid, val_question, cross_results, cross_lock),
                kwargs={"is_cross_validation": True},
            )
            t.start()
            threads.append(t)

    for t in threads:
        t.join(timeout=_PER_MODEL_TIMEOUT)

    return initial_results + cross_results


def _synthesize(config: dict, question: str, raw_results: list[dict]) -> str:
    """Synthesize raw results into structured analysis using judge model."""
    responses_text = []
    for r in raw_results:
        if r["status"] == "ok" and r["response"]:
            tag = "[VALIDATION]" if r.get("round") == "cross_val" else "[RESPONSE]"
            responses_text.append(
                f"{tag} {r['provider']}/{r['model']}:\n{r['response'][:3000]}"
            )
        elif r["status"] == "error":
            responses_text.append(
                f"[ERROR] {r['provider']}/{r['model']}: {r['error']}"
            )

    if not responses_text:
        return "[Fusion] No responses collected from any provider."

    has_cross_val = any(r.get("round") == "cross_val" for r in raw_results if r["status"] == "ok")

    synthesis_prompt = (
        "You are a judge in a multi-model deliberation. "
        "Below are responses from multiple AI models to the same question. "
        "Analyze them and produce a structured report covering:\n\n"
        "1. **CONSENSUS** — Points where all/most models agree\n"
        "2. **CONTRADICTIONS** — Points where models disagree\n"
        "3. **UNIQUE INSIGHTS** — Points raised by only one model\n"
        "4. **BLIND SPOTS** — Important aspects that NO model addressed\n"
        "5. **SYNTHESIS** — Your consolidated answer, resolving contradictions\n\n"
    )
    if has_cross_val:
        synthesis_prompt += (
            "A cross-validation round was also run. The [VALIDATION] entries "
            "reviewed other responses for errors. Incorporate their findings.\n\n"
        )
    synthesis_prompt += (
        f"Original question: {question}\n\n"
        f"--- Model Responses ---\n\n"
        f"{chr(10).join(responses_text)}"
    )

    # Use default model as judge
    try:
        from core.llm import get_model, _call_with_timeout

        model_id = config.get("model", {}).get("default", "deepseek-v4-flash")
        model = get_model(config, model_id)

        judge_messages = [
            {"role": "system", "content": "You are a neutral synthesis judge. Respond in the user's language."},
            {"role": "user", "content": synthesis_prompt},
        ]

        judge_response = _call(
            model=model,
            messages=judge_messages,
            tools=None,
            temperature=0.3,
            max_tokens=4096,
        )

        return judge_response or "[Fusion] Judge synthesis failed."

    except Exception as e:
        logger.error(f"[Fusion] Judge synthesis failed: {e}")
        fallback = ["[FUSION] Judge synthesis failed — raw responses:"]
        for r in raw_results:
            status_tag = "[OK]" if r["status"] == "ok" else "[FAIL]"
            tag = " [VAL]" if r.get("round") == "cross_val" else ""
            fallback.append(f"\n### {status_tag}{tag} {r['provider']}/{r['model']}")
            if r["response"]:
                fallback.append(r["response"][:2000])
            else:
                fallback.append(f"Error: {r['error']}")
        return "\n".join(fallback)


def fusion_analyze(question: str, mode: str = "auto") -> str:
    """Run multi-model deliberation on a question.

    Args:
        question: The question or topic to analyze.
        mode: auto (default, intelligent selection), quick (cheapest),
              all (every provider), deep (all + cross-validation).

    Returns:
        Structured analysis with consensus, contradictions, insights, and synthesis.
    """
    if not question or not question.strip():
        return "[Fusion] Error: question is required."

    valid_modes = ("auto", "quick", "all", "deep")
    if mode not in valid_modes:
        mode = "auto"

    # Load config
    from core.config import load_config

    config = load_config(reload=True)
    providers = config.get("providers", {})
    if not providers:
        return "[Fusion] No LLM providers configured."

    # Smart model selection
    selected_models = _select_models_for_mode(config, mode, question)
    if not selected_models:
        return "[Fusion] No suitable models found for this mode."

    # Run all selected models in parallel
    results = []
    lock = threading.Lock()
    max_workers = min(len(selected_models), 8)
    threads = []

    for sel in selected_models:
        t = threading.Thread(
            target=_try_model,
            args=(config, sel["provider"], sel["model"], question, results, lock),
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join(timeout=_PER_MODEL_TIMEOUT)

    # Cross-validation for deep/auto complex tasks
    results = _cross_validate(config, question, results, mode)

    # Synthesize
    synthesis = _synthesize(config, question, results)

    # Append model info
    status_count = sum(1 for r in results if r["status"] == "ok")
    cross_count = sum(1 for r in results if r.get("round") == "cross_val" and r["status"] == "ok")
    total = len(results)
    model_list = ", ".join(f"{r['provider']}/{r['model']}" for r in results[:6])
    model_info = (
        f"\n\n---\n[FUSION] Mode: {mode} | "
        f"{status_count}/{total} models OK"
        + (f" | {cross_count} cross-validation" if cross_count else "")
        + f" | Models: {model_list}"
    )

    return synthesis + model_info


# ── Tool definition ──
TOOL_DEF = {
    "name": "fusion_analyze",
    "description": (
        "Multi-model deliberation: queries multiple LLM providers in parallel, "
        "synthesizes consensus, contradictions, and insights. "
        "Mode 'auto' (default) intelligently selects models based on task complexity. "
        "Mode 'deep' adds cross-validation: cheap models validate each other's conclusions "
        "to match frontier quality at fraction of cost. "
        "Mode 'quick' uses only cheapest providers. Mode 'all' queries every provider."
    ),
    "handler": fusion_analyze,
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question or topic to analyze with multiple models.",
            },
            "mode": {
                "type": "string",
                "enum": ["auto", "quick", "all", "deep"],
                "description": (
                    "auto=intelligent model selection based on complexity, "
                    "quick=cheapest providers only, "
                    "all=every configured provider, "
                    "deep=all providers + cross-validation round"
                ),
                "default": "auto",
            },
        },
        "required": ["question"],
    },
    "risk_level": "low",
}
