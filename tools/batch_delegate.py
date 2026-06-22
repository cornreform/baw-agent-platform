"""BAW built-in: batch_delegate — parallel sub-agent execution.

Runs multiple delegate_task calls in parallel threads.
Each task is independent — they share no context.

Use this for:
- Parallel research (search multiple sources simultaneously)
- Parallel development (write multiple files at once)
- Parallel testing (test multiple scenarios)

Limits: max 5 parallel tasks, 5 min timeout per task.
"""

import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

_BAW_ROOT = str(Path(__file__).resolve().parent.parent)


def batch_delegate(tasks: list[dict]) -> str:
    """Run multiple delegate_task calls in parallel.

    Each task in the list is a dict with:
      goal: str (required) — what the sub-agent should do
      context: str (optional) — background info
      toolsets: str (optional) — comma-separated tool names

    Max 5 parallel tasks. Results are combined in order.

    Args:
        tasks: List of task dicts. Each has 'goal' (required), 'context' (optional).
    """
    if not tasks:
        return "⚠️ No tasks provided."
    if len(tasks) > 5:
        tasks = tasks[:5]
        import logging as _lg
        _lg.getLogger(__name__).warning("[batch_delegate] Truncated to 5 tasks")

    # Import delegate_task (lazy, thread-safe)
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)
    from tools.delegate_task import delegate_task

    results = []
    errors = []
    _start = time.time()
    _task_count = len(tasks)

    with ThreadPoolExecutor(max_workers=min(len(tasks), 5)) as pool:
        future_map = {}
        for i, task in enumerate(tasks):
            goal = task.get("goal", "")
            context = task.get("context", "")
            toolsets = task.get("toolsets", "")
            model_id = task.get("model_id", "")
            fut = pool.submit(
                delegate_task,
                goal=goal,
                context=context,
                toolsets=toolsets,
                model_id=model_id,
            )
            future_map[fut] = i

        for fut in as_completed(future_map):
            i = future_map[fut]
            try:
                result = fut.result(timeout=300)
                results.append((i, result))
            except Exception as e:
                errors.append((i, str(e)))

    # Sort by original order
    results.sort(key=lambda x: x[0])
    errors.sort(key=lambda x: x[0])

    _elapsed = time.time() - _start
    lines = [f"╔═══ Batch Delegate ({len(results)}/{_task_count} done, {_elapsed:.1f}s) ═══╗"]

    for i, result in results:
        # Extract the content between the box markers
        _content = result
        if "│ " in _content:
            _parts = _content.split("│ ")
            if len(_parts) > 1:
                _content = _parts[-1]
                _content = _content.replace("\n│", "\n")
        lines.append(f"├─ Task {i + 1} ─────────────────────┤")
        lines.append(_content[:500])

    if errors:
        lines.append("├─ Errors ─────────────────────────┤")
        for i, err in errors:
            lines.append(f"│ Task {i + 1}: {err[:200]}")

    lines.append(f"╚═══ {_elapsed:.1f}s ═══════════════════════════╝")
    return "\n".join(lines)


def handler(tasks: str) -> str:
    """Run multiple delegate_task calls in parallel.

    Args:
        tasks: JSON string — list of dicts, each with 'goal' (required) and 'context' (optional).
    """
    try:
        task_list = json.loads(tasks) if isinstance(tasks, str) else tasks
    except json.JSONDecodeError as e:
        return f"⚠️ Invalid JSON: {e}"
    if not isinstance(task_list, list):
        return "⚠️ tasks must be a JSON array of dicts."
    return batch_delegate(task_list)


TOOL_DEF = {
    "name": "batch_delegate",
    "description": (
        "Run multiple delegate_task calls IN PARALLEL. "
        "Use for parallel research, parallel file creation, or any work "
        "that can be split into independent subtasks. "
        "Input: JSON array of task dicts, each with 'goal' (required) and 'context' (optional). "
        "Max 5 parallel tasks. Results combined in original order."
    ),
    "handler": handler,
    "parameters": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "string",
                "description": (
                    "JSON array. Each item: {\"goal\": \"...\", \"context\": \"...\", \"toolsets\": \"...\"}. "
                    "Example: [{\"goal\": \"Search the web for X\"}, {\"goal\": \"Create file Y\"}] "
                    "Max 5 items."
                ),
            },
        },
        "required": ["tasks"],
    },
}
