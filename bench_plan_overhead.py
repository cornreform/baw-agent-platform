#!/usr/bin/env python3
"""Benchmark the overhead of Orchestrator + Executor planning.

Simulates a realistic 2-step task and compares:
  - Current flow (court + plan + execute)
  - What the old flow would cost (court + direct execute, no plan)
"""

import sys, os, time
sys.path.insert(0, os.path.expanduser("~/baw"))
from pathlib import Path
from core.llm import get_model, call_llm_with_fallback, calculate_cost, FallbackResult
from core.context import Context
from core.tools import get_openai_tools, list_tools
from core.adversarial import AdversarialCourt
from core import render as html

DATA_DIR = Path.home() / ".baw"

def load_config():
    import yaml
    path = DATA_DIR / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)

def load_soul():
    path = DATA_DIR / "SOUL.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""

# ---------- Estimate plan call cost ----------

def benchmark_plan():
    """Measure tokens & time for just the plan generation step."""
    config = load_config()
    model = get_model(config)
    soul = load_soul()

    test_prompts = [
        "檢查 ~/baw/ 目錄有幾多行 code，列出最多的 3 個檔案",
        "在 ~/baw/ 建立一個新的 tools/math_tool.py，可以 add/sub/mul/div",
        "搜尋網上有關 ESPHome Voice Assistant 的最新 blog post，摘錄重點",
        "讀取 ~/baw/core/loop.py 頭 50 行，總結 loop 嘅運作方式",
        "用 bash 執行 pip list —format=freeze，filter 出 numpy, torch, transformers 嘅版本號",
    ]

    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0
    total_time = 0.0

    for prompt in test_prompts:
        system_prompt = soul + (
            f"\n\n## Dynamic context\n"
            f"- Current tone: casual\n"
            f"- Fact check mode: normal\n"
            f"- Available tools: {', '.join(t.name for t in list_tools())}\n"
            f"- Cost transparency: per-call cost shown after each response\n"
            f"- Core rule: NEVER ask the user what to do."
        )

        plan_msg = (
            f"[ORCHESTRATOR] Goal: {prompt}\n\n"
            f"Write a detailed step-by-step execution plan. "
            f"Each step must be SMALL (max 1-2 tool calls).\n"
            f"Format each step as:\n"
            f"  Step N: <action> — <expected tool> — <expected outcome>\n\n"
            f"Reply with ONLY the plan, numbered 1..N."
        )

        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": plan_msg},
        ]

        t0 = time.time()
        fb = call_llm_with_fallback(
            config, msgs,
            temperature=None,
        )
        elapsed = time.time() - t0
        resp = fb.response

        cost = calculate_cost(model, resp.input_tokens, resp.output_tokens)
        total_tokens_in += resp.input_tokens
        total_tokens_out += resp.output_tokens
        total_cost += cost
        total_time += elapsed

        lines = (resp.content or "").strip().split("\n")
        n_steps = sum(1 for l in lines if l.strip() and l.strip()[0].isdigit())
        print(f"  {prompt[:50]}... → {n_steps} steps  |  {resp.input_tokens:>5}↑ {resp.output_tokens:>5}↓  ${cost:.5f}  {elapsed:.1f}s")

    print(f"\n─── Plan benchmark (n={len(test_prompts)}) ───")
    print(f"  Avg input tokens:  {total_tokens_in // len(test_prompts)}")
    print(f"  Avg output tokens: {total_tokens_out // len(test_prompts)}")
    print(f"  Avg cost:          ${total_cost / len(test_prompts):.5f}")
    print(f"  Avg time:          {total_time / len(test_prompts):.1f}s")
    print(f"  Total plan cost:   ${total_cost:.5f}")
    print(f"  Total plan time:   {total_time:.1f}s")

# ---------- Simulate realistic end-to-end ----------

def simulate_full_run():
    """Compare: with-plan vs no-plan overhead for a 2-tool-call task."""
    config = load_config()
    model = get_model(config)
    soul = load_soul()

    prompt = "檢查 ~/baw/ 有無 git commit 未 push，有就 push"

    system_prompt = soul + (
        f"\n\n## Dynamic context\n"
        f"- Current tone: casual\n"
        f"- Fact check mode: normal\n"
        f"- Available tools: bash\n"
        f"- Cost transparency: per-call cost shown after each response"
    )

    print(f"\n─── End-to-end: '{prompt}' ───")

    # Phase 1: Court (common cost, not part of the comparison)
    court_ctx = Context(system_prompt=system_prompt)
    court_ctx.add_user(prompt)

    court = AdversarialCourt(model, system_prompt, config)
    t0 = time.time()
    verdict = court.hold_court(prompt, "")
    court_time = time.time() - t0
    print(f"  Court: {verdict['devil']['tokens_in']+verdict['angel']['tokens_in']}↑ {verdict['devil']['tokens_out']+verdict['angel']['tokens_out']}↓  ${verdict['devil']['cost']+verdict['angel']['cost']:.5f}  {court_time:.1f}s")

    # Phase 2: Plan
    plan_msg = (
        f"[ORCHESTRATOR] Goal: {prompt}\n\n"
        f"Write a detailed step-by-step execution plan. "
        f"Each step must be SMALL (max 1-2 tool calls).\n"
        f"Format each step as:\n"
        f"  Step N: <action> — <expected tool> — <expected outcome>\n\n"
        f"Reply with ONLY the plan, numbered 1..N."
    )
    plan_msgs = [{"role": "system", "content": system_prompt}, {"role": "user", "content": plan_msg}]

    t0 = time.time()
    plan_fb = call_llm_with_fallback(config, plan_msgs, temperature=None)
    plan_time = time.time() - t0
    plan_resp = plan_fb.response
    plan_cost = calculate_cost(model, plan_resp.input_tokens, plan_resp.output_tokens)
    print(f"  Plan:  {plan_resp.input_tokens:>5}↑ {plan_resp.output_tokens:>5}↓  ${plan_cost:.5f}  {plan_time:.1f}s")
    print(f"  Plan text ({len(plan_resp.content or '')} chars):")
    for line in (plan_resp.content or "").strip().split("\n")[:6]:
        print(f"    {line}")

    # Simulate what the plan adds to system prompt
    plan_text = plan_resp.content or ""
    plan_for_context = (
        "\n[Execution Plan]\n" +
        "\n".join(f"  Step {i+1}: {l}" for i, l in enumerate(plan_text.split("\n"))) +
        "\n\n[Progress: nothing completed yet]"
    )

    print(f"\n  Plan overhead in system prompt: ~{len(plan_for_context)} chars")
    print(f"\n─── Summary per task ───")
    baseline_calls = 2  # court only (Devil + Angel)
    with_plan_calls = 3  # court + plan
    print(f"  Without plan: {baseline_calls} LLM calls + execution")
    print(f"  With plan:    {with_plan_calls} LLM calls + execution (1 extra)")
    print(f"  Extra tokens: ~{plan_resp.input_tokens + plan_resp.output_tokens} per task")
    print(f"  Extra cost:   ~${plan_cost:.5f} per task")
    print(f"  Extra time:   ~{plan_time:.1f}s per task")

if __name__ == "__main__":
    print("=== BAW Plan Overhead Benchmark ===\n")
    print("─── Plan generation cost (5 prompts) ───")
    benchmark_plan()
    print("\n")
    simulate_full_run()
