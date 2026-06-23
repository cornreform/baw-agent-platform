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


def _add_provider(providers: dict, selected: list, name: str, limit: int = 1):
    """Add a provider's models to selected list if configured and chat-capable."""
    if name not in providers:
        return
    models = providers[name].get("models", [])
    for m in models[:limit]:
        caps = m.get("capabilities", [])
        if caps and "chat" not in caps:
            continue
        selected.append({"provider": name, "model": m.get("id", ""), "config": m})


def _auto_select_models(question: str, providers: dict, selected: list) -> None:
    """AUTO mode: judge complexity from question length and pick proportionally."""
    q_len = len(question or "")
    if q_len < 50:
        for p in _CHEAP_PROVIDERS[:2]:
            _add_provider(providers, selected, p)
    elif q_len < 200:
        for p in _CHEAP_PROVIDERS:
            _add_provider(providers, selected, p)
        for p in _MID_PROVIDERS[:1]:
            _add_provider(providers, selected, p)
    else:
        for p in _CHEAP_PROVIDERS:
            _add_provider(providers, selected, p)
        for p in _MID_PROVIDERS:
            _add_provider(providers, selected, p)
        for p in _EXPENSIVE_PROVIDERS:
            _add_provider(providers, selected, p)


def _select_models_for_mode(config: dict, mode: str, question: str) -> list[dict]:
    """Intelligently select which models to query based on mode and task.

    auto:    Judge complexity from question, pick proportionally
    quick:   Only cheapest providers (deepseek, openrouter, stepfun)
    all:     Every configured provider (original behavior)
    deep:    All providers + cross-validation round
    """
    providers = config.get("providers", {})
    selected = []

    if mode == "quick":
        for p in _CHEAP_PROVIDERS:
            _add_provider(providers, selected, p)
        return selected

    if mode == "all":
        for name in providers:
            _add_provider(providers, selected, name, limit=2)
        return selected

    if mode == "deep":
        for name in providers:
            _add_provider(providers, selected, name, limit=2)
        return selected

    # ── AUTO mode: judge complexity from question ──
    _auto_select_models(question, providers, selected)
    return selected


def _build_model_messages(question: str, is_cross_validation: bool) -> list[dict]:
    """Build messages list for a model call with appropriate system prompt."""
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
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]


def _call_model(config: dict, model_id: str, messages: list[dict]) -> str:
    """Call a model and return the response text."""
    from core.llm import get_model, _call_with_timeout as _call

    model = get_model(config, model_id)
    if not model:
        return ""
    response = _call(
        model=model,
        messages=messages,
        tools=None,
        temperature=0.7,
        max_tokens=2048,
    )
    return (response.content if response else "") or "[empty response]"


def _try_model(config: dict, provider_name: str, model_id: str,
               question: str, results: list, lock: threading.Lock,
               is_cross_validation: bool = False) -> None:
    """Try one model, append result to shared results list."""
    if not model_id:
        return

    try:
        messages = _build_model_messages(question, is_cross_validation)
        response = _call_model(config, model_id, messages)

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


def _build_cross_val_question(question: str, ok_results: list[dict]) -> str:
    """Build the validation question from original question and responses to review."""
    val_q = (
        f"Original question: {question}\n\n"
        f"Review the responses below and identify errors, contradictions, or gaps:\n"
    )
    for r in ok_results[:2]:
        val_q += f"\n--- {r['provider']}/{r['model']} ---\n{r['response'][:2000]}\n"
    val_q += "\n\nList: (a) confirmed correct, (b) questionable claims, (c) what's missing."
    return val_q


def _run_cross_val_models(config: dict, val_question: str, available: list) -> list[dict]:
    """Run cross-validation models on the validation question."""
    cross_lock = threading.Lock()
    cross_results = []
    threads = []

    for p in available[:1]:
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

    return cross_results


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
        available = list(providers_used)

    val_question = _build_cross_val_question(question, ok_results)
    cross_results = _run_cross_val_models(config, val_question, available)

    return initial_results + cross_results


def _build_responses_text(raw_results: list[dict]) -> tuple[list[str], bool]:
    """Build response text blocks and check if cross-validation was performed."""
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
    has_cross_val = any(r.get("round") == "cross_val" for r in raw_results if r["status"] == "ok")
    return responses_text, has_cross_val


def _build_synthesis_prompt(question: str, responses_text: list[str], has_cross_val: bool) -> str:
    """Build the full synthesis prompt for the judge model."""
    prompt = (
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
        prompt += (
            "A cross-validation round was also run. The [VALIDATION] entries "
            "reviewed other responses for errors. Incorporate their findings.\n\n"
        )
    prompt += (
        f"Original question: {question}\n\n"
        f"--- Model Responses ---\n\n"
        f"{chr(10).join(responses_text)}"
    )
    return prompt


def _call_judge(config: dict, synthesis_prompt: str) -> str | None:
    """Call the judge/default model to synthesize responses."""
    from core.llm import get_model, _call_with_timeout

    model_id = config.get("model", {}).get("default", "deepseek-v4-flash")
    model = get_model(config, model_id)

    judge_messages = [
        {"role": "system", "content": "You are a neutral synthesis judge. Respond in the user's language."},
        {"role": "user", "content": synthesis_prompt},
    ]

    judge_response = _call_with_timeout(
        model=model,
        messages=judge_messages,
        tools=None,
        temperature=0.3,
        max_tokens=4096,
    )
    return judge_response.content if judge_response else None


def _build_fallback_synthesis(raw_results: list[dict]) -> str:
    """Build a fallback synthesis when the judge model call fails."""
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


def _synthesize(config: dict, question: str, raw_results: list[dict]) -> str:
    """Synthesize raw results into structured analysis using judge model."""
    responses_text, has_cross_val = _build_responses_text(raw_results)

    if not responses_text:
        return "[Fusion] No responses collected from any provider."

    synthesis_prompt = _build_synthesis_prompt(question, responses_text, has_cross_val)

    try:
        judge_response = _call_judge(config, synthesis_prompt)
        return judge_response or "[Fusion] Judge synthesis failed."
    except Exception as e:
        logger.error(f"[Fusion] Judge synthesis failed: {e}")
        return _build_fallback_synthesis(raw_results)


def _run_models_parallel(config: dict, selected_models: list[dict],
                         question: str) -> list[dict]:
    """Run all selected models in parallel threads."""
    results = []
    lock = threading.Lock()
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

    return results


def _build_model_info(results: list[dict], mode: str) -> str:
    """Build the model info footer string appended to synthesis output."""
    status_count = sum(1 for r in results if r["status"] == "ok")
    cross_count = sum(1 for r in results if r.get("round") == "cross_val" and r["status"] == "ok")
    total = len(results)
    model_list = ", ".join(f"{r['provider']}/{r['model']}" for r in results[:6])
    info = (
        f"\n\n---\n[FUSION] Mode: {mode} | "
        f"{status_count}/{total} models OK"
        + (f" | {cross_count} cross-validation" if cross_count else "")
        + f" | Models: {model_list}"
    )
    return info


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
    results = _run_models_parallel(config, selected_models, question)

    # Cross-validation for deep/auto complex tasks
    results = _cross_validate(config, question, results, mode)

    # Synthesize
    synthesis = _synthesize(config, question, results)

    # Append model info
    model_info = _build_model_info(results, mode)
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
