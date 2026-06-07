"""
BAW — Self-Improving Agent Loop

Core philosophy: BAW NEVER asks the user "can this be done" or "how should I choose".
Instead, BAW:
  1. Plan: Analyse goal → create step plan
  2. Execute each step with Angel/Devil self-check
  3. On failure: retry → replan → rollback (never ask user)
  4. Only contact user when truly stuck after all alternatives exhausted

Flow per user turn:
  Phase 1 — Plan
    Angel generates step plan, Devil reviews
  Phase 2 — Execute (each step)
    → Checkpoint save
    → Devil challenges step
    → Angel decides
    → Execute tool(s)
    → Verify result
    → Success ? commit : recover (retry/replan/rollback)
  Phase 3 — Report
    What was done, what worked, summary
"""

from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Optional

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
        """HTML version for Telegram-friendly reporting."""
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


# ── Helper functions that use the tracker ──
# These exist for backward compatibility with existing call sites.
# New code should create a CostTracker instance directly.

_TRACKER: CostTracker | None = None


def _get_tracker() -> CostTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = CostTracker()
    return _TRACKER


def record_cost(model_name: str, tokens_in: int, tokens_out: int, cost: float):
    _get_tracker().record(model_name, tokens_in, tokens_out, cost)


def format_cost_summary() -> str:
    """Markdown cost summary (used within agent context)."""
    return _get_tracker().summary()


def html_cost_summary() -> str:
    """HTML cost summary (for user-facing output)."""
    return _get_tracker().html_summary()


def reset_cost():
    _get_tracker().reset()


# ── System prompt ──────────────────────────────────────────────

def build_system_prompt(config: dict, data_dir: Optional[Path] = None) -> str:
    """Build the Angel's system prompt from SOUL.md + dynamic context."""
    base_path = data_dir or Path.home() / ".baw"
    soul_path = base_path / "SOUL.md"

    if soul_path.exists():
        system_prompt = soul_path.read_text(encoding="utf-8")
    else:
        system_prompt = (
            "You are BAW (Black And White), Sunny's agent platform.\n"
            "Respond in Traditional Chinese (Cantonese).\n"
            "Be concise, lead with results.\n"
            "Never ask the user what to do — figure it out yourself."
        )

    # Dynamic context
    tone = config.get("tone", {}).get("default", "casual")
    fact_mode = config.get("fact_check", {}).get("mode", "normal")
    tools_list = ", ".join(t.name for t in list_tools())

    system_prompt += (
        f"\n\n## Dynamic context\n"
        f"- Current tone: {tone}\n"
        f"- Fact check mode: {fact_mode}\n"
        f"- Available tools: {tools_list}\n"
        f"- Cost transparency: per-call cost shown after each response\n"
        f"- Core rule: NEVER ask the user what to do. Analyse, plan, execute, recover."
    )

    return system_prompt


# ── Adversarial Court (step-level) ─────────────────────────────

def _run_step_court(
    court, step_desc: str, context_summary: str,
) -> dict:
    """Run a lightweight adversarial check for a single step.

    Returns verdict dict: {should_stop, decision, devil_score, angel_score}.
    """
    from .adversarial import AdversarialCourt
    return court.hold_court(step_desc, context_summary)


def _should_proceed(verdict: dict, max_retries: int = 3) -> bool:
    """Determine if BAW should proceed or recover based on verdict."""
    if verdict.get("should_stop"):
        return False
    return True


# ── Main agent loop ────────────────────────────────────────────

MAX_STEP_RETRIES = 3
MAX_CONSECUTIVE_FAILURES = 3


def run_agent(
    prompt: str,
    config: dict,
    model_id: Optional[str] = None,
    data_dir: Optional[Path] = None,
    verbose: bool = False,
    interactive: bool = False,
) -> tuple[str, dict]:
    """Run BAW agent with self-improving loop.

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

    # Init adversarial court (for step-level checks)
    system_prompt = build_system_prompt(config, data_dir)
    from .adversarial import AdversarialCourt
    court = AdversarialCourt(model, system_prompt, config)

    # ── Detect tone switch ──
    from .tone import detect_tone_switch, format_tone_confirmation
    old_tone = config.get("tone", {}).get("default", "casual")
    new_tone = detect_tone_switch(prompt)
    tone_change = False
    if new_tone and new_tone != old_tone:
        config.setdefault("tone", {})["default"] = new_tone
        system_prompt = build_system_prompt(config, data_dir)
        court = AdversarialCourt(model, system_prompt, config)
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

    # ── Phase 1: Build context ──
    ctx = Context(system_prompt=system_prompt, temperature=0.7)
    ctx.add_user(prompt)
    if mem_text:
        ctx.add_user(f"Relevant memories:\n{mem_text}")

    # ── Adversarial Court (goal-level) ──
    adv_cfg = config.get("adversarial", {})
    court_enabled = adv_cfg.get("enabled", True)

    devil_output = None
    verdict = None

    if court_enabled:
        verdict = court.hold_court(prompt, mem_text)
        devil_output = verdict.get("devil")
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

        if verbose:
            print(f"\n[Court] Devil={verdict['devil_score']} Angel={verdict['angel_score']} → {verdict['decision']}")

        if verdict.get("should_stop"):
            # ⛔ Goal blocked — explain why
            output = ""
            if tone_change:
                output += format_tone_confirmation(old_tone, new_tone) + "\n\n"
            output += _format_verdict(verdict)
            return output, {
                "cost": round(session_cost, 4),
                "model": f"{model.provider}/{model.id}",
                "iterations": 0,
                "adversarial": "flagged",
            }

        # ✅ Goal approved — add Angel's first response to context
        angel_response = verdict["angel"]["response"]
        ctx.add_assistant(angel_response.content, angel_response.tool_calls)

    else:
        # No court — direct call
        fb = call_llm_with_fallback(
            config, ctx.to_openai_messages(),
            tools=get_openai_tools(), temperature=0.7,
        )
        angel_response = fb.response
        cost = calculate_cost(model, fb.response.input_tokens, fb.response.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", fb.response.input_tokens, fb.response.output_tokens, cost)
        ctx.add_assistant(angel_response.content, angel_response.tool_calls)

    # ── Phase 2: Self-Improving Execution Loop ──
    # If no tool calls from the Angel's first response, we're done
    if not angel_response.tool_calls:
        output = ""
        if tone_change:
            output += format_tone_confirmation(old_tone, new_tone) + "\n\n"
        output += (angel_response.content or "")
        output += f"\n\n{format_cost_summary()}"

        # Save to memory
        try:
            mem.remember(f"User asked: {prompt[:200]}")
        except Exception:
            pass

        return output, {
            "cost": round(session_cost, 4),
            "model": f"{model.provider}/{model.id}",
            "iterations": 1,
        }

    # Build output
    output_parts = []
    if tone_change:
        output_parts.append(format_tone_confirmation(old_tone, new_tone))
    if verdict:
        output_parts.append(_format_verdict(verdict))
    output = "\n\n".join(output_parts) + "\n\n"

    # ── Step-by-step execution ──
    max_iterations = 20
    response = angel_response
    total_llm_calls = 1
    consecutive_failures = 0
    _current_strategies: list[str] = []
    steps_completed = 0

    for iteration in range(max_iterations):
        if not response.tool_calls:
            break

        # Execute each tool call as a step
        for tc in response.tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            raw_args = func.get("arguments", "{}")

            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}

            # ── Permission check (built-in, no user question) ──
            perm_result = perm.check(name, args)
            if perm_result["decision"] == "deny":
                # Don't ask user — just report the block and move on
                result = f"[BLOCKED] {perm_result['reason']}"
                ctx.add_tool_result(tc.get("id", ""), name, result)
                consecutive_failures += 1
                if verbose:
                    print(f"  ⛔ {name} → BLOCKED: {perm_result['reason']}")
                continue

            # ── Save checkpoint before state-changing ops ──
            checkpointer.save(
                ctx.messages, tool_name=name, tool_args=args,
            )

            # ── Record file history for write operations ──
            _file_history = FileHistory(data_dir)
            if name == "write_file" and "path" in args and "content" in args:
                _file_history.record_write(args["path"], args["content"],
                    action="create" if not Path(args["path"]).expanduser().exists() else "update",
                    metadata={"source": "agent_tool", "step": checkpointer._step_count})

            # ── Execute tool ──
            if perm_result["decision"] == "allow":
                exe_result = execute_tool(name, args)
            else:
                # Medium risk: auto-allow (never prompt user in self-improving mode)
                exe_result = execute_tool(name, args)

            step_success = not exe_result.startswith("[Error") and not exe_result.startswith("[BLOCKED")

            if verbose:
                short = exe_result[:200].replace("\n", " ")
                print(f"  🛠️  {name}(...) → {'✅' if step_success else '❌'} {short}...")

            # ── Handle step result with strategy-based recovery ──
            if step_success:
                # Commit checkpoint, record success
                checkpointer.commit()
                consecutive_failures = 0
                steps_completed += 1
                _current_strategies.clear()

                ctx.add_tool_result(tc.get("id", ""), name, exe_result)

                # Auto-commit after successful write operation
                if name == "write_file":
                    try:
                        auto_commit(
                            data_dir or Path.home() / ".baw",
                            f"write: {args.get('path', '?')}"
                        )
                    except Exception:
                        pass
                elif name == "bash" and "git commit" in args.get("command", ""):
                    try:
                        auto_commit(data_dir or Path.home() / ".baw", "git operation")
                    except Exception:
                        pass
            else:
                # Step failed — strategy-based recovery
                consecutive_failures += 1
                checkpointer.record_attempt()

                # Track the strategy that failed
                strategy_desc = f"{name}({args.get('path', args.get('command', '?'))})"
                _current_strategies.append(strategy_desc)
                checkpointer.record_strategy(strategy_desc)

                # Add the failure to context
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    # All strategies exhausted — notify user with analysis
                    error_summary = (
                        f"<b>🔄 BAW Strategy Recovery</b>\n\n"
                        f"After {len(_current_strategies)} strategy attempts, "
                        f"this approach is not working:\n"
                        f"{'<br>'.join(f'❌ {s}' for s in _current_strategies)}\n\n"
                    )

                    # Check if we should try a completely different approach
                    if consecutive_failures < MAX_CONSECUTIVE_FAILURES * 2:
                        error_summary += (
                            f"<b>Changing strategy to a different approach...</b>\n"
                            f"Previous approaches blocked/already tried."
                        )
                        _current_strategies.clear()
                    else:
                        error_summary += (
                            f"<b>⛔ All approaches exhausted.</b>\n"
                            f"Angel/Devil evaluation required for next steps."
                        )

                    if not output.endswith("\n"):
                        output += "\n"
                    output += error_summary + "\n"
                    consecutive_failures = 0
                    break

        # ── Next LLM call ──
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            consecutive_failures = 0

        fb_result = call_llm_with_fallback(
            config, ctx.to_openai_messages(),
            tools=get_openai_tools(), temperature=0.7,
        )
        response = fb_result.response
        total_llm_calls += 1

        cost = calculate_cost(model, response.input_tokens, response.output_tokens)
        session_cost += cost
        record_cost(
            f"{model.provider}/{model.id}",
            response.input_tokens, response.output_tokens, cost,
        )

        if verbose:
            print(f"\n[LLM #{total_llm_calls}] {fb_result.model_used} | tokens: {response.input_tokens}↑ {response.output_tokens}↓ ${cost:.4f}")

        ctx.add_assistant(response.content, response.tool_calls)

        # Compact context if needed
        if ctx.count_tokens_approx() > model.context_window * 0.7:
            ctx.messages = ctx.messages[:1] + ctx.messages[-10:]
            if verbose:
                print("  [Context compacted]")

    # ── Phase 3: Collect output ──
    # Gather all assistant responses from the tool loop
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

    # Add the final response
    if assistant_responses:
        final_reply = assistant_responses[-1]
        if final_reply and final_reply not in output:
            output += f"\n{final_reply}"

        # ── Fact check final output (with web search verification) ──
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
            # First-pass regex check
            action, fc_result = fc.check(last_text or "", prompt)
            if action == "block":
                output += f"\n\n{fc_result['message']}"
            elif action == "flag":
                output += f"\n\n<i>⚠️ {len(fc_result['claims'])} unverified claims flagged</i>"
                # Second-pass: web search verification
                try:
                    search_action, search_result = fc.verify_with_search(last_text or "", prompt)
                    if search_action == "block":
                        msg = search_result.get("message", "blocked by web search")
                        output += f"\n\n{html.italic('Web search: ' + msg)}"
                    elif search_action == "flag" and search_result.get("flagged"):
                        n_flagged = len(search_result["flagged"])
                        output += f"\n\n{html.italic(f'Web search: {n_flagged} claims unverifiable')}"
                except Exception:
                    pass  # web search check is best-effort
        except Exception:
            pass

        # Cost summary (HTML format)
        output += f"\n\n{html_cost_summary()}"

    # Auto-save: commit any changes
    last_commit = None
    try:
        last_commit = auto_commit(
            Path.home() / "baw",
            f"agent: {prompt[:80]}"
        )
    except Exception:
        pass

    # If auto-committed, add to report
    if last_commit:
        output += f"\n💾 <i>Auto-saved: {last_commit}</i>"

    # Save to memory
    try:
        mem.remember(f"User asked: {prompt[:200]}")
    except Exception:
        pass

    info = {
        "cost": round(session_cost, 4),
        "model": f"{model.provider}/{model.id}",
        "iterations": total_llm_calls,
        "steps": steps_completed,
        "adversarial": verdict["decision"] if verdict else None,
    }
    return output, info


def _format_verdict(verdict: dict) -> str:
    """Format adversarial court output in HTML (Telegram-compatible)."""
    devil = verdict.get("devil")
    angel = verdict.get("angel")
    if not devil or not angel:
        return ""

    lines = [
        f"👿 <b>Devil (Opposing Counsel)</b> — Risk: {devil['score']}/10",
        html.blockquote(devil["content"]),
        "",
        f"😇 <b>Angel (Executor)</b> — Feasibility: {angel['score']}/10",
    ]
    if verdict.get("should_stop"):
        lines.append(
            f"\n⚠️ <b>Devil ({devil['score']}/10) &gt; Angel ({angel['score']}/10)</b>\n"
            f"⛔ BAW has significant concerns. Stopped before any action.\n"
        )
    elif verdict.get("decision") == "warn":
        lines.append(
            f"\n⚠️ Devil close to Angel — proceeding with caution\n"
        )
    else:
        lines.append(f"\n━━━ Proceeding ───\n")

    lines.append(angel["content"])
    return "\n".join(lines)
