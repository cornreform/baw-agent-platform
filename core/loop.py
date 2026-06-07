"""
BAW — Agent Loop
Stream → Angel/Devil (Courtroom) → Fact Check → Tool Call → Execute → Loop

Flow per user turn:
  1. Tone detection: detect user wants to switch tone
  2. 👿 Devil speaks (always, no tools)
  3. 😇 Angel responds (with Devil's analysis, has tools)
  4. Devil > Angel ⛔ → STOP
  5. Angel ≥ Angel ✅ → proceed into tool execution loop
  6. Fact check final response before returning to user
  7. Cost meter: show per-call and session cost
"""

from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Optional

from .llm import get_model, call_llm, calculate_cost
from .context import Context
from .tools import get_openai_tools, execute_tool, list_tools
from .permission import PermissionEngine
from .memory import MemoryStore


_COST_ACCUMULATOR: dict = {"total": 0.0, "calls": []}


def record_cost(model_name: str, tokens_in: int, tokens_out: int, cost: float):
    """Record a single LLM call cost."""
    _COST_ACCUMULATOR["calls"].append({
        "model": model_name,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": round(cost, 6),
    })
    _COST_ACCUMULATOR["total"] += cost


def format_cost_summary() -> str:
    """Format a cost summary string for display."""
    total = _COST_ACCUMULATOR["total"]
    n = len(_COST_ACCUMULATOR["calls"])
    if n == 0:
        return ""
    calls_info = " | ".join(
        f"{c['tokens_in']}↑{c['tokens_out']}↓${c['cost']:.4f}"
        for c in _COST_ACCUMULATOR["calls"]
    )
    return f"📊 [{n} LLM calls] {calls_info} | **total: ${total:.4f}**"


def reset_cost():
    """Reset cost accumulator for a new session."""
    _COST_ACCUMULATOR["total"] = 0.0
    _COST_ACCUMULATOR["calls"] = []


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
            "\n## Fact Check Rule\n"
            "When making claims about prices, specs, dates, or statistics, "
            "always cite your source or use the `web_search` tool to verify first. "
            "If you cannot find a source, mark the claim as (unsourced)."
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
        f"- Cost transparency: per-call cost shown after each response"
    )

    return system_prompt


def format_verdict_for_user(verdict: dict) -> str:
    """Format the adversarial court output for display."""
    devil = verdict["devil"]
    angel = verdict["angel"]
    lines = []

    # Devil
    lines.append(f"👿 **Devil (Opposing Counsel)** — Risk: {devil['score']}/10")
    lines.append(devil["content"])
    lines.append("")

    # Angel
    lines.append(
        f"😇 **Angel (Executor)** — Feasibility: {angel['score']}/10"
    )

    # Decision
    if verdict["should_stop"]:
        lines.append(
            f"\n⚠️ **Devil ({devil['score']}/10) > Angel ({angel['score']}/10)**\n"
            f"⛔ BAW has significant concerns. Stopped before any action.\n"
        )
    elif verdict["decision"] == "warn":
        lines.append(
            f"\n⚠️ Devil ({devil['score']}/10) close to Angel ({angel['score']}/10)"
            f" — proceeding with caution\n"
        )
    else:
        lines.append(f"\n━━━ Proceeding ───\n")

    # Add Angel's actual response
    lines.append(angel["content"])

    return "\n".join(lines)


def run_agent(
    prompt: str,
    config: dict,
    model_id: Optional[str] = None,
    data_dir: Optional[Path] = None,
    verbose: bool = False,
    interactive: bool = False,
) -> tuple[str, dict]:
    """
    Run BAW agent with a single prompt.

    Returns: (response_text, info_dict)
    """
    # ── Initialise ──
    model = get_model(config, model_id)
    perm = PermissionEngine(config)
    mem = MemoryStore(data_dir or Path.home() / ".baw")
    system_prompt = build_system_prompt(config, data_dir)

    # ── Detect tone switch from user message ──
    from .tone import detect_tone_switch, format_tone_confirmation
    old_tone = config.get("tone", {}).get("default", "casual")
    new_tone = detect_tone_switch(prompt)
    tone_change = False
    if new_tone and new_tone != old_tone:
        config.setdefault("tone", {})["default"] = new_tone
        system_prompt = build_system_prompt(config, data_dir)
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

    # ── Phase 1: Conversation context ──
    ctx = Context(system_prompt=system_prompt, temperature=0.7)
    ctx.add_user(prompt)
    if mem_text:
        ctx.add_user(f"Relevant memories:\n{mem_text}")

    # ── Phase 2: Adversarial Court ──
    adv_cfg = config.get("adversarial", {})
    court_enabled = adv_cfg.get("enabled", True)

    devil_output = None
    angel_response = None
    angel_content = ""
    verdict = None

    if court_enabled:
        from .adversarial import AdversarialCourt

        court = AdversarialCourt(model, system_prompt, config)
        verdict = court.hold_court(prompt, mem_text)

        devil_output = verdict["devil"]
        angel_response = verdict["angel"]["response"]
        angel_content = verdict["angel"]["content"]
        session_cost += devil_output["cost"]
        session_cost += verdict["angel"]["cost"]
        record_cost(
            f"{model.provider}/{model.id}",
            devil_output["tokens_in"],
            devil_output["tokens_out"],
            devil_output["cost"],
        )
        record_cost(
            f"{model.provider}/{model.id}",
            verdict["angel"]["tokens_in"],
            verdict["angel"]["tokens_out"],
            verdict["angel"]["cost"],
        )

        if verbose:
            print(f"\n[Court] Devil={devil_output['score']} Angel={verdict['angel']['score']} → {verdict['decision']}")

        # Build initial output
        output_parts = []

        # Tone change notification
        if tone_change:
            output_parts.append(format_tone_confirmation(old_tone, new_tone))

        # Adversarial output
        court_output = format_verdict_for_user(verdict)
        output_parts.append(court_output)

        if verdict["should_stop"]:
            # ⛔ Devil wins — return immediately, no tool execution
            base_info = {
                "cost": round(session_cost, 4),
                "model": f"{model.provider}/{model.id}",
                "iterations": 0,
                "adversarial": "flagged",
                "tone_switch": new_tone if tone_change else None,
            }
            return "\n\n".join(output_parts), base_info

        # ✅ Proceed — add Angel's response to context
        ctx.add_assistant(angel_response.content, angel_response.tool_calls)

        # Determine the combined output so far
        output = "\n\n".join(output_parts)

        # If no tool calls, apply fact check and return
        if not angel_response.tool_calls:
            # Fact check final response
            from .fact_checker import FactChecker
            fc = FactChecker(config)
            action, fc_result = fc.check(angel_content, prompt)
            if action == "block":
                output += f"\n\n{fc_result['message']}"
            elif action == "flag":
                output += f"\n\n_⚠️ Note: {len(fc_result['claims'])} unverified claims flagged_"
                if verbose:
                    for c in fc_result["claims"][:3]:
                        output += f"\n- `{c['claim']}`"

            # Cost summary
            output += f"\n\n{format_cost_summary()}"

            base_info = {
                "cost": round(session_cost, 4),
                "model": f"{model.provider}/{model.id}",
                "iterations": 1,
                "adversarial": verdict["decision"],
                "tone_switch": new_tone if tone_change else None,
                "fact_check": action,
            }
            return output, base_info

        response = angel_response
    else:
        # No court — direct LLM call
        response = call_llm(
            model,
            ctx.to_openai_messages(),
            tools=get_openai_tools(),
        )

        cost = calculate_cost(model, response.input_tokens, response.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", response.input_tokens, response.output_tokens, cost)

        if verbose:
            print(f"\n[BAW {model.provider}/{model.id}] tokens: {response.input_tokens}↑ {response.output_tokens}↓ ${cost:.4f}")

        output_parts = []
        if tone_change:
            output_parts.append(format_tone_confirmation(old_tone, new_tone))

        output = "\n\n".join(output_parts)
        angel_content = response.content or ""

        if not response.tool_calls:
            # Fact check + cost
            from .fact_checker import FactChecker
            fc = FactChecker(config)
            action, fc_result = fc.check(angel_content, prompt)
            if action == "block":
                output += f"\n{fc_result['message']}"
            elif action == "flag" and fc_result.get("annotated"):
                output += f"\n{fc_result['annotated']}"
            else:
                output += f"\n{angel_content}"
            output += f"\n\n{format_cost_summary()}"

            return output, {
                "cost": round(session_cost, 4),
                "model": f"{model.provider}/{model.id}",
                "iterations": 1,
                "fact_check": action,
                "tone_switch": new_tone if tone_change else None,
            }

        ctx.add_assistant(response.content, response.tool_calls)
        output += f"\n{angel_content}"

    # ── Phase 3: Tool Execution Loop ──
    max_iterations = 15
    total_llm_calls = 1 if court_enabled else 1
    had_tool_calls = True

    # Build running output string
    if court_enabled and verdict:
        final_output = format_verdict_for_user(verdict)
        if tone_change:
            final_output = format_tone_confirmation(old_tone, new_tone) + "\n\n" + final_output
    else:
        final_output = output

    for iteration in range(max_iterations):
        # Execute tool calls
        for tc in response.tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            raw_args = func.get("arguments", "{}")

            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}

            # Permission check
            perm_result = perm.check(name, args)

            if perm_result["decision"] == "deny":
                result = f"[Permission DENIED] {perm_result['reason']}"
                ctx.add_assistant("", [tc])
                ctx.add_tool_result(tc.get("id", ""), name, result)
                if verbose:
                    print(f"  ⛔ {name}(...) → DENIED: {perm_result['reason']}")
                continue

            if perm_result["decision"] == "prompt":
                if interactive:
                    try:
                        ans = input(f"\n⚠️  {perm_result['reason']}\n→ ")
                    except (EOFError, KeyboardInterrupt):
                        ans = "n"
                    if ans.lower() in ("y", "yes", "s"):
                        if ans.lower() == "s":
                            perm.session_allow(name, args)
                        result = execute_tool(name, args)
                    else:
                        result = f"[Permission DENIED] User declined"
                        perm.session_deny(name, args)
                else:
                    # Non-interactive: allow medium risk by default
                    result = execute_tool(name, args)
            else:
                result = execute_tool(name, args)

            if verbose:
                short = result[:200].replace("\n", " ")
                print(f"  🛠️  {name}({json.dumps(args, ensure_ascii=False)[:80]}) → {short}...")

            ctx.add_assistant("", [tc])
            ctx.add_tool_result(tc.get("id", ""), name, result)

        # Next LLM call with tool results
        response = call_llm(
            model,
            ctx.to_openai_messages(),
            tools=get_openai_tools(),
        )
        total_llm_calls += 1

        cost = calculate_cost(model, response.input_tokens, response.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", response.input_tokens, response.output_tokens, cost)

        if verbose:
            print(f"\n[LLM #{total_llm_calls}] tokens: {response.input_tokens}↑ {response.output_tokens}↓ ${cost:.4f}")

        ctx.add_assistant(response.content, response.tool_calls)

        if not response.tool_calls:
            # Done with tool loop
            # Collect remaining assistant messages after tool calls
            extra_responses = []
            for msg in reversed(ctx.messages):
                if msg.get("role") == "tool":
                    break
                if msg.get("role") == "assistant" and msg.get("content"):
                    extra_responses.append(msg["content"])

            # Add extra LLM responses to output (skip the first one already shown)
            if len(extra_responses) > 1:
                for extra in reversed(extra_responses[:-1]):
                    final_output += f"\n\n{extra}"
            elif extra_responses:
                last_content = extra_responses[-1]
                # Only add if different from what's already shown
                if last_content and last_content not in final_output:
                    final_output += f"\n\n{last_content}"

            # Fact check final output
            from .fact_checker import FactChecker
            fc = FactChecker(config)
            final_text = ctx.messages[-1].get("content", "") if ctx.messages else response.content or ""
            action, fc_result = fc.check(final_text, prompt)
            if action == "block":
                final_output += f"\n\n{fc_result['message']}"
            elif action == "flag":
                final_output += f"\n\n_⚠️ {len(fc_result['claims'])} unverified claims flagged_"

            # Cost summary
            final_output += f"\n\n{format_cost_summary()}"

            # Save to memory
            try:
                mem.remember(f"User asked: {prompt[:200]}")
            except Exception:
                pass

            info = {
                "cost": round(session_cost, 4),
                "model": f"{model.provider}/{model.id}",
                "iterations": total_llm_calls,
                "adversarial": verdict["decision"] if verdict else None,
                "fact_check": action,
                "tone_switch": new_tone if tone_change else None,
            }
            return final_output, info

        # Compact context if too large
        if ctx.count_tokens_approx() > model.context_window * 0.7:
            ctx.messages = ctx.messages[:1] + ctx.messages[-10:]
            if verbose:
                print("  [Context compacted]")

    max_msg = "Max iterations reached (15)."
    return final_output + f"\n\n⚠️ {max_msg}", {
        "cost": round(session_cost, 4),
        "model": f"{model.provider}/{model.id}",
        "iterations": total_llm_calls,
    }
