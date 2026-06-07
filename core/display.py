"""BAW — Rich Display Formatter

Shows the agent's internal reasoning, plan, and step-by-step progress
with clear formatting. No more blank tool calls — user sees WHAT the
agent is thinking and WHY it's doing each step.
"""

from __future__ import annotations
from datetime import datetime


# ── Terminal-friendly section separators ──

def rule(title: str = "", width: int = 50, char: str = "─") -> str:
    """Draw a section separator like '─── Analysis ────────────'"""
    if not title:
        return char * width
    space = width - len(title) - 2
    left = space // 2
    right = space - left
    return f"{char * left} {title} {char * right}"


def hr() -> str:
    """Full-width separator line."""
    return "─" * 50


# ── Phase headers ──

def phase_analysis(text: str, max_len: int = 600) -> str:
    """Show the agent's internal analysis before a plan."""
    lines = [
        "",
        f"🔍 {rule('ANALYSIS')}",
    ]
    if text:
        # Truncate very long analysis
        if len(text) > max_len:
            text = text[:max_len] + "\n[... truncated]"
        lines.append(text)
    return "\n".join(lines)


def phase_plan(steps: list[dict]) -> str:
    """Show the execution plan.
    
    steps: list of {num, desc} dicts from the plan parser.
    """
    if not steps:
        return ""
    lines = [
        "",
        f"📋 {rule('PLAN')}",
    ]
    for s in steps:
        lines.append(f"  Step {s['num']}: {s['desc']}")
    lines.append(f"  ── {len(steps)} steps total")
    return "\n".join(lines)


def phase_step(current: int, total: int, desc: str, result: str = "") -> str:
    """Show a step being executed with live result.
    
    Returns two lines: the step header and the optional result.
    """
    header = f"  ▶️  Step {current}/{total}: {desc}"
    if result:
        return f"{header}\n    ✅ {result[:200]}"
    return header


def phase_step_done(current: int, total: int, desc: str, result: str = "") -> str:
    """Show a completed step."""
    header = f"  ✅ Step {current}/{total}: {desc}"
    if result:
        return f"{header}\n    {_indent(result[:200])}"
    return header


def phase_step_skip(current: int, total: int, desc: str, reason: str = "") -> str:
    """Show a skipped or blocked step."""
    header = f"  ⏭️  Step {current}/{total}: {desc}"
    if reason:
        return f"{header}\n    ⛔ {reason[:200]}"
    return header


def phase_step_error(current: int, total: int, desc: str, error: str = "") -> str:
    """Show a failed step."""
    header = f"  ❌ Step {current}/{total}: {desc}"
    if error:
        return f"{header}\n    ⚠️ {error[:200]}"
    return header


# ── Progress bar ──

def progress_bar(completed: int, total: int, width: int = 20) -> str:
    """Simple ASCII progress bar: [████░░░░░░] 2/5"""
    if total <= 0:
        return ""
    filled = int(width * completed / total)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"  [{bar}] {completed}/{total}"


def phase_progress(completed: int, total: int) -> str:
    """Single-line progress banner."""
    pct = int(100 * completed / total) if total > 0 else 0
    return f"\n📊 {rule(f'Progress: {completed}/{total} ({pct}%)')}"


# ── Tool call context ──

def tool_call(name: str, args: dict) -> str:
    """Describe what tool is being called and why."""
    desc = _tool_description(name, args)
    return f"    🛠️  running {name}(…)\n    └─ {desc}"


def tool_result(name: str, result: str) -> str:
    """Show tool result with context."""
    if not result:
        return ""
    short = result[:250]
    return f"      ↩️ {short}"


def _tool_description(name: str, args: dict) -> str:
    """Human-readable description of what a tool call does."""
    descriptions = {
        "bash": lambda a: f"shell: `{a.get('command', '').strip()[:80]}`",
        "read_file": lambda a: f"read: `{a.get('path', '?')}`",
        "write_file": lambda a: f"write: `{a.get('path', '?')}` ({len(a.get('content', ''))} chars)",
        "web_search": lambda a: f"search: `{a.get('query', '')[:60]}`",
    }
    fn = descriptions.get(name)
    if fn:
        return fn(args)
    return f"execute {name}"


# ── Court display ──

def court_verdict(verdict: dict) -> str:
    """Format the Angel/Devil court verdict for display."""
    devil = verdict.get("devil", {})
    angel = verdict.get("angel", {})
    if not devil or not angel:
        return ""
    lines = [
        "",
        f"⚖️ {rule('ADVERSARIAL CHECK')}",
        f"  👿 Devil (opposing): {devil.get('score', '?')}/10",
        f"  😇 Angel (executor): {angel.get('score', '?')}/10",
    ]
    if verdict.get("should_stop"):
        lines.append(f"  ⛔ Blocked — Devil wins")
    elif verdict.get("decision") == "warn":
        lines.append(f"  ⚠️ Proceeding with caution")
    else:
        lines.append(f"  ✅ Proceeding")
    
    # Show reasoning
    devil_reason = _extract_analysis(devil.get("content", ""))
    angel_reason = _extract_analysis(angel.get("content", ""))
    if devil_reason:
        lines.append(f"\n  👿 Devil: {devil_reason}")
    if angel_reason:
        lines.append(f"  😇 Angel: {angel_reason}")
    
    return "\n".join(lines)


def _extract_analysis(text: str) -> str:
    """Extract the core reasoning from a potentially long analysis text."""
    if not text:
        return ""
    # Take first 2 sentences or first 200 chars
    sentences = text.split(".")
    result = ".".join(sentences[:2])
    if len(result) > 250:
        result = result[:250] + "..."
    return result.strip()


# ── Final report ──

def summary(steps_completed: int, total_steps: int, elapsed: float, cost: float) -> str:
    """End-of-task summary."""
    return (
        f"\n{hr()}"
        f"\n📊 Summary: {steps_completed}/{total_steps} steps | "
        f"{elapsed:.1f}s | ${cost:.5f}"
    )


# ── Strategy recovery ──

def strategy_recovery(strategies: list[str]) -> str:
    """Show which strategies were tried and failed."""
    if not strategies:
        return ""
    lines = [f"\n🔄 {rule('STRATEGY RECOVERY')}"]
    for s in strategies:
        lines.append(f"  ❌ {s}")
    return "\n".join(lines)


def _indent(text: str, prefix: str = "    ") -> str:
    """Indent multi-line text."""
    return text.replace("\n", f"\n{prefix}")


# ── BTW (quick question) ──

def btw_header() -> str:
    """Show that this is a quick BTW response, not main task."""
    return f"\n⚡ {rule('BTW - Quick Reply')}\n"


def btw_context_note() -> str:
    """Note that BTW is independent of current task."""
    return "\n<i>(independent — does not interrupt any running task)</i>"


# ── Front desk delegation ──

def front_desk_task_id(task_id: str) -> str:
    """Show that a task has been delegated."""
    return (
        f"\n📨 {rule('TASK DELEGATED')}"
        f"\n  Task ID: {task_id}"
        f"\n  Running in background — chat stays free"
        f"\n  Check with `baw --status {task_id}`"
    )


def front_desk_status(task_id: str, status: str, detail: str = "") -> str:
    """Show status update from a background task."""
    lines = [
        f"\n📨 Task [{task_id}] update:",
        f"  Status: {status}",
    ]
    if detail:
        lines.append(f"  {detail}")
    return "\n".join(lines)
