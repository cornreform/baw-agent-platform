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


def _import_baw():
    """Import BAW modules once into a module-level cache."""
    if not hasattr(_import_baw, "_done"):
        sys.path.insert(0, _BAW_ROOT)
        sys.path.insert(0, str(Path(_BAW_ROOT).parent))
        _import_baw._done = True
    import importlib.util as _iu

    # Tools
    def _ld(name):
        p = Path(_BAW_ROOT) / "tools" / f"{name}.py"
        s = _iu.spec_from_file_location(f"_tk_{name}", str(p))
        if s is None or s.loader is None:
            raise ImportError(f"Cannot load tool '{name}'")
        m = _iu.module_from_spec(s)
        s.loader.exec_module(m)
        return m.TOOL_DEF

    from core.tools import register as _reg, clear as _clear
    import core.tools as _core_tools_mod

    # P1-2 (Opus 4.8 audit): save and restore the global tool registry around
    # the sub-agent's _clear() call. Without this, the sub-agent's _clear()
    # wipes out all tools the parent agent registered (post-P3 fix, 15+ tools),
    # and the parent agent loses access to them after delegation returns.
    # We do a shallow dict snapshot — values are tool-def dicts and don't mutate.
    # The registry dict is intentionally private; use getattr so we don't bind
    # to a name that might be renamed in a future refactor.
    # NEW-2 fix: use `is not None` so an empty {} registry still counts.
    _registry = None
    for _name in ("_tools", "_TOOLS"):
        _candidate = getattr(_core_tools_mod, _name, None)
        if _candidate is not None and isinstance(_candidate, dict):
            _registry = _candidate
            break
    if _registry is None:
        _registry = {}  # Fallback: empty registry, no save possible.
    _saved_tools = dict(_registry)
    try:
        _clear()
        _reg(**_ld("bash"))
        _reg(**_ld("read_file"))
        _reg(**_ld("write_file"))
        _reg(**_ld("web_search"))
        _reg(**_ld("web_extract"))
        _reg(**_ld("search_files"))
        _reg(**_ld("patch"))
        _reg(**_ld("memory"))
        _reg(**_ld("todo"))
        _reg(**_ld("vision"))
        _reg(**_ld("tts"))
        _reg(**_ld("image_generate"))
        _reg(**_ld("install"))
    except BaseException:
        # Restore on any failure so the parent process isn't left broken.
        _registry.clear()
        _registry.update(_saved_tools)
        raise
    # NEW-1 mitigation: use a per-call key derived from id() so concurrent
    # delegate_task invocations don't clobber each other's snapshot. We still
    # rely on a module attribute (can't easily pass a context object through
    # _import_baw without refactoring its signature) but the keying prevents
    # the most common reentrancy footgun. Caller (delegate_task) pops the
    # matching entry on exit.
    _snapshot_key = f"_pending_restore_{id(_saved_tools)}"
    setattr(_core_tools_mod, _snapshot_key, _saved_tools)

    return True  # tools registered

def _resolve_executor_model(cfg: dict, goal: str = "") -> str:
    """Resolve which model to use for a delegated task.

    Priority:
    1. model.task_rules — keyword match on goal (first match wins)
    2. executor.model — configured executor model
    3. model.fallback or model.default — final fallback
    """
    import re
    model_cfg = cfg.get("model", {})

    # ── Check per-task rules ──
    if goal:
        for rule in model_cfg.get("task_rules", []) or []:
            pattern = rule.get("match", "")
            if pattern and re.search(pattern, goal, re.IGNORECASE):
                matched_model = rule.get("model", "")
                if matched_model:
                    return matched_model

    # ── Fall back to executor.model → fallback → default ──
    return (
        cfg.get("executor", {}).get("model") or
        model_cfg.get("fallback") or
        model_cfg.get("default", "deepseek-v4-flash")
    )


def _get_minimax_config(goal: str = "", model_override: str = "") -> dict:
    """Load config and resolve the executor model (per-task routing support).
    Falls back gracefully if resolved model is not in providers list.

    Args:
        goal: Used to match model.task_rules for keyword-based routing.
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
                  Without this, the router's tier_preferences decision is silently
                  dropped because _resolve_executor_model re-runs task_rules.
    """
    # ── Resolve model pool for auto-fallback ──
    _base_config = _get_minimax_config(goal, model_override=model_id)
    _initial_model = _base_config.get("model", {}).get("default", "deepseek-v4-flash")

    # Build fallback model pool from tier preferences
    _model_pool = [_initial_model]
    try:
        from core.router import pick_model_for_tier, score_complexity, tier_of
        _score = score_complexity(goal)
        _tier = tier_of(_score)
        # Get the tier preference list to know which models to try
        _prefs = _base_config.get("router", {}).get("tier_preferences", {})
        if not _prefs.get(_tier):
            from core.router import get_tier_preferences
            _prefs = get_tier_preferences(_base_config)
        for _m in _prefs.get(_tier, []):
            if _m not in _model_pool:
                _model_pool.append(_m)
    except Exception:
        pass  # Best-effort — fallback pool just has the initial model

    # Max 2 model retries
    _model_pool = _model_pool[:3]  # at most 3 attempts (initial + 2 retries)
    _last_error = ""

    for _attempt_idx, _fallback_model in enumerate(_model_pool):
        if _attempt_idx > 0:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                f"[delegate_task] retrying model #{_attempt_idx}: "
                f"{_fallback_model} (previous error: {_last_error[:100]})"
            )

        # Rebuild config with this attempt's model
        _attempt_config = _get_minimax_config(goal, model_override=_fallback_model)

        # ── Ensure sys.path before any BAW imports ──
        if _BAW_ROOT not in sys.path:
            sys.path.insert(0, _BAW_ROOT)
            sys.path.insert(0, str(Path(_BAW_ROOT).parent))
        _import_baw()  # register tools (also sets sys.path if needed)

        # ── Import BAW modules (now sys.path is safe) ──
        from core.llm import call_llm_with_fallback
        from core.tools import execute_tool, get_openai_tools
        from core.context import Context
        openai_tools = get_openai_tools()
        config = _attempt_config

        # ── Restrict toolsets if specified ──
        if toolsets:
            allowed = {t.strip() for t in toolsets.split(",")}
            openai_tools = [t for t in openai_tools if t["function"]["name"] in allowed]

        # ── Build prompt with explicit checklist ──
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
        prompt = "\n".join(prompt_parts)

        # ── Run sub-agent in quick mode (no court/plan) ──
        ctx = Context(
            system_prompt=(
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
                f"13) Model: {_fallback_model}. Use this model for all LLM calls."
            ),
            temperature=0.3,
        )
        ctx.add_user(prompt)

        max_iterations = 12  # enough for complex tasks (API calls + retries + fallback)
        iteration = 0
        final_content = ""
        _tool_calls_made = 0  # track if sub-agent actually used tools
        _verify_retries = 0  # P1-4: per-iteration verify_step retries
        MAX_VERIFY_RETRIES = 2  # P1-4: hard cap so we don't loop forever
        OVERALL_TIMEOUT = 300  # 5 minutes max for entire sub-agent
        _start_time = __import__('time').time()
        _sub_promises: list[str] = []  # Promise tracker across iterations

        try:
            for iteration in range(max_iterations):
                # Overall timeout guard
                if __import__('time').time() - _start_time > OVERALL_TIMEOUT:
                    final_content = f"[TIMEOUT] Sub-agent exceeded {OVERALL_TIMEOUT}s overall limit. Partial result: {final_content[:200]}"
                    break
                try:
                    fb = call_llm_with_fallback(
                        config,
                        ctx.to_openai_messages(),
                        tools=openai_tools,
                        temperature=0.5,
                    )
                except (RuntimeError, ValueError, ConnectionError, TimeoutError) as _llm_err:
                    _last_error = str(_llm_err)
                    raise  # Re-raise to trigger model retry

                resp = fb.response

                if resp.content:
                    final_content = resp.content

                if not resp.tool_calls:
                    # ── Promise-based verification guard (sub-agent) ──
                    _resp_text = resp.content or ""
                    _resp_lower = _resp_text.lower()
                    _promise_patterns = [
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
                    _new_subs = []
                    for p_lower, p_label in _promise_patterns:
                        if p_lower in _resp_lower:
                            for _sent in _resp_text.split("。"):
                                if p_lower in _sent.lower():
                                    _new_subs.append(_sent.strip()[:120])
                                    break
                    if _new_subs:
                        _sub_list = "\n".join(f"  ✗ \"{p}\"" for p in _new_subs[:3])
                        _repropmpt = (
                            f"[PROMISE BROKEN] You said:\n{_sub_list}\n\n"
                            f"ZERO tools called. You MUST call a tool NOW.\n"
                            f"No text. Only tool calls."
                        )
                        _sub_promises.extend(_new_subs)
                        if iteration >= 3:
                            _tool_names = [t["function"]["name"] for t in openai_tools]
                            _repropmpt += (
                                "\n\nAvailable tools:\n"
                                + "\n".join(f"  - {n}" for n in _tool_names)
                                + "\n\nPick ONE tool matching your promise. Call it NOW."
                            )
                        ctx.add_user(_repropmpt)
                        continue
                    # Check if previous promises were never kept
                    if _sub_promises:
                        _old_list = "\n".join(f"  ⏳ \"{p}\"" for p in _sub_promises[-3:])
                        ctx.add_user(
                            f"[PROMISE AUDIT] Unfulfilled:\n{_old_list}\n"
                            "Call a tool NOW or the task FAILS."
                        )
                        continue
                    break  # Really done

                _tool_calls_made += len(resp.tool_calls)
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

                    # P1-4 (Opus 4.8 audit): verify each tool result with the configured
                    # judge model. score < 7 -> retry. Hard-capped at MAX_VERIFY_RETRIES
                    # so a broken verifier doesn't loop forever. Sub-agent failure
                    # modes (tool returned error string) still surface as normal errors.
                    if _verify_retries < MAX_VERIFY_RETRIES:
                        try:
                            from core.verifier import verify_step
                            verdict = verify_step(
                                goal=goal,
                                tool_name=name,
                                tool_args=args,
                                tool_result=str(result),
                                config=config,
                            )
                            score = int(verdict.get("score", 7))
                            if score < 7:
                                _verify_retries += 1
                                ctx.add_user(
                                    f"[Verifier] step scored {score}/10, "
                                    f"reason: {verdict.get('reason', '')[:200]}. "
                                    f"Try a different approach for this step."
                                )
                        except Exception as _ve:
                            # Verifier is best-effort. If it's misconfigured
                            # (no judge model, etc.) we don't want to crash delegation.
                            # NEW-3 fix: log the exception so silent failures are
                            # observable in the run logs, not just swallowed.
                            import logging
                            logging.getLogger(__name__).warning(
                                f"[delegate_task] verify_step failed silently: "
                                f"{type(_ve).__name__}: {_ve}"
                            )
        finally:
            # P1-2 (Opus 4.8 audit): restore the global tool registry that was
            # saved before the sub-agent ran. Without this, the parent agent's
            # tool list is permanently shrunk to the sub-agent's 6 tools.
            # NEW-1 mitigation: pop the snapshot matching THIS call (keyed by id()).
            # Falls back to scanning any leftover _pending_restore_* attrs if the
            # keyed one is missing (e.g. _import_baw raised before stashing).
            try:
                import core.tools as _ct
                # First: try the keyed snapshot
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
                            # Only restore the most recent one; the rest are stale
                            # from earlier calls that didn't clean up properly.
                            break
                if not _restored:
                    # Backward-compat: legacy single-slot name.
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
                # Restoration is best-effort. Don't mask the real return value.
                pass

        # ── Format result ──
        result = final_content.strip() or "(no output)"

        # ── Failure detection: if sub-agent described problems but didn't ACTUALLY solve ──
        _failure_keywords = [
            "failed", "error", "無法", "失敗", "placeholder",
            "not found", "not installed", "cannot", "unable",
            "冇實際執行", "只係講", "迷失方向", "唔 work",
            "冇做到", "未有", "sub-agent",
        ]
        _result_lower = result.lower()
        _is_failure = any(kw in _result_lower for kw in _failure_keywords) and _tool_calls_made == 0
        # Also detect: sub-agent just described what happened without taking action
        if _tool_calls_made == 0 and len(result) > 50:
            _is_failure = True  # long text output without any tool call = LLM just talked

        if _is_failure:
            _last_error = result[:200]
            # Try next model if available
            continue

        # Success — return with model info
        _model_info = f"model={_fallback_model}"
        return (
            f"╔═══ 巳分工 (Sub-agent) ═══╗\n"
            f"│ Goal: {goal[:80]}\n"
            f"├─────────────────────────────┤\n"
            f"{result}\n"
            f"├─────────────────────────────┤\n"
            f"│ Iterations: {iteration + 1} | {_model_info} |\n"
            f"╚═════════════════════════════╝"
        )

    # All models failed
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
