"""
BAW — Agent Loop: Debate-first, Execute-second

Flow per user turn:
  Phase 1 — Court (independent analysis)
    Devil and Angel analyze the SAME input independently.
    Both give scores. BAW synthesizes neutrally.
    ⚠️ NEITHER voice has execution power during court.

  Phase 2 — Neutral response & debate (interactive only)
    BAW responds from neutral perspective.
    NOT trying to please the user — WILLING to disagree.
    User ↔ Agent back and forth until conclusion.

  Phase 3 — Execute (once conclusion is reached)
    BAW executes toward the goal.
    Plan → Execute per step → Verify → Report.
    No re-litigation — the debate is settled.
"""
from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Optional
from typing import Callable

from .llm import get_model, call_llm_with_fallback, calculate_cost, FallbackResult
from .context import Context, Message
from .tools import get_openai_tools, execute_tool, list_tools
from .permission import PermissionEngine
from .memory import MemoryStore
from .checkpoint import Checkpointer
from .file_history import FileHistory
from .autosave import auto_commit, get_commit_log
from . import render as html

# ── Cost tracking (thread-safe class) ──────────────────────────

import threading


class CostTracker:
    """Thread-safe cost accumulator. One instance per session."""

    def __init__(self):
        self._lock = threading.Lock()
        self.total = 0.0
        self.calls: list[dict] = []

    def record(self, model_name: str, tokens_in: int, tokens_out: int, cost: float):
        with self._lock:
            self.calls.append({
                "model": model_name, "tokens_in": tokens_in,
                "tokens_out": tokens_out, "cost": round(cost, 6),
            })
            self.total += cost

    def summary(self) -> str:
        with self._lock:
            if not self.calls:
                return ""
            calls_info = " | ".join(
                f"{c['tokens_in']}↑{c['tokens_out']}↓${c['cost']:.4f}"
                for c in self.calls
            )
            return f"📊 [{len(self.calls)} LLM calls] {calls_info} | **total: ${self.total:.4f}**"

    def html_summary(self) -> str:
        with self._lock:
            if not self.calls:
                return ""
            calls_info = " | ".join(
                f"{c['tokens_in']}↑{c['tokens_out']}↓<code>${c['cost']:.4f}</code>"
                for c in self.calls
            )
            return (
                f"📊 <b>[{len(self.calls)} LLM calls]</b> {calls_info} | "
                f"<b>total: ${self.total:.4f}</b>"
            )

    def reset(self):
        with self._lock:
            self.total = 0.0
            self.calls = []


_TRACKER: CostTracker | None = None


def _get_tracker() -> CostTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = CostTracker()
    return _TRACKER


def record_cost(model_name: str, tokens_in: int, tokens_out: int, cost: float):
    _get_tracker().record(model_name, tokens_in, tokens_out, cost)


def format_cost_summary() -> str:
    return _get_tracker().summary()


def html_cost_summary() -> str:
    return _get_tracker().html_summary()


def reset_cost():
    _get_tracker().reset()


# ── System prompt ──────────────────────────────────────────────

def build_system_prompt(config: dict, data_dir: Optional[Path] = None,
                        fresh_start: bool = False,
                        quick_mode: bool = False) -> str:
    """Build system prompt from SOUL.md + dynamic context.

    quick_mode=True: loads only core identity, skips heavy court/tool rules.
    fresh_start=True: returns a minimal prompt with no SOUL.md or memories.
    """
    if fresh_start:
        return (
            "You are an AI assistant. No prior context, no memories, no soul profile.\n"
            "Respond to this prompt with your raw, unfiltered judgment.\n"
            "Be direct and honest. Do not assume any prior conversation.\n"
        )

    base_path = data_dir or Path.home() / ".baw"
    soul_path = base_path / "SOUL.md"

    if soul_path.exists():
        soul_text = soul_path.read_text(encoding="utf-8")
        if quick_mode:
            # Quick mode: only identity + core philosophy (first ~600 chars)
            # Skip court rules, tool descriptions, permission rules
            lines = soul_text.splitlines()
            trimmed = []
            for line in lines:
                trimmed.append(line)
                if "## 核心靈魂" in line:
                    for inner in lines[lines.index(line)+1:]:
                        if inner.startswith("## "):
                            break
                        trimmed.append(inner)
                    break
            system_prompt = "\n".join(trimmed)
            system_prompt += (
                "\n\n## Quick mode\n"
                "- Respond in Traditional Chinese (Cantonese)\n"
                "- Lead with result, 1 paragraph max\n"
                "- ALWAYS report what actually happened after tool execution\n"
                "- CRITICAL: You MUST use tools (bash, read_file, etc.) when the user asks for data\n"
                "  Do NOT fabricate system info — always call the relevant tool to get real data\n"
                "- 🔴 Do NOT ask 'should I continue?' or 'what next?'. Execute the ENTIRE plan silently."
            )
        else:
            system_prompt = soul_text
    else:
        system_prompt = (
            "You are BAW (Black And White), Sunny's agent platform.\n"
            "Respond in Traditional Chinese (Cantonese).\n"
            "Be concise, lead with results.\n"
            "Never ask the user what to do — figure it out yourself."
        )

    if not quick_mode:
        # Dynamic context (only for full modes)
        tone = config.get("tone", {}).get("default", "casual")
        fact_mode = config.get("fact_check", {}).get("mode", "normal")
        tools_list = ", ".join(t.name for t in list_tools())

        # Build available models summary
        available_models = []
        for pname, pdata in config.get("providers", {}).items():
            for m in pdata.get("models", []):
                mid = m.get("id", "?")
                caps = m.get("capabilities", [])
                available_models.append(f"{mid} ({pname}: {', '.join(caps)})")
        models_summary = ", ".join(available_models) if available_models else "none configured"
        default_model = config.get("model", {}).get("default", "unknown")
        config_path = data_dir / "config.yaml" if data_dir else Path.home() / ".baw" / "config.yaml"
        env_path = data_dir / ".env" if data_dir else Path.home() / ".baw" / ".env"

        system_prompt += (
            f"\n\n## System config\n"
            f"- Config file: {config_path}\n"
            f"- Env file: {env_path}\n"
            f"- Default model: {default_model}\n"
            f"- Available models: {models_summary}\n"
            f"  NEVER fabricate model names. Only use models from this list.\n"
            f"  If you need a model not in the list, you must add it to config.yaml first.\n"
            f"\n## Tool self-configuration (CRITICAL)\n"
            f"- You have FULL autonomy to discover, register, and use ANY system CLI tool.\n"
            f"- When told to use a new tool: 'which <tool>' or 'find / -name <tool>' to locate it.\n"
            f"- Test it via 'bash' first. If useful, create a permanent wrapper:\n"
            f"  1. Read ~/baw/tools/vision.py as template\n"
            f"  2. Write new wrapper at ~/baw/tools/<name>.py\n"
            f"  3. Tool auto-registers — no restart, no manual config.\n"
            f"- Also discover tools proactively: 'ls /usr/bin /usr/local/bin ~/.local/bin' for new capabilities.\n"
            f"- NEVER wait for the user to pre-configure tools. You own your toolchain.\n"
            f"\n## Dynamic context\n"
            f"- Current tone: {tone}\n"
            f"- Fact check mode: {fact_mode}\n"
            f"- Available tools: {tools_list}\n"
            f"- Cost transparency: per-call cost shown after each response\n"
            f"- 🔴 HARD RULE: Complete the ENTIRE plan without pausing. Do NOT ask the user 'should I continue?' or 'what next?'. The user already gave you the full goal — execute ALL steps silently, start to finish.\n"
            f"- 🔴 After each step completes, immediately proceed to the next step. Do NOT wait, do NOT summarize, do NOT ask for permission.\n"
            f"- 🔴 Only speak when ALL steps are done. Present the final result only. No partial reports.\n"
            f"- NEVER end your response with a question. Execute directly."
        )

    return system_prompt


# ── Main agent loop ────────────────────────────────────────────

MAX_STEP_RETRIES = 3
MAX_CONSECUTIVE_FAILURES = 3
MAX_STEP_SECONDS = 120  # individual step timeout — prevent silent hangs


def run_agent(
    prompt: str,
    config: dict,
    model_id: Optional[str] = None,
    data_dir: Optional[Path] = None,
    verbose: bool = False,
    interactive: bool = False,
    fresh_start: bool = False,
    mode: Optional[str] = None,
    conversation_history: Optional[list[dict]] = None,
    progress_callback: Optional[Callable[..., None]] = None,
) -> tuple[str, dict]:
    """Run BAW agent with debate-first, execute-second flow.

    Phase 1 — Court: Devil + Angel analyze independently.
    Phase 2 — Neutral response & debate (interactive only).
    Phase 3 — Execute: plan → execute → verify → report.

    If fresh_start=True, SOUL.md and memories are bypassed entirely.
    conversation_history: list of {role, content, ...} dicts from previous turns.
    progress_callback: called on each tool call or milestone (for timeout refresh).
    Returns: (response_text, info_dict)
    """
    # ── Initialise ──
    model = get_model(config, model_id)
    model_temperature = getattr(model, "temperature", 0.7)
    perm = PermissionEngine(config)
    mem = MemoryStore(data_dir or Path.home() / ".baw")
    checkpointer = Checkpointer()

    # Init search providers
    try:
        from .search import _auto_discover as _init_search
        _init_search()
    except Exception:
        pass

    system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start)

    # ── Detect tone switch ──
    from .tone import detect_tone_switch, format_tone_confirmation
    old_tone = config.get("tone", {}).get("default", "casual")
    new_tone = detect_tone_switch(prompt)
    tone_change = False
    if new_tone and new_tone != old_tone:
        config.setdefault("tone", {})["default"] = new_tone
        system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start)
        tone_change = True

    # Memory search
    memories = mem.search(prompt, limit=3)
    mem_text = ""
    if memories:
        mem_text = "\n".join(
            f"- [{m['score']:.2f}] {m['content']}" for m in memories
        )

    reset_cost()
    session_cost = 0.0
    court_result = None

    # ── Resolve execution mode ──
    _mode = (mode or config.get("mode", "tight")).lower()
    if _mode not in ("quick", "hybrid", "tight"):
        _mode = "tight"

    # ── Build system prompt ──
    _is_quick = (_mode == "quick")
    system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start, quick_mode=_is_quick)

    # ── Phase 1: Build context ──
    ctx = Context(system_prompt=system_prompt, temperature=model_temperature)

    # Inject conversation history (between system prompt and current prompt)
    _history_msg_count = 1  # system message at index 0
    if conversation_history:
        for _hmsg in conversation_history:
            _role = _hmsg.get("role", "")
            _content = _hmsg.get("content", "")
            if _role == "user":
                ctx.add_user(_content)
                _history_msg_count += 1
            elif _role == "assistant":
                ctx.add_assistant(_content, _hmsg.get("tool_calls"))
                _history_msg_count += 1
            elif _role == "tool":
                ctx.add_tool_result(
                    _hmsg.get("tool_call_id", "call_xxx"),
                    _hmsg.get("name", ""),
                    _content or "",
                )
                _history_msg_count += 1

        # ── Sequence validation: strip dangling tool_calls from any message ──
        # (truncated history may cut tool messages mid-sequence)
        for _i in range(len(ctx.messages) - 1):
            _msg = ctx.messages[_i]
            if _msg.role != "assistant" or not _msg.tool_calls:
                continue
            _next = ctx.messages[_i + 1]
            if _next.role != "tool":
                logger.warning(
                    f"[Loop] Stripped dangling tool_calls from assistant message {_i} "
                    f"(not followed by tool response — history truncated)"
                )
                _msg.tool_calls = None
        # Also strip last message's tool_calls if it's the tail
        if ctx.messages and ctx.messages[-1].role == "assistant" and ctx.messages[-1].tool_calls:
            logger.warning(
                f"[Loop] Stripped dangling tool_calls from trailing assistant message "
                f"(history truncated / interrupted session)"
            )
            ctx.messages[-1].tool_calls = None

    # ── Quick Mode marker for new-message extraction ──
    _pre_prompt_count = _history_msg_count
    ctx.add_user(prompt)

    # ── Helper: extract new messages from this turn ──
    def _extract_new_msgs(_ctx, _since_idx):
        _out = []
        for _m in _ctx.messages[_since_idx:]:
            if hasattr(_m, 'role'):
                _d = {"role": _m.role, "content": _m.content or ""}
                if hasattr(_m, 'tool_calls') and _m.tool_calls:
                    _d["tool_calls"] = _m.tool_calls
                if hasattr(_m, 'tool_call_id') and getattr(_m, 'tool_call_id', None):
                    _d["tool_call_id"] = _m.tool_call_id
                if hasattr(_m, 'name') and getattr(_m, 'name', None):
                    _d["name"] = _m.name
                if _m.role != "system":
                    _out.append(_d)
            elif isinstance(_m, dict):
                if _m.get("role") != "system":
                    _out.append(dict(_m))
        return _out
    if mem_text:
        ctx.add_user(f"Relevant memories:\n{mem_text}")

    # ── Quick Mode: no court, no plan, just execute ──
    if _mode == "quick":
        skip_verify = True
        skip_adversarial = True
        skip_plan = True
        max_recovery = 1
        fb = call_llm_with_fallback(
            config, ctx.to_openai_messages(),
            tools=get_openai_tools(), temperature=model_temperature,
        )
        quick_resp = fb.response
        q_cost = calculate_cost(model, quick_resp.input_tokens, quick_resp.output_tokens)
        session_cost += q_cost
        record_cost(f"{model.provider}/{model.id}", quick_resp.input_tokens, quick_resp.output_tokens, q_cost)
        ctx.add_assistant(quick_resp.content, quick_resp.tool_calls)

        # Execute any tool calls
        while quick_resp.tool_calls:
            for tc in quick_resp.tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if progress_callback:
                    progress_callback("tool", name, args)
                _show_progress = verbose or interactive
                if _show_progress:
                    print(f"\033[90m🔧 {name}", end="", flush=True)
                perm_result = perm.check(name, args)
                if perm_result["decision"] == "deny":
                    if _show_progress:
                        print(f" ⛔ BLOCKED: {perm_result['reason']}\033[0m")
                    ctx.add_tool_result(tc.get("id", ""), name, f"[BLOCKED] {perm_result['reason']}")
                    continue
                if _show_progress:
                    _arg_str = str(args)[:80]
                    print(f" {_arg_str}", end="", flush=True)
                exe_result = execute_tool(name, args)
                if _show_progress:
                    print(f" \033[32m✅\033[0m", flush=True)
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)

            # Next LLM call
            fb = call_llm_with_fallback(
                config, ctx.to_openai_messages(),
                tools=get_openai_tools(), temperature=model_temperature,
            )
            quick_resp = fb.response
            q_cost = calculate_cost(model, quick_resp.input_tokens, quick_resp.output_tokens)
            session_cost += q_cost
            record_cost(f"{model.provider}/{model.id}", quick_resp.input_tokens, quick_resp.output_tokens, q_cost)
            ctx.add_assistant(quick_resp.content, quick_resp.tool_calls)

        # Collect output
        output = ""
        if tone_change:
            output += format_tone_confirmation(old_tone, new_tone) + "\n\n"

        # Determine final response content
        final_content = quick_resp.content or ""

        # If final LLM response is empty but we executed tool calls,
        # auto-generate a completion summary from tool results
        if not final_content and ctx.messages:
            tool_summaries = []
            # Extract last N tool results for summary
            for m in reversed(ctx.messages):
                role = m.role if hasattr(m, 'role') else m.get("role", "")
                content = m.content or "" if hasattr(m, 'content') else m.get("content", "") or ""
                if role == "tool" and content:
                    # Truncate long tool output
                    tool_summaries.append(content[:300])
                    if len(tool_summaries) >= 3:
                        break
            if tool_summaries:
                final_content = "✅ Done.\n\n" + "\n".join(
                    f"• {s.strip()}" for s in reversed(tool_summaries)
                )

        # Fallback: use last non-empty assistant message
        if not final_content:
            for msg in ctx.messages:
                role = msg.role if hasattr(msg, 'role') else msg.get("role", "")
                content = msg.content or "" if hasattr(msg, 'content') else msg.get("content", "") or ""
                if role == "assistant" and content:
                    final_content = content

        output += final_content
        output += f"\n\n{format_cost_summary()}"
        try:
            mem.remember(f"User: {prompt[:150]} → BAW: {final_content[:150]}")
        except Exception:
            pass
        return output, {
            "cost": round(session_cost, 4),
            "model": f"{model.provider}/{model.id}",
            "iterations": 0,
            "steps": 0,
            "mode": "quick",
            "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        }

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Court — Independent dual-voice analysis
    # ═══════════════════════════════════════════════════════════════
    from .adversarial import AdversarialCourt
    # Load per-side model config (both fall back to default model if unset)
    adv_cfg = config.get("adversarial", {})
    angel_model_id = adv_cfg.get("angel_model")
    devil_model_id = adv_cfg.get("devil_model")
    angel_model = None
    devil_model = None
    try:
        if angel_model_id:
            angel_model = get_model(config, angel_model_id)
        if devil_model_id:
            devil_model = get_model(config, devil_model_id)
    except Exception:
        pass
    court = AdversarialCourt(
        model, system_prompt, config,
        angel_model=angel_model, devil_model=devil_model,
    )
    court_enabled = config.get("adversarial", {}).get("enabled", True) and _mode == "tight"

    if court_enabled:
        if verbose:
            print("\n  ⚖️ Court: Devil + Angel analyzing independently...")
        if progress_callback:
            progress_callback("court", "", {})
        verdict = court.hold_court(prompt, mem_text)
        court_result = verdict

        session_cost += (verdict["devil"]["cost"] + verdict["angel"]["cost"])
        record_cost(
            f"{model.provider}/{model.id}",
            verdict["devil"]["tokens_in"], verdict["devil"]["tokens_out"],
            verdict["devil"]["cost"],
        )
        record_cost(
            f"{model.provider}/{model.id}",
            verdict["angel"]["tokens_in"], verdict["angel"]["tokens_out"],
            verdict["angel"]["cost"],
        )

        # Inject both analyses into context (not as a decision — as perspectives)
        court_context = (
            f"\n─── 👿 DEVIL'S INDEPENDENT ANALYSIS ───\n"
            f"{verdict['devil']['content']}\n"
            f"Score: {verdict['devil_score']}/10\n"
            f"─── 😇 ANGEL'S INDEPENDENT ANALYSIS ───\n"
            f"{verdict['angel']['content']}\n"
            f"Score: {verdict['angel_score']}/10\n"
            f"─── END COURT ───\n\n"
            f"Both perspectives are available. "
            f"Now respond from a NEUTRAL perspective — "
            f"you are NOT obligated to please the user. "
            f"Be honest. Be willing to disagree. "
            f"Your goal is to reach the best outcome through honest discussion."
        )
        ctx.add_user(court_context)

        if verbose:
            print(f"  [Court] Devil={verdict['devil_score']}/10 | Angel={verdict['angel_score']}/10 | Gap={verdict['score_gap']} ({verdict['agreement_level']})")

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: Neutral response
    # BAW responds from neutral perspective — may disagree with user
    # ═══════════════════════════════════════════════════════════════

    # Call LLM with court context (or without, for hybrid mode)
    fb = call_llm_with_fallback(
        config, ctx.to_openai_messages(),
        tools=get_openai_tools(),
        temperature=model_temperature,
    )
    neutral_response = fb.response
    n_cost = calculate_cost(model, neutral_response.input_tokens, neutral_response.output_tokens)
    session_cost += n_cost
    record_cost(f"{model.provider}/{model.id}", neutral_response.input_tokens, neutral_response.output_tokens, n_cost)
    ctx.add_assistant(neutral_response.content, neutral_response.tool_calls)

    # In non-interactive mode, strip dangling tool_calls from neutral response
    # (they won't be executed — Phase 3 uses plan+delegate instead)
    if not interactive and neutral_response.tool_calls and ctx.messages:
        _last = ctx.messages[-1]
        if _last.role == "assistant" and _last.tool_calls:
            _last.tool_calls = None

    # ═══════════════════════════════════════════════════════════════
    # Phase 2.5: Debate (interactive mode only)
    # In single-shot mode, BAW must decide on its own.
    # ═══════════════════════════════════════════════════════════════

    if interactive:
        # Execute any tool calls from the neutral response (live progress)
        _resp = neutral_response
        while _resp.tool_calls:
            for tc in _resp.tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if progress_callback:
                    progress_callback("tool", name, args)
                print(f"\033[90m🔧 {name}", end="", flush=True)
                perm_result = perm.check(name, args)
                if perm_result["decision"] == "deny":
                    print(f" ⛔ BLOCKED: {perm_result['reason']}\033[0m")
                    ctx.add_tool_result(tc.get("id", ""), name, f"[BLOCKED] {perm_result['reason']}")
                    continue
                print(f" {str(args)[:80]}", end="", flush=True)
                exe_result = execute_tool(name, args)
                print(f" \033[32m✅\033[0m", flush=True)
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)
            # Next LLM call to synthesize results
            fb = call_llm_with_fallback(config, ctx.to_openai_messages(), tools=get_openai_tools(), temperature=model_temperature)
            _resp = fb.response
            n_cost = calculate_cost(model, _resp.input_tokens, _resp.output_tokens)
            session_cost += n_cost
            record_cost(f"{model.provider}/{model.id}", _resp.input_tokens, _resp.output_tokens, n_cost)
            ctx.add_assistant(_resp.content, _resp.tool_calls)

        # Now return the final synthesized response
        output = ""
        if tone_change:
            output += format_tone_confirmation(old_tone, new_tone) + "\n\n"
        output += (_resp.content or "")
        output += f"\n\n{format_cost_summary()}"

        try:
            mem.remember(f"User: {prompt[:150]} → BAW: {(_resp.content or '')[:150]}")
        except Exception:
            pass

        return output, {
            "cost": round(session_cost, 4),
            "model": f"{model.provider}/{model.id}",
            "iterations": 1,
            "steps": 0,
            "adversarial": "debate",
            "adversarial_raw": court_result,
            "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        }

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: Execute (single-shot mode)
    # BAW makes its best judgment and executes.
    # ═══════════════════════════════════════════════════════════════

    # If BAW's neutral response didn't call any tools, just return it
    if not neutral_response.tool_calls:
        output = ""
        if tone_change:
            output += format_tone_confirmation(old_tone, new_tone) + "\n\n"
        output += (neutral_response.content or "")
        output += f"\n\n{format_cost_summary()}"

        try:
            mem.remember(f"User: {prompt[:150]} → BAW: {(neutral_response.content or '')[:150]}")
        except Exception:
            pass

        return output, {
            "cost": round(session_cost, 4),
            "model": f"{model.provider}/{model.id}",
            "iterations": 1,
            "steps": 0,
            "adversarial": court_result["agreement_level"] if court_result else None,
            "adversarial_raw": court_result,
            "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        }

    # ── Build output ──
    output_parts = []
    if tone_change:
        output_parts.append(format_tone_confirmation(old_tone, new_tone))
    output = "\n\n".join(output_parts)
    if output_parts:
        output += "\n\n"

    # ── Phase 3a: Orchestrator writes execution plan ──
    total_llm_calls = 1
    _execution_plan: list[dict] = []
    _execution_progress: list[str] = []

    plan_prompt = (
        f"[ORCHESTRATOR] Goal: {prompt}\n\n"
        f"Write a step-by-step execution plan.\n"
        f"Each step must be SMALL (max 1-2 tool calls).\n"
        f"Describe each step in human language — "
        f"NOT raw commands.\n"
        f"Format each step as:\n"
        f"  Step N: <description> — <tool> — <expected outcome>\n\n"
        f"Reply with ONLY the plan, numbered 1..N."
    )
    _plan_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": plan_prompt}]
    plan_fb = call_llm_with_fallback(
        config, _plan_msgs,
        temperature=None,
    )
    plan_response = plan_fb.response
    total_llm_calls += 1
    cost = calculate_cost(model, plan_response.input_tokens, plan_response.output_tokens)
    session_cost += cost
    record_cost(f"{model.provider}/{model.id}", plan_response.input_tokens, plan_response.output_tokens, cost)

    # Parse plan into steps
    import re as _re
    plan_text = plan_response.content or ""
    for _line in plan_text.split("\n"):
        _m = _re.match(r'\s*(?:Step\s*)?(\d+)[.:]\s*(.*)', _line.strip())
        if _m:
            _execution_plan.append({"num": int(_m.group(1)), "desc": _m.group(2)})
    if not _execution_plan:
        _execution_plan.append({"num": 1, "desc": plan_text[:200]})

    if verbose:
        print(f"\n  📋 Plan ({len(_execution_plan)} steps):")
        for _s in _execution_plan:
            print(f"    {_s['num']}. {_s['desc'][:100]}")

    # Append plan to system prompt for context
    _plan_text_for_context = (
        "\n[Execution Plan]\n" +
        "\n".join(f"  Step {s['num']}: {s['desc']}" for s in _execution_plan) +
        "\n\n[Progress: nothing completed yet]"
    )
    ctx.system_prompt += _plan_text_for_context

    # ── Display: show plan to user ──
    from . import display as dsp
    _display_log: list[str] = []
    if _execution_plan:
        _display_log.append(dsp.phase_plan(_execution_plan))

    # ── Phase 3b: Goal-pursuit loop ──
    # Each step: execute → if fail → think alternative → retry
    # Whole loop: after all steps → self-review → if goal not met → retry with new plan
    import importlib.util as _dt_iu
    _dt_spec = _dt_iu.spec_from_file_location(
        "_tk_delegate",
        str(Path(__file__).resolve().parent.parent / "tools" / "delegate_task.py"),
    )
    _dt_mod = _dt_iu.module_from_spec(_dt_spec)
    _dt_spec.loader.exec_module(_dt_mod)
    _delegate_fn = _dt_mod.delegate_task

    # ── Phase 3b: Goal-pursuit loop ──
    # Route recalculation: wrong turn → silently recalculates new route from current position
    # No retries, no skipping — just instant re-route from where you are.
    _GOAL_PURSUIT_MAX_ATTEMPTS = 3  # Max full from-scratch re-plans
    _MAX_RECALCULATES = 5           # Max micro re-routes per pursuit (generous: 5 chances)
    steps_completed = 0
    _delegation_results: list[str] = []
    _synthesis_results: list[str] = []  # Successful step results for final synthesis
    _goal_achieved = False

    for _pursuit in range(1, _GOAL_PURSUIT_MAX_ATTEMPTS + 1):
        if verbose:
            print(f"\n  🎯 Goal pursuit iteration {_pursuit}/{_GOAL_PURSUIT_MAX_ATTEMPTS}")

        # ── (Re-)Plan entire goal ──
        if _pursuit > 1:
            _failure_log = "\n".join(
                r[:500] for r in _delegation_results
            ) if _delegation_results else "No details"

            _plan_prompt = (
                f"[ORCHESTRATOR - RETRY {_pursuit - 1}/{_GOAL_PURSUIT_MAX_ATTEMPTS}]\n\n"
                f"Original goal: {prompt}\n\n"
                f"Previous run failed. Here's what happened:\n{_failure_log}\n\n"
                f"Analyse what went wrong. Write a COMPLETELY DIFFERENT plan.\n"
                f"Format:\n"
                f"  Step N: <description> — <tool>\n"
            )
            _plan_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": _plan_prompt}]
            _plan_fb = call_llm_with_fallback(config, _plan_msgs, temperature=None)
            _plan_resp = _plan_fb.response
            total_llm_calls += 1
            _cost2 = calculate_cost(model, _plan_resp.input_tokens, _plan_resp.output_tokens)
            session_cost += _cost2
            record_cost(f"{model.provider}/{model.id}", _plan_resp.input_tokens, _plan_resp.output_tokens, _cost2)

            _execution_plan = []
            for _line in (_plan_resp.content or "").split("\n"):
                _m = re.match(r'\s*(?:Step\s*)?(\d+)[.:]\s*(.*)', _line.strip())
                if _m:
                    _execution_plan.append({"num": int(_m.group(1)), "desc": _m.group(2)})
            if not _execution_plan:
                _execution_plan.append({"num": 1, "desc": (_plan_resp.content or "")[:200]})

            _delegation_results = []
            _synthesis_results = []
            steps_completed = 0

        if progress_callback:
            progress_callback("plan", "", {"steps": len(_execution_plan)})

        # ── Execute steps - route navigation ──
        # Each step tried ONCE. If fail → recalculate remaining route from here.
        _pursuit_failed = False
        _recalc_count = 0
        _step_idx = 0

        while _step_idx < len(_execution_plan) and not _pursuit_failed:
            _step = _execution_plan[_step_idx]
            _step_goal = _step['desc']

            # Show step progress for all steps (including step 1)
            if progress_callback:
                progress_callback("delegate", "", {"step": _step_idx + 1, "total": len(_execution_plan), "goal": _step_goal[:80]})

            _step_ctx = ""
            if _synthesis_results:
                _step_ctx = "Completed so far:\n" + "\n---\n".join(
                    f"Step {i+1}:\n{r[:800]}" for i, r in enumerate(_synthesis_results)
                )

            _step_desc_short = _step['desc'][:80]
            if verbose:
                print(f"  🗺️ Step {_step_idx + 1}/{len(_execution_plan)}: {_step_desc_short}")

            try:
                # ── Step timeout: prevent silent hangs from stuck sub-agents ──
                _step_exc = [None]
                _step_result = [None]
                _step_done = threading.Event()

                def _run_step():
                    try:
                        _step_result[0] = _delegate_fn(
                            goal=_step_goal,
                            context=_step_ctx,
                            toolsets="",
                        )
                    except Exception as _se:
                        _step_exc[0] = _se
                    finally:
                        _step_done.set()

                _step_thread = threading.Thread(target=_run_step, daemon=True)
                _step_thread.start()
                _step_done.wait(timeout=MAX_STEP_SECONDS)
                if not _step_done.is_set():
                    raise TimeoutError(f"Step timed out after {MAX_STEP_SECONDS}s")
                if _step_exc[0]:
                    raise _step_exc[0]  # re-raise inside try block for recalc
                _result = _step_result[0]

                while len(_delegation_results) <= _step_idx:
                    _delegation_results.append("")
                _delegation_results[_step_idx] = _result
                _synthesis_results.append(_result)
                steps_completed += 1

                _dsp = dsp.phase_step_done(_step_idx + 1, len(_execution_plan), _step_desc_short)
                _display_log.append(_dsp)
                if verbose:
                    print(_dsp)
                _step_idx += 1  # Move to next step
                _recalc_count = 0  # Reset — step succeeded, fresh count for next position

            except Exception as _e:
                while len(_delegation_results) <= _step_idx:
                    _delegation_results.append("")
                _delegation_results[_step_idx] = f"[FAILED] {_step_desc_short}: {_e}"

                if verbose:
                    print(f"  🚫 Step {_step_idx + 1} failed: {str(_e)[:100]}")
                    print(f"     ↻ Route recalculation...")

                # ── Route recalculation: recalculate remaining route from here ──
                _recalc_count += 1
                if _recalc_count > _MAX_RECALCULATES:
                    _pursuit_failed = True
                    break

                if progress_callback:
                    progress_callback("recalc", "", {"step": _step_idx + 1, "count": _recalc_count, "error": str(_e)[:60]})

                _done_summary = "\n".join(
                    f"Step {i+1}: {r[:200]}" for i, r in enumerate(_synthesis_results)
                ) if _synthesis_results else "Nothing completed yet."

                _replan_prompt = (
                    f"[GOOGLE MAPS RECALCULATE #{_recalc_count}]\n"
                    f"Original destination: {prompt}\n\n"
                    f"Journey so far:\n{_done_summary}\n\n"
                    f"Just took a wrong turn at: {_step['desc']}\n"
                    f"Error: {str(_e)[:300]}\n\n"
                    f"Recalculate a NEW route from HERE to reach the destination.\n"
                    f"List ONLY the remaining steps needed (numbered from 1).\n"
                    f"Do NOT re-list already completed steps.\n"
                    f"Format each step:\n"
                    f"  Step N: <description> — <tool>"
                )
                _replan_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": _replan_prompt}]
                _replan_fb = call_llm_with_fallback(config, _replan_msgs, temperature=None)
                _replan_resp = _replan_fb.response
                total_llm_calls += 1
                _cost_r = calculate_cost(model, _replan_resp.input_tokens, _replan_resp.output_tokens)
                session_cost += _cost_r
                record_cost(f"{model.provider}/{model.id}", _replan_resp.input_tokens, _replan_resp.output_tokens, _cost_r)

                _new_steps = []
                for _line in (_replan_resp.content or "").split("\n"):
                    _m = re.match(r'\s*(?:Step\s*)?(\d+)[.:]\s*(.*)', _line.strip())
                    if _m:
                        _new_steps.append({"num": int(_m.group(1)), "desc": _m.group(2)})

                if _new_steps:
                    # Replace remaining plan with new route (keep completed steps)
                    _execution_plan = _execution_plan[:_step_idx] + _new_steps
                    # NOTE: _recalc_count keeps accumulating — only resets on step success
                    if verbose:
                        print(f"     ✅ New route: {len(_new_steps)} remaining steps")
                    # Don't increment _step_idx — retry this position with new first step
                else:
                    # Can't recalculate — entire pursuit iteration fails
                    _pursuit_failed = True
                    _dsp = dsp.phase_step_error(_step_idx + 1, len(_execution_plan), _step_desc_short, str(_e)[:80])
                    _display_log.append(_dsp)
                    if verbose:
                        print(f"     ❌ Can't recalculate route — re-planning from scratch")
                    break

        # ── Self-review ──
        if not _pursuit_failed and steps_completed > 0:
            _review_prompt = (
                f"[SELF-REVIEW] Goal: {prompt}\n\n"
                f"All steps completed.\n\n"
                f"Results:\n"
                + "\n".join(f"Step {i+1}:\n{r[:500]}" for i, r in enumerate(_delegation_results))
                + f"\n\n---\n"
                f"Given the goal, are the results sufficient? Answer with:\n"
                f"SCORE: <0-10> (7+ = goal achieved)\n"
                f"REASON: <brief explanation>"
            )
            _review_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": _review_prompt}]
            _review_fb = call_llm_with_fallback(config, _review_msgs, temperature=0.3)
            _review_resp = _review_fb.response
            total_llm_calls += 1
            _cost3 = calculate_cost(model, _review_resp.input_tokens, _review_resp.output_tokens)
            session_cost += _cost3
            record_cost(f"{model.provider}/{model.id}", _review_resp.input_tokens, _review_resp.output_tokens, _cost3)
            _review_text = _review_resp.content or ""

            _score_match = re.search(r'SCORE:\s*(\d+(?:\.\d+)?)', _review_text)
            _score = float(_score_match.group(1)) if _score_match else 0

            if verbose:
                print(f"\n  🔍 Self-review: {steps_completed} steps done, score {_score}/10")

            if _score >= 7:
                _goal_achieved = True
                break
            else:
                if verbose:
                    print(f"     → Score too low, re-planning ({_pursuit}/{_GOAL_PURSUIT_MAX_ATTEMPTS})")
                # Fall through to outer loop re-plan
        else:
            if _pursuit >= _GOAL_PURSUIT_MAX_ATTEMPTS:
                if verbose:
                    print(f"  ❌ All {_GOAL_PURSUIT_MAX_ATTEMPTS} pursuit iterations exhausted")

    # ── After goal loop ──
    if _goal_achieved:
        if verbose:
            print("  ✅ Goal achieved — synthesising final response")
    else:
        if verbose:
            print(f"  ⚠️ Goal not fully achieved after {_GOAL_PURSUIT_MAX_ATTEMPTS} pursuit attempts — synthesising partial results")

    # ── Phase 3c: DeepSeek synthesises final response from delegation results ──
    if _delegation_results:
        _synthesis_prompt = (
            f"[ORCHESTRATOR] All {len(_execution_plan)} steps completed for goal: {prompt[:200]}\n\n"
            f"Results from each step:\n"
            + "\n".join(f"Step {i+1}:\n{r}" for i, r in enumerate(_delegation_results))
            + "\n\n---\n"
            "Verify if the user's goal was FULLY achieved:\n"
            "- If YES: report the final outcome. Be brief.\n"
            "- If NO: say exactly 'NOT DONE: <what's missing>' and STOP.\n"
            "  Do NOT say 'let me go do X' or 'I will now Y' — these are empty promises.\n"
            "  If you can't finish, be honest: state the blocker.\n"
            "- NEVER end with a question. NEVER ask for permission. NEVER promise future action."
        )
        ctx.add_user(_synthesis_prompt)

        fb = call_llm_with_fallback(
            config, ctx.to_openai_messages(),
            temperature=model_temperature,
        )
        response = fb.response
        total_llm_calls += 1
        cost = calculate_cost(model, response.input_tokens, response.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", response.input_tokens, response.output_tokens, cost)
        ctx.add_assistant(response.content, response.tool_calls)

        if verbose:
            print(f"\n[LLM #{total_llm_calls}] {fb.model_used} | Synthesis complete")
    else:
        # No delegation results — fall back to neutral response
        response = neutral_response
        _delegation_failed = True

    # ── Collect final output ──
    assistant_responses = []
    for msg in ctx.messages:
        if hasattr(msg, 'role'):
            role = msg.role
            content = msg.content or ""
        else:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
        if role == "assistant" and content:
            assistant_responses.append(content)

    if assistant_responses:
        final_reply = assistant_responses[-1]
    elif _delegation_results:
        # Synthesis response was empty — auto-generate summary from delegation results
        final_reply = "✅ Completed " + ", ".join(
            r.strip()[:200] for r in _delegation_results[-3:]
        )
    else:
        final_reply = ""

    # ── Build plan recap (route plan + step progress) → Message 1 ──
    plan_recap = ""
    if _display_log:
        plan_recap += "\n".join(_display_log) + "\n"
    if steps_completed > 0:
        plan_recap += dsp.done(steps_completed, len(_execution_plan), 0, 0) + "\n"

    # ── Build findings (actual results + options) → Message 2 ──
    findings = final_reply or ""

    # ── Fact check findings ──
    if findings:
        try:
            from .fact_checker import FactChecker
            fc = FactChecker(config)
            last_text = ""
            if ctx.messages:
                last_msg = ctx.messages[-1]
                if hasattr(last_msg, "content"):
                    last_text = last_msg.content or ""
                else:
                    last_text = last_msg.get("content", "") or ""
            action, fc_result = fc.check(last_text or "", prompt)
            if action == "block":
                findings += f"\n\n{fc_result['message']}"
            elif action == "flag":
                findings += f"\n\n<i>⚠️ {len(fc_result['claims'])} unverified claims flagged</i>"
                try:
                    search_action, search_result = fc.verify_with_search(last_text or "", prompt)
                    if search_action == "block":
                        msg = search_result.get("message", "blocked by web search")
                        findings += f"\n\n{html.italic('Web search: ' + msg)}"
                    elif search_action == "flag" and search_result.get("flagged"):
                        n_flagged = len(search_result["flagged"])
                        findings += f"\n\n{html.italic(f'Web search: {n_flagged} claims unverifiable')}"
                except Exception:
                    pass
        except Exception:
            pass

        findings += f"\n\n{html_cost_summary()}"

    # Auto-save
    last_commit = None
    try:
        last_commit = auto_commit(
            Path.home() / "baw",
            f"agent: {prompt[:80]}"
        )
    except Exception:
        pass

    if last_commit:
        findings += f"\n💾 <i>Auto-saved: {last_commit}</i>"

    try:
        mem.remember(f"User: {prompt[:150]} → BAW: {(final_reply or '')[:150]}")
    except Exception:
        pass

    info = {
        "cost": round(session_cost, 4),
        "model": f"{model.provider}/{model.id}",
        "iterations": total_llm_calls,
        "steps": steps_completed,
        "adversarial": court_result["agreement_level"] if court_result else None,
        "adversarial_raw": court_result,
        "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        "plan_recap": plan_recap.strip(),
        "goal_achieved": _goal_achieved,
    }
    return findings, info
