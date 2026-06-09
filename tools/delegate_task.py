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

    _clear()
    _reg(**_ld("bash"))
    _reg(**_ld("read_file"))
    _reg(**_ld("write_file"))
    _reg(**_ld("web_search"))
    _reg(**_ld("vision"))
    return True  # tools registered

def _get_minimax_config() -> dict:
    """Load config and use the configured executor model (no hardcode).
    Falls back gracefully if executor model is not in providers list."""
    import yaml
    data_dir = Path.home() / ".baw"
    cfg = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))

    model_cfg = cfg.get("model", {})
    executor_model = (
        cfg.get("executor", {}).get("model") or
        model_cfg.get("fallback") or
        model_cfg.get("default", "deepseek-v4-flash")
    )

    # Verify executor model actually exists in providers
    providers = cfg.get("providers", {})
    model_exists = any(
        m["id"] == executor_model
        for p in providers.values()
        for m in p.get("models", [])
    )
    if not model_exists:
        # Fall back to default model
        executor_model = model_cfg.get("default", "deepseek-v4-flash")

    cfg["model"] = {
        "default": executor_model,
        "fallback": model_cfg.get("fallback", executor_model),
    }

    # Load env vars
    env_file = data_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    import os
                    os.environ.setdefault(k.strip(), v)

    return cfg


def delegate_task(goal: str, context: str = "", toolsets: str = "") -> str:
    """Delegate a task to a sub-agent (MiniMax executor).

    Sub-agent runs independently with its own tools and isolated context.
    Returns the sub-agent's final output.

    Args:
        goal: The task for the sub-agent to accomplish. Be specific.
        context: Optional background info, file paths, constraints.
        toolsets: Comma-separated tool names to restrict (e.g. "bash,read_file").
                 Leave empty for all tools (bash, read_file, write_file, web_search).
    """
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
    config = _get_minimax_config()

    # ── Restrict toolsets if specified ──
    if toolsets:
        allowed = {t.strip() for t in toolsets.split(",")}
        openai_tools = [t for t in openai_tools if t["function"]["name"] in allowed]

    # ── Build prompt ──
    prompt_parts = [f"[DELEGATED TASK]\nGoal: {goal}"]
    if context:
        prompt_parts.append(f"\nContext:\n{context}")
    prompt_parts.append(
        "\n\nYou are a focused executor. Use your tools to accomplish the goal. "
        "Report back what you did and the key results. Be concise."
    )
    prompt = "\n".join(prompt_parts)

    # ── Run sub-agent in quick mode (no court/plan) ──
    ctx = Context(
        system_prompt=(
            "You are an EXECUTION agent — your ONLY job is to DO, not to research or describe.\n"
            "- If the task says 'write file X', WRITE IT. Do not just check if the file exists.\n"
            "- If the task says 'modify config', MODIFY IT. Do not just read and report.\n"
            "- If the task says 'install package', INSTALL IT. Do not just check versions.\n"
            "- Use write_file/bash to CREATE and MODIFY. Use read_file/web_search only as prep.\n"
            "- Report back ONLY after you've DONE the work — what you changed, not what you found.\n"
            "- NEVER describe what you WOULD do. JUST DO IT.\n"
            "- NEVER ask questions — figure it out yourself."
        ),
        temperature=0.5,
    )
    ctx.add_user(prompt)

    max_iterations = 10
    iteration = 0
    final_content = ""

    for iteration in range(max_iterations):
        fb = call_llm_with_fallback(
            config,
            ctx.to_openai_messages(),
            tools=openai_tools,
            temperature=0.5,
        )
        resp = fb.response

        if resp.content:
            final_content = resp.content

        if not resp.tool_calls:
            break

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

    # ── Format result ──
    result = final_content.strip() or "(no output)"
    return (
        f"[Sub-agent MiniMax result]\n"
        f"{result}\n"
        f"_(iterations: {iteration + 1})_"
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
}
