#!/usr/bin/env python3
"""Benchmark BAW's 3 execution modes: quick / hybrid / tight.

Tests a simple 2-tool-call task under each mode and reports:
  - Total LLM calls
  - Total tokens
  - Wall time
  - Cost
"""

import sys, os, time
sys.path.insert(0, os.path.expanduser("~/baw"))
from pathlib import Path
from core.llm import load_config, get_model, call_llm_with_fallback, calculate_cost
from core.context import Context
from core.tools import get_openai_tools, list_tools, register_all

# Register all tools
register_all()

# Load config
config = load_config()
model = get_model(config)
DATA_DIR = Path.home() / ".baw"

# Load soul
soul_path = DATA_DIR / "SOUL.md"
soul = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""

# Choose a prompt that triggers tool calls
TEST_PROMPT = "check on ~/baw/ if there are uncommitted git changes"

# Build common system prompt
tools_list = [t.name for t in list_tools()]

def build_system():
    return soul + (
        f"\n\n## Dynamic context\n"
        f"- Current tone: casual\n"
        f"- Fact check mode: normal\n"
        f"- Available tools: {', '.join(tools_list)}\n"
        f"- Cost transparency: per-call cost shown after each response\n"
        f"- Core rule: NEVER ask the user what to do."
    )

def count_llm_calls(messages):
    """Count LLM calls from cost entries."""
    return len(messages)

def run_mode(mode_name: str) -> dict:
    """Run a single agent call in the given mode and measure it."""
    from core.adversarial import AdversarialCourt
    from core.permission import PermissionEngine
    from core.memory import MemoryStore
    from core.checkpoint import Checkpointer
    from core.file_history import FileHistory
    from core.autosave import auto_commit
    from core.tone import detect_tone_switch, format_tone_confirmation

    system_prompt = build_system()
    mem = MemoryStore(DATA_DIR)
    perm = PermissionEngine(config)
    checkpointer = Checkpointer()

    court = AdversarialCourt(model, system_prompt, config)
    from core import render as html
    from core.loop import build_system_prompt, format_cost_summary, record_cost, reset_cost

    reset_cost()
    session_cost = 0.0

    _mode = mode_name
    
    # ── Phase 1: Build context ──
    ctx = Context(system_prompt=system_prompt, temperature=0.7)
    ctx.add_user(TEST_PROMPT)
    
    llm_calls = 0
    
    # ── Quick Mode ──
    if _mode == "quick":
        t0 = time.time()
        fb = call_llm_with_fallback(config, ctx.to_openai_messages(),
                                     tools=get_openai_tools(), temperature=0.7)
        resp = fb.response
        llm_calls += 1
        cost = calculate_cost(model, resp.input_tokens, resp.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", resp.input_tokens, resp.output_tokens, cost)
        ctx.add_assistant(resp.content, resp.tool_calls)
        
        tool_iterations = 0
        while resp.tool_calls:
            tool_iterations += 1
            for tc in resp.tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try: args = json.loads(raw_args)
                except: args = {}
                perm_result = perm.check(name, args)
                if perm_result["decision"] == "deny":
                    ctx.add_tool_result(tc.get("id", ""), name, f"[BLOCKED] {perm_result['reason']}")
                    continue
                exe_result = "ok"
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)
            fb = call_llm_with_fallback(config, ctx.to_openai_messages(),
                                         tools=get_openai_tools(), temperature=0.7)
            resp = fb.response
            llm_calls += 1
            cost = calculate_cost(model, resp.input_tokens, resp.output_tokens)
            session_cost += cost
            record_cost(f"{model.provider}/{model.id}", resp.input_tokens, resp.output_tokens, cost)
            ctx.add_assistant(resp.content, resp.tool_calls)
        
        elapsed = time.time() - t0
        total_tokens = sum(
            (m.get("tokens_in", 0) if isinstance(m, dict) else 0) for m in ctx.messages
        )
        return {
            "mode": mode_name,
            "llm_calls": llm_calls,
            "tool_iterations": tool_iterations,
            "cost": round(session_cost, 6),
            "time": round(elapsed, 2),
            "message_count": len(ctx.messages),
        }

    # ── Tight / Hybrid: Court (tight only) ──
    adv_cfg = config.get("adversarial", {})
    court_enabled = adv_cfg.get("enabled", True)
    verdict = None
    
    t0 = time.time()
    
    if court_enabled and _mode != "hybrid":
        verdict = court.hold_court(TEST_PROMPT, "")
        session_cost += (verdict["devil"]["cost"] + verdict["angel"]["cost"])
        record_cost(f"{model.provider}/{model.id}", verdict["devil"]["tokens_in"], verdict["devil"]["tokens_out"], verdict["devil"]["cost"])
        record_cost(f"{model.provider}/{model.id}", verdict["angel"]["tokens_in"], verdict["angel"]["tokens_out"], verdict["angel"]["cost"])
        angel_response = verdict["angel"]["response"]
        ctx.add_assistant(angel_response.content, angel_response.tool_calls)
        llm_calls = 2  # Devil + Angel
    else:
        fb = call_llm_with_fallback(config, ctx.to_openai_messages(),
                                     tools=get_openai_tools(), temperature=0.7)
        angel_response = fb.response
        cost = calculate_cost(model, fb.response.input_tokens, fb.response.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", fb.response.input_tokens, fb.response.output_tokens, cost)
        ctx.add_assistant(angel_response.content, angel_response.tool_calls)
        llm_calls = 1
    
    if not angel_response.tool_calls:
        elapsed = time.time() - t0
        return {
            "mode": mode_name,
            "llm_calls": llm_calls,
            "tool_iterations": 0,
            "cost": round(session_cost, 6),
            "time": round(elapsed, 2),
            "message_count": len(ctx.messages),
            "note": "no tool calls needed",
        }
    
    # ── Plan (skip only for quick, which already returned) ──
    total_llm_calls = llm_calls
    plan_prompt_text = (
        f"[ORCHESTRATOR] Goal: {TEST_PROMPT}\n\n"
        f"Write a detailed step-by-step execution plan. "
        f"Each step must be SMALL (max 1-2 tool calls).\n"
        f"Format each step as:\n"
        f"  Step N: <action> — <expected tool> — <expected outcome>\n\n"
        f"Reply with ONLY the plan, numbered 1..N."
    )
    plan_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": plan_prompt_text}]
    plan_fb = call_llm_with_fallback(config, plan_msgs, temperature=None)
    plan_response = plan_fb.response
    total_llm_calls += 1
    cost = calculate_cost(model, plan_response.input_tokens, plan_response.output_tokens)
    session_cost += cost
    record_cost(f"{model.provider}/{model.id}", plan_response.input_tokens, plan_response.output_tokens, cost)
    
    import re as _re
    plan_text = plan_response.content or ""
    execution_plan = []
    for _line in plan_text.split("\n"):
        _m = _re.match(r'\s*(?:Step\s*)?(\d+)[.:]\s*(.*)', _line.strip())
        if _m:
            execution_plan.append({"num": int(_m.group(1)), "desc": _m.group(2)})
    if not execution_plan:
        execution_plan.append({"num": 1, "desc": plan_text[:200]})
    
    # Append plan to context
    plan_for_ctx = (
        "\n[Execution Plan]\n" +
        "\n".join(f"  Step {s['num']}: {s['desc']}" for s in execution_plan) +
        "\n\n[Progress: nothing completed yet]"
    )
    ctx.system_prompt += plan_for_ctx
    
    # ── Execute ──
    response = angel_response
    consecutive_failures = 0
    steps_completed = 0
    current_plan_step = 0
    tool_iterations = 0
    MAX_FAIL = 3
    if _mode == "hybrid":
        MAX_FAIL = 1
    
    for iteration in range(40):
        if not response.tool_calls:
            break
        tool_iterations += 1
        for tc in response.tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            raw_args = func.get("arguments", "{}")
            try: args = json.loads(raw_args)
            except: args = {}
            perm_result = perm.check(name, args)
            if perm_result["decision"] == "deny":
                ctx.add_tool_result(tc.get("id", ""), name, f"[BLOCKED] {perm_result['reason']}")
                consecutive_failures += 1
                continue
            
            exe_result = "ok"
            step_success = True
            if step_success:
                consecutive_failures = 0
                steps_completed += 1
                if current_plan_step < len(execution_plan):
                    current_plan_step += 1
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)
            else:
                consecutive_failures += 1
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)
                if consecutive_failures >= MAX_FAIL:
                    break
        
        if consecutive_failures >= MAX_FAIL:
            consecutive_failures = 0
            break
        
        # Next LLM call
        fb = call_llm_with_fallback(config, ctx.to_openai_messages(),
                                     tools=get_openai_tools(), temperature=0.7)
        response = fb.response
        total_llm_calls += 1
        cost = calculate_cost(model, response.input_tokens, response.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", response.input_tokens, response.output_tokens, cost)
        ctx.add_assistant(response.content, response.tool_calls)
    
    elapsed = time.time() - t0
    return {
        "mode": mode_name,
        "llm_calls": total_llm_calls,
        "tool_iterations": tool_iterations,
        "steps": steps_completed,
        "plan_steps": len(execution_plan),
        "cost": round(session_cost, 6),
        "time": round(elapsed, 2),
        "message_count": len(ctx.messages),
    }


print("═══ BAW 3-Mode Benchmark ═══")
print(f"Prompt: {TEST_PROMPT}\n")

results = []
for mode in ["quick", "hybrid", "tight"]:
    print(f"─── Mode: {mode.upper()} ───")
    r = run_mode(mode)
    results.append(r)
    for k, v in r.items():
        print(f"  {k}: {v}")
    print()

print("═══ Comparison ═══")
print(f"{'Mode':<10} {'LLM calls':<12} {'Iterations':<12} {'Cost':<12} {'Time':<8} {'Messages':<10}")
print("-" * 64)
for r in results:
    print(f"{r['mode']:<10} {r['llm_calls']:<12} {r.get('tool_iterations', 0):<12} ${r['cost']:<8.5f} {r['time']:<7.1f}s {r['message_count']:<10}")
