"""BAW built-in: fusion_analyze — multi-model deliberation.

Queries ALL configured LLM providers in parallel, collects responses,
then synthesizes a structured analysis: consensus, contradictions,
unique insights, and blind spots.

Inspired by OpenRouter Fusion — but BAW-native, using all your own API keys.

Usage:
  User: "用fusion分析呢個問題"
  BAW:  Calls fusion_analyze(question="...") → runs all providers → returns analysis
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger("baw.fusion_analyze")

# ── How many chat-capable models to try per provider ──
_MAX_MODELS_PER_PROVIDER = 2

# ── Timeout per model call (seconds) ──
_PER_MODEL_TIMEOUT = 30


def _try_model(config: dict, provider_name: str, model_cfg: dict,
               question: str, results: list, lock: threading.Lock) -> None:
    """Try one model, append result to shared results list."""
    model_id = model_cfg.get("id", "")
    if not model_id:
        return

    # Only chat-capable models
    caps = model_cfg.get("capabilities", [])
    if caps and "chat" not in caps:
        return

    try:
        from core.llm import get_model

        model = get_model(config, model_id)
        if not model:
            return

        # Build messages
        system_prompt = (
            "You are participating in a multi-model deliberation. "
            "Answer the user's question clearly and concisely. "
            "Focus on your unique expertise. Be honest about uncertainty."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]

        # Call the model
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
            })
        logger.info(f"[Fusion] {provider_name}/{model_id}: OK")

    except Exception as e:
        with lock:
            results.append({
                "provider": provider_name,
                "model": model_id,
                "response": None,
                "status": "error",
                "error": str(e)[:200],
            })
        logger.debug(f"[Fusion] {provider_name}/{model_id}: {e}")


def _synthesize(config: dict, question: str, raw_results: list[dict]) -> str:
    """Synthesize raw results into structured analysis using judge model."""
    # Build synthesis prompt
    responses_text = []
    for r in raw_results:
        if r["status"] == "ok" and r["response"]:
            responses_text.append(
                f"=== {r['provider']}/{r['model']} ===\n{r['response'][:3000]}"
            )
        elif r["status"] == "error":
            responses_text.append(
                f"=== {r['provider']}/{r['model']} ===\n[ERROR: {r['error']}]"
            )

    if not responses_text:
        return "[Fusion] No responses collected from any provider."

    synthesis_prompt = (
        "You are a judge in a multi-model deliberation. "
        "Below are responses from multiple AI models to the same question. "
        "Analyze them and produce a structured report covering:\n\n"
        "1. **CONSENSUS** — Points where all/most models agree\n"
        "2. **CONTRADICTIONS** — Points where models disagree\n"
        "3. **UNIQUE INSIGHTS** — Points raised by only one model\n"
        "4. **BLIND SPOTS** — Important aspects that NO model addressed\n"
        "5. **SYNTHESIS** — Your consolidated answer, resolving contradictions\n\n"
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
        # Fallback: return raw results
        fallback = ["[FUSION] Judge synthesis failed — raw responses:"]
        for r in raw_results:
            status_tag = "[OK]" if r["status"] == "ok" else "[FAIL]"
            fallback.append(f"\n### {status_tag} {r['provider']}/{r['model']}")
            if r["response"]:
                fallback.append(r["response"][:2000])
            else:
                fallback.append(f"Error: {r['error']}")
        return "\n".join(fallback)


def fusion_analyze(question: str) -> str:
    """Run multi-model deliberation on a question using ALL configured providers.

    Args:
        question: The question or topic to analyze.

    Returns:
        Structured analysis with consensus, contradictions, insights, and synthesis.
    """
    if not question or not question.strip():
        return "[Fusion] Error: question is required."

    # Load config
    from core.config import load_config

    config = load_config(reload=True)
    providers = config.get("providers", {})
    if not providers:
        return "[Fusion] No LLM providers configured."

    # Collect models to query (up to _MAX_MODELS_PER_PROVIDER per provider)
    targets: list[tuple[str, dict]] = []
    for pname, pcfg in providers.items():
        models = pcfg.get("models", [])
        count = 0
        for m in models:
            caps = m.get("capabilities", [])
            if caps and "chat" not in caps:
                continue
            targets.append((pname, m))
            count += 1
            if count >= _MAX_MODELS_PER_PROVIDER:
                break

    if not targets:
        return "[Fusion] No chat-capable models found in any provider."

    # Parallel query all targets
    results: list[dict] = []
    lock = threading.Lock()
    start = time.time()

    with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as executor:
        futures = []
        for pname, mcfg in targets:
            futures.append(
                executor.submit(
                    _try_model, config, pname, mcfg, question, results, lock
                )
            )
        for future in as_completed(futures):
            pass  # results collected via _try_model callback

    elapsed = time.time() - start

    # Sort results: successful first
    results.sort(key=lambda r: (0 if r["status"] == "ok" else 1, r["provider"]))

    # Build raw report
    raw_lines = [
        f"[FUSION ANALYSIS]",
        f"  Question: {question[:100]}{'...' if len(question) > 100 else ''}",
        f"  Models queried: {len(targets)} across {len(providers)} providers",
        f"  Successful: {sum(1 for r in results if r['status'] == 'ok')}",
        f"  Failed: {sum(1 for r in results if r['status'] == 'error')}",
        f"  Time: {elapsed:.1f}s",
        "",
    ]
    for r in results:
        icon = "[OK]" if r["status"] == "ok" else "[FAIL]"
        raw_lines.append(f"  {icon} {r['provider']}/{r['model']}")
        if r["error"]:
            raw_lines.append(f"     Error: {r['error']}")

    # Synthesize
    synthesis = _synthesize(config, question, results)

    # Combine
    output = "\n".join(raw_lines) + "\n\n" + synthesis
    return output


# ── Tool Definition ────────────────────────────────────────────
def _handler(args: dict) -> str:
    question = args.get("question", "").strip()
    return fusion_analyze(question)


TOOL_DEF = {
    "name": "fusion_analyze",
    "description": (
        "[FUSION MODE] Run multi-model deliberation across ALL configured LLM "
        "providers. Queries every chat-capable model in parallel, collects all "
        "responses, then synthesizes a structured analysis: consensus, "
        "contradictions, unique insights, blind spots, and a final synthesis. "
        "Use when you need diverse perspectives on a complex question."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "The question or topic for multi-model deliberation. "
                    "Be specific to get the best analysis."
                ),
            },
        },
        "required": ["question"],
    },
    "risk_level": "low",
}
