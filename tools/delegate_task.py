from __future__ import annotations
"""BAW built-in: delegate a task to a sub-agent (MiniMax executor).

Sub-agent runs in an isolated context — no parent conversation history,
no parent memories. It receives only the goal + optional context.

Use this for:
- Parallel subtasks (e.g. "search the web while I process local files")
- Complex research tasks that benefit from independent reasoning
- Mechanical sub-tasks that don't need the full BAW court/plan pipeline
"""
import json
import sys
from pathlib import Path

# ── Lazy imports (only when tool is called) ──
_BAW_ROOT = str(Path(__file__).resolve().parent.parent)  # ~/baw/


def _save_tool_registry(_core_tools_mod):
    """Snapshot the global tool registry and return (registry_ref, saved_copy).
    Returns (registry, saved_tools) where registry is the live dict reference.
    """
    _registry = None
    for _name in ("_tools", "_TOOLS"):
        _candidate = getattr(_core_tools_mod, _name, None)
        if _candidate is not None and isinstance(_candidate, dict):
            _registry = _candidate
            break
    if _registry is None:
        _registry = {}
    _saved_tools = dict(_registry)
    return _registry, _saved_tools


def _load_tool_def(name: str) -> dict:
    """Dynamically import a tool module and return its TOOL_DEF."""
    import importlib.util as _iu
    p = Path(_BAW_ROOT) / "tools" / f"{name}.py"
    s = _iu.spec_from_file_location(f"_tk_{name}", str(p))
    if s is None or s.loader is None:
        raise ImportError(f"Cannot load tool '{name}'")
    m = _iu.module_from_spec(s)
    s.loader.exec_module(m)
    return m.TOOL_DEF


def _ensure_sys_path():
    """Insert BAW_ROOT into sys.path if not already present."""
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)
        sys.path.insert(0, str(Path(_BAW_ROOT).parent))


def _register_core_tools():
    """Register the standard set of sub-agent tools. Returns saved_tools dict."""
    _ensure_sys_path()
    from core.tools import register as _reg, clear as _clear
    import core.tools as _core_tools_mod

    _registry, _saved_tools = _save_tool_registry(_core_tools_mod)
    try:
        _clear()
        _CORE_TOOL_NAMES = [
            "bash", "read_file", "write_file", "web_search", "web_extract",
            "search_files", "patch", "memory", "todo", "vision", "tts",
            "image_generate", "install",
        ]
        for _tn in _CORE_TOOL_NAMES:
            _reg(**_load_tool_def(_tn))
    except BaseException:
        _registry.clear()
        _registry.update(_saved_tools)
        raise

    _snapshot_key = f"_pending_restore_{id(_saved_tools)}"
    setattr(_core_tools_mod, _snapshot_key, _saved_tools)
    return _saved_tools


def _import_baw():
    """Import BAW modules once into a module-level cache."""
    if not hasattr(_import_baw, "_done"):
        _ensure_sys_path()
    _register_core_tools()
    _import_baw._done = True
    return True  # tools registered

def _resolve_executor_model(cfg: dict, goal: str = "") -> str:
    """Resolve which model to use for a delegated task.

    Natural language first: model selection is driven by the parent LLM's
    understanding of the task, not keyword/regex rules. Falls back to
    configured executor model then default.
    """
    model_cfg = cfg.get("model", {})

    return (
        cfg.get("executor", {}).get("model") or
        model_cfg.get("fallback") or
        model_cfg.get("default", "deepseek-v4-flash")
    )


def _get_minimax_config(goal: str = "", model_override: str = "") -> dict:
    """Load config and resolve the executor model (per-task routing support).
    Falls back gracefully if resolved model is not in providers list.

    Args:
        goal: Natural language task description — model resolved by fallback chain, not keyword matching.
        model_override: If non-empty, forces this model (P0-1 fix — respects
                        caller/router decision instead of silently dropping it).

    P1-3 (Opus 4.8 audit): now uses core.config.load_config so the merged
    view (repo + ~/.baw) matches what core.llm and cli chat see. Previously
    this function only read ~/.baw/config.yaml, which could cause P0-1's
    model_override to be rejected by the existence check if the model was
    only declared in repo/config.yaml.
    """
    # P1-3: unified loader, no manual yaml/env plumbing here.
    from core.config import load_config as _unified_load, model_exists
    import copy
    cfg = copy.deepcopy(_unified_load())  # deep-copy: we mutate cfg["model"] below; must not poison the shared cache.

    model_cfg = cfg.get("model", {})

    # P0-1: explicit override wins over everything (router decision).
    if model_override:
        executor_model = model_override
    else:
        executor_model = _resolve_executor_model(cfg, goal)

    # P1-3: existence check now uses the unified helper, so the same set
    # of providers is consulted regardless of which file the model was
    # declared in.
    if not model_exists(cfg, executor_model):
        # Fall back to default model
        executor_model = model_cfg.get("default", "deepseek-v4-flash")

    cfg["model"] = {
        "default": executor_model,
        "fallback": model_cfg.get("fallback", executor_model),
    }

    return cfg


def _build_model_pool(goal: str, model_id: str, base_config: dict) -> list[str]:
    """Build the ordered model pool for auto-fallback attempts."""
    _initial_model = base_config.get("model", {}).get("default", "deepseek-v4-flash")
    _model_pool = [_initial_model]
    try:
        from core.router import pick_model_for_tier, score_complexity, tier_of
        _score = score_complexity(goal)
        _tier = tier_of(_score)
        _prefs = base_config.get("router", {}).get("tier_preferences", {})
        if not _prefs.get(_tier):
            from core.router import get_tier_preferences
            _prefs = get_tier_preferences(base_config)
        for _m in _prefs.get(_tier, []):
            if _m not in _model_pool:
                _model_pool.append(_m)
    except Exception:
        pass  # Best-effort
    return _model_pool[:3]  # at most 3 attempts


def _build_executor_system_prompt(fallback_model: str) -> str:
    """Build the system prompt for the sub-agent executor."""
    return (
        "You are an EXECUTOR. Do the task. Rules:\n"
        "1) First response MUST call a tool. NO planning text before tool calls.\n"
        "2) Read file -> write file -> verify. Do ALL steps.\n"
        "3) curl NOT available. python3 -c 'import urllib.request...' (always works).\n"
        "4) NEVER describe what you will do — JUST DO IT.\n"
        "5) Only report after every tool call returned.\n"
        "6) If stuck, try a different approach, don't re-describe.\n"
        "7) No questions. No confirmations. No explanations mid-task. Execute silently.\n"
        "8) NEVER fabricate model names or test results. If API fails, report the error.\n"
        "9) After modifying config, read it back to confirm the change was applied. Report before/after.\n"
        "10) If asked to send files (MEDIA:), the response MUST include 'MEDIA:/path/to/file' tag for each file. If the file doesn't exist, report this honestly — do NOT pretend to send.\n"
        "11) CRITICAL — TOOL CALLING METHOD: You have direct access to tools via FUNCTION CALLING (JSON). "
        "Call read_file, write_file, bash, web_search etc. directly as tool calls — "
        "NOT by wrapping them in Python code or bash commands. "
        "Do NOT write: bash('python3 -c \"read_file(...)\"') — that fails because read_file is not a Python builtin. "
        "Instead, call: read_file(path='/path/to/file') directly as a tool call.\n"
        "12) If you finish without making ANY tool calls, you MUST return the literal string '0 tool calls' at the start of your reply. This signals failure to the orchestrator.\n"
        f"13) Model: {fallback_model}. Use this model for all LLM calls."
    )


def _build_executor_prompt(goal: str, context: str) -> str:
    """Build the user prompt for the sub-agent with explicit checklist instructions."""
    prompt_parts = [f"[DELEGATED TASK]\nGoal: {goal}"]
    if context:
        prompt_parts.append(f"\nContext:\n{context}")
    prompt_parts.append(
        "\n\n"
        "REQUIRED: Before you respond, work out your checklist in 3-5 bullets:\n"
        "  1. What specific tool calls are needed to accomplish this goal?\n"
        "  2. What is the expected output of each call?\n"
        "  3. What is the final success criterion (file exists? MEDIA: tag present? config updated?)?\n\n"
        "Then EXECUTE the checklist. Do NOT respond with the checklist alone —\n"
        "you MUST make tool calls before sending any text reply.\n\n"
        "If a step fails, try a different approach. NEVER return a plan without\n"
        "having actually invoked tools. NEVER mark a step as 'done' unless you\n"
        "have evidence (file exists, API returned 200, config read back).\n\n"
        "Final reply format: state what you DID (verb + target + result),\n"
        "not what you INTEND to do."
    )
    return "\n".join(prompt_parts)


def _call_llm_with_shutdown_retry(config: dict, ctx: 'Context', openai_tools: list) -> 'Any':
    """Call LLM with retry on spurious shutdown signals (P1-5 fix)."""
    from core.llm import call_llm_with_fallback, is_shutdown_requested
    _shutdown_retries = 0
    _re_msg = ""
    while _shutdown_retries < 2:
        try:
            return call_llm_with_fallback(
                config,
                ctx.to_openai_messages(),
                tools=openai_tools,
                temperature=0.5,
            )
        except RuntimeError as _re:
            _re_msg = str(_re)
            if "Shutdown" in _re_msg and is_shutdown_requested():
                import logging as _lg
                _lg.getLogger(__name__).warning(
                    f"[delegate_task] Shutdown flag detected — clearing and retrying "
                    f"(attempt {_shutdown_retries + 1}/2)"
                )
                import core.llm as _llm_mod
                _llm_mod._shutdown_requested = False
                _shutdown_retries += 1
                continue
            raise
    raise RuntimeError(f"Sub-agent LLM call failed after shutdown retries: {_re_msg}")


def _check_promise_violation(resp_text: str, sub_promises: list[str],
                             openai_tools: list, iteration: int) -> str | None:
    """Check if the sub-agent made promises without calling tools.
    Returns a repropmpt string if violation detected, None otherwise."""
    resp_lower = resp_text.lower()
    promise_patterns = [
        ("i will ", "I will"), ("i'll ", "I'll"),
        ("let me ", "Let me"),
        ("下一步", "下一步"), ("會去", "會去"),
        ("我會", "我會"), ("我會先", "我會先"),
        ("go do", "go do"), ("going to", "going to"),
        ("check and", "check and"), ("look into", "look into"),
        ("investigate", "investigate"), ("find out", "find out"),
        ("我而家", "我而家"), ("跟住", "跟住"),
        ("之後會", "之後會"), ("先檢查", "先檢查"),
    ]
    new_subs = []
    for p_lower, p_label in promise_patterns:
        if p_lower in resp_lower:
            for sent in resp_text.split("。"):
                if p_lower in sent.lower():
                    new_subs.append(sent.strip()[:120])
                    break
    if new_subs:
        sub_list = "\n".join(f"  ✗ \"{p}\"" for p in new_subs[:3])
        repropmpt = (
            f"[PROMISE BROKEN] You said:\n{sub_list}\n\n"
            f"ZERO tools called. You MUST call a tool NOW.\n"
            f"No text. Only tool calls."
        )
        if iteration >= 3:
            tool_names = [t["function"]["name"] for t in openai_tools]
            repropmpt += (
                "\n\nAvailable tools:\n"
                + "\n".join(f"  - {n}" for n in tool_names)
                + "\n\nPick ONE tool matching your promise. Call it NOW."
            )
        return repropmpt

    # Check if previous promises were never kept
    if sub_promises:
        old_list = "\n".join(f"  ⏳ \"{p}\"" for p in sub_promises[-3:])
        return (
            f"[PROMISE AUDIT] Unfulfilled:\n{old_list}\n"
            "Call a tool NOW or the task FAILS."
        )
    return None


def _verify_execution_step(goal: str, name: str, args: dict, result: str,
                           config: dict, verify_retries: int, max_retries: int) -> str | None:
    """Run verify_step on a tool result. Returns a retry prompt or None."""
    if verify_retries >= max_retries:
        return None
    try:
        from core.verifier import verify_step
        verdict = verify_step(
            goal=goal, tool_name=name, tool_args=args,
            tool_result=str(result), config=config,
        )
        score = int(verdict.get("score", 7))
        if score < 7:
            return (
                f"[Verifier] step scored {score}/10, "
                f"reason: {verdict.get('reason', '')[:200]}. "
                f"Try a different approach for this step."
            )
    except Exception as _ve:
        import logging
        logging.getLogger(__name__).warning(
            f"[delegate_task] verify_step failed silently: "
            f"{type(_ve).__name__}: {_ve}"
        )
    return None


def _restore_tool_registry() -> None:
    """Restore the parent agent's tool registry after sub-agent completes."""
    try:
        import core.tools as _ct
        _restored = False
        for _attr in list(vars(_ct).keys()):
            if _attr.startswith("_pending_restore_"):
                _pending = getattr(_ct, _attr, None)
                if _pending is not None:
                    _registry = None
                    for _name in ("_tools", "_TOOLS"):
                        _candidate = getattr(_ct, _name, None)
                        if _candidate is not None and isinstance(_candidate, dict):
                            _registry = _candidate
                            break
                    if _registry is not None:
                        _registry.clear()
                        _registry.update(_pending)
                    delattr(_ct, _attr)
                    _restored = True
                    break
        if not _restored:
            _pending = getattr(_ct, "_pending_restore", None)
            if _pending is not None:
                _registry = None
                for _name in ("_tools", "_TOOLS"):
                    _candidate = getattr(_ct, _name, None)
                    if _candidate is not None and isinstance(_candidate, dict):
                        _registry = _candidate
                        break
                if _registry is not None:
                    _registry.clear()
                    _registry.update(_pending)
                delattr(_ct, "_pending_restore")
    except Exception:
        pass


def _detect_execution_failure(result_text: str, tool_calls_made: int) -> bool:
    """Detect if the sub-agent failed despite producing text output."""
    failure_keywords = [
        "failed", "error", "無法", "失敗", "placeholder",
        "not found", "not installed", "cannot", "unable",
        "冇實際執行", "只係講", "迷失方向", "唔 work",
        "冇做到", "未有", "sub-agent",
    ]
    result_lower = result_text.lower()
    is_failure = any(kw in result_lower for kw in failure_keywords) and tool_calls_made == 0
    if tool_calls_made == 0 and len(result_text) > 50:
        is_failure = True
    return is_failure


def _call_fusion_judge(goal: str, result: str, judge_model_id: str) -> str:
    """Call the judge LLM for fusion verification. Returns response text."""
    from core.llm import call_llm_with_fallback
    from core.context import Context

    judge_config = _get_minimax_config(goal, model_override=judge_model_id)
    judge_ctx = Context(
        system_prompt=(
            "You are a VERIFIER. Your job: check if the sub-agent actually "
            "DID the work (called tools, created files, modified config). "
            "Score 1-10. Be strict but fair: accept alternative approaches "
            "(e.g. wrote to /app/ if /tmp/ was blocked). "
            "Score 8+ if goal was achieved. Score 4-7 if partially done. "
            "Score 1-3 if no real action taken."
        )
    )
    judge_ctx.add_user(
        f"Goal: {goal}\n\n"
        f"Result:\n{result}\n\n"
        f"Score (1-10) and brief reason. Format: SCORE: <N>\\nREASON: <text>"
    )
    judge_fb = call_llm_with_fallback(
        judge_config,
        judge_ctx.to_openai_messages(),
        tools=None,
        temperature=0.3,
    )
    judge_resp = judge_fb.response
    if not judge_resp.content:
        return ""
    return judge_resp.content


def _parse_fusion_verdict(judge_text: str) -> tuple[int | None, str]:
    """Parse SCORE and REASON from judge response text."""
    import re as _re
    score_match = _re.search(r"SCORE:\s*(\d+)", judge_text)
    fusion_score = int(score_match.group(1)) if score_match else None
    reason_match = _re.search(r"REASON:\s*(.+?)(?:\n|$)", judge_text)
    fusion_feedback = reason_match.group(1).strip() if reason_match else ""
    return (fusion_score, fusion_feedback)


def _run_fusion_verification(goal: str, result: str, config: dict,
                              fallback_model: str) -> tuple[int | None, str]:
    """Run cross-model fusion verification using a different judge model."""
    judge_model_id = "MiniMax-M3"
    if judge_model_id == fallback_model:
        return (None, "")

    try:
        judge_text = _call_fusion_judge(goal, result, judge_model_id)
        if not judge_text:
            return (None, "")
        fusion_score, fusion_feedback = _parse_fusion_verdict(judge_text)

        if fusion_score is not None and fusion_score < 6:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                f"[delegate_task] Fusion verifier scored {fusion_score}/10: "
                f"{fusion_feedback}"
            )
        return (fusion_score, fusion_feedback)
    except Exception as _ve:
        import logging as _lg
        _lg.getLogger(__name__).debug(f"[delegate_task] Fusion verifier skipped: {_ve}")
        return (None, "")


def _format_subagent_result(goal: str, result: str, iteration: int,
                             fallback_model: str,
                             fusion_score: int | None = None,
                             fusion_feedback: str = "") -> str:
    """Format the final sub-agent result with model and verification metadata."""
    model_info = f"model={fallback_model}"
    if fusion_score is not None:
        model_info += f" | fusion_verify={fusion_score}/10"
        if fusion_feedback:
            model_info += f" | {fusion_feedback[:60]}"
    return (
        f"╔═══ 巳分工 (Sub-agent) ═══╗\n"
        f"│ Goal: {goal[:80]}\n"
        f"├─────────────────────────────┤\n"
        f"{result}\n"
        f"├─────────────────────────────┤\n"
        f"│ Iterations: {iteration + 1} | {model_info} |\n"
        f"╚═════════════════════════════╝"
    )


def _setup_sub_agent_env(goal: str, context: str, toolsets: str,
                          fallback_model: str) -> tuple[list, 'Context']:
    """Set up sub-agent environment: sys.path, tools, context. Returns (openai_tools, ctx)."""
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)
        sys.path.insert(0, str(Path(_BAW_ROOT).parent))
    _import_baw()

    from core.tools import get_openai_tools
    from core.context import Context
    openai_tools = get_openai_tools()

    if toolsets:
        allowed = {t.strip() for t in toolsets.split(",")}
        openai_tools = [t for t in openai_tools if t["function"]["name"] in allowed]

    prompt = _build_executor_prompt(goal, context)
    ctx = Context(
        system_prompt=_build_executor_system_prompt(fallback_model),
        temperature=0.3,
    )
    ctx.add_user(prompt)
    return openai_tools, ctx


def _execute_sub_agent_tool_calls(goal: str, resp, ctx: 'Context',
                                   config: dict, verify_retries: int,
                                   max_retries: int) -> tuple[int, int]:
    """Execute tool calls from an LLM response, verify them. Returns (tool_calls_made, updated_verify_retries)."""
    from core.tools import execute_tool
    import json

    tool_calls_made = len(resp.tool_calls)
    ctx.add_assistant(resp.content, resp.tool_calls)

    for tc in resp.tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
        result = execute_tool(name, args)
        ctx.add_tool_result(tc.get("id", ""), name, result)

        retry_prompt = _verify_execution_step(
            goal, name, args, result, config,
            verify_retries, max_retries
        )
        if retry_prompt:
            verify_retries += 1
            ctx.add_user(retry_prompt)
    return tool_calls_made, verify_retries


def _handle_attempt_no_tool_calls(
    resp_text: str, sub_promises: list, openai_tools: list,
    iteration: int, ctx: 'Context',
) -> bool:
    """Handle a response with no tool calls. Returns True if loop should continue."""
    reprompt = _check_promise_violation(resp_text, sub_promises, openai_tools, iteration)
    if not reprompt:
        return False
    if "PROMISE BROKEN" in reprompt:
        sub_promises.append(resp_text.split("。")[0].strip()[:120])
    ctx.add_user(reprompt)
    return True


def _run_attempt_error_return(iteration: int, tool_calls_made: int, error: str) -> dict:
    """Build an error result dict for a failed attempt."""
    return {"result_text": "", "iteration": iteration,
            "tool_calls_made": tool_calls_made, "error": error}


def _run_model_attempt(
    goal: str, context: str, toolsets: str,
    fallback_model: str, attempt_config: dict,
) -> dict:
    """Run a single model attempt. Returns {result_text, iteration, tool_calls_made, error}."""
    openai_tools, ctx = _setup_sub_agent_env(goal, context, toolsets, fallback_model)
    config = attempt_config
    MAX_VERIFY_RETRIES = 2
    OVERALL_TIMEOUT = 300
    final_content, tool_calls_made, verify_retries = "", 0, 0
    _start_time = __import__('time').time()
    sub_promises: list[str] = []

    try:
        for iteration in range(12):
            if __import__('time').time() - _start_time > OVERALL_TIMEOUT:
                final_content = f"[TIMEOUT] Exceeded {OVERALL_TIMEOUT}s. Partial: {final_content[:200]}"
                break
            try:
                fb = _call_llm_with_shutdown_retry(config, ctx, openai_tools)
            except (ValueError, ConnectionError, TimeoutError) as _llm_err:
                return _run_attempt_error_return(iteration, tool_calls_made, str(_llm_err))
            resp = fb.response
            if resp.content:
                final_content = resp.content
            if not resp.tool_calls:
                if _handle_attempt_no_tool_calls(resp.content or "", sub_promises,
                                                  openai_tools, iteration, ctx):
                    continue
                break
            tc_made, verify_retries = _execute_sub_agent_tool_calls(
                goal, resp, ctx, config, verify_retries, MAX_VERIFY_RETRIES)
            tool_calls_made += tc_made
    finally:
        _restore_tool_registry()

    return {
        "result_text": final_content.strip() or "(no output)",
        "iteration": iteration, "tool_calls_made": tool_calls_made, "error": None,
    }


def _process_model_attempt(goal, context, toolsets, fallback_model, attempt_config):
    """Run one model attempt and return (result_str, error_str_or_None)."""
    import logging as _lg
    attempt = _run_model_attempt(goal, context, toolsets, fallback_model, attempt_config)

    if attempt["error"]:
        return None, attempt["error"]

    result_text = attempt["result_text"]
    tool_calls_made = attempt["tool_calls_made"]

    if _detect_execution_failure(result_text, tool_calls_made):
        return None, result_text[:200]

    fusion_score, fusion_feedback = _run_fusion_verification(
        goal, result_text, attempt_config, fallback_model
    )
    return _format_subagent_result(
        goal, result_text, attempt["iteration"], fallback_model,
        fusion_score, fusion_feedback
    ), None


def delegate_task(goal: str, context: str = "", toolsets: str = "", model_id: str = "") -> str:
    """Delegate a task to a sub-agent (MiniMax executor).

    Sub-agent runs independently with its own tools and isolated context.
    Returns the sub-agent's final output.

    Auto-fallback: if the chosen model errors out (timeout, API failure,
    model not found), retries the ENTIRE sub-agent with the next available
    model from the tier preference list. Up to 2 model retries.

    Args:
        goal: The task for the sub-agent to accomplish. Be specific.
        context: Optional background info, file paths, constraints.
        toolsets: Comma-separated tool names to restrict (e.g. "bash,read_file").
                 Leave empty for all tools (bash, read_file, write_file, web_search).
        model_id: P0-1 fix — if non-empty, override model selection with this model.
                  Lets the caller (e.g. router) force a specific tier model.
                  Without this, the router's tier_preferences decision is silently dropped.
    """
    _base_config = _get_minimax_config(goal, model_override=model_id)
    _model_pool = _build_model_pool(goal, model_id, _base_config)
    _last_error = ""

    for _attempt_idx, _fallback_model in enumerate(_model_pool):
        if _attempt_idx > 0:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                f"[delegate_task] retrying model #{_attempt_idx}: "
                f"{_fallback_model} (previous error: {_last_error[:100]})"
            )
        _attempt_config = _get_minimax_config(goal, model_override=_fallback_model)
        result, err = _process_model_attempt(
            goal, context, toolsets, _fallback_model, _attempt_config
        )
        if err:
            _last_error = err
            continue
        return result

    raise RuntimeError(
        f"Sub-agent failed after {len(_model_pool)} model attempt(s). "
        f"Models tried: {_model_pool}. "
        f"Last error: {_last_error[:300]}"
    )



TOOL_DEF = {
    "name": "delegate_task",
    "description": (
        "Delegate a task to a sub-agent (MiniMax executor). "
        "Use for complex subtasks, parallel work, or anything that benefits from "
        "independent reasoning. Sub-agent has its own tools and isolated context. "
        "Returns the sub-agent's findings."
    ),
    "handler": delegate_task,
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The task for the sub-agent. Be specific and self-contained.",
            },
            "context": {
                "type": "string",
                "description": "Optional background info, file paths, constraints.",
            },
            "toolsets": {
                "type": "string",
                "description": "Comma-separated tool names to allow (e.g. 'bash,read_file'). Empty = all tools.",
            },
        },
        "required": ["goal"],
    },
    "risk_level": "high",
}
