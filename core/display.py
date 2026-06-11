"""BAW — Ultra-Concise Display Formatter

No Group letters. No step counts. Just dynamic status updates.
Shows: overall goal → each step result → done.
"""

from __future__ import annotations


# ── Plan: just the goal, no structure ──

def phase_plan(steps: list[dict]) -> str:
    """Show only overall plan goal, no step structure."""
    if not steps:
        return ""
    # First step's group_name = plan goal
    goal = steps[0].get("group_name", "") or steps[0].get("desc", "")
    lines = [f"📋 {goal}"]
    return "\n".join(lines) + "\n"


# ── Step progress: just description, no label ──

def _shorten(desc: str, max_len: int = 80) -> str:
    """Shorten a step description."""
    for sep in (" — ", "—", " - "):
        if sep in desc:
            desc = desc.split(sep)[0].strip()
    if len(desc) > max_len:
        cutoff = desc[:max_len].rfind("。")
        if cutoff > max_len // 2:
            desc = desc[:cutoff+1]
        else:
            cutoff = desc[:max_len].rfind("，")
            if cutoff > max_len // 2:
                desc = desc[:cutoff+1]
            else:
                desc = desc[:max_len-1] + "…"
    return desc


def phase_step_done(group: str, step_in_group: int, group_total: int,
                    desc: str, result: str = "") -> str:
    short = _shorten(desc, 50)
    line = f"  ✅ {short}"
    if result:
        line += f" — {result[:60]}"
    return line


def phase_step_running(group: str, step_in_group: int, group_total: int,
                       desc: str) -> str:
    short = _shorten(desc, 50)
    return f"  ▶️  {short}"


def phase_step_skip(group: str, step_in_group: int, group_total: int,
                    desc: str, reason: str = "") -> str:
    short = _shorten(desc, 50)
    line = f"  ⏭️  {short}"
    if reason:
        line += f" — {reason[:60]}"
    return line


def phase_step_error(group: str, step_in_group: int, group_total: int,
                     desc: str, resolution: str = "") -> str:
    short = _shorten(desc, 50)
    line = f"  ⚠️  {short}"
    if resolution:
        line += f"\n    → {resolution[:80]}"
    return line


# ── Done banner ──

def done(steps: int, total: int, elapsed: float, cost: float) -> str:
    pct = 100 if steps >= total else int(100 * steps / max(total, 1))
    return f"  ✅ Done — {steps}/{total} ({pct}%), {elapsed:.1f}s"


# ── Court (keep brief) ──

def court_verdict(verdict: dict) -> str:
    devil = verdict.get("devil", {})
    angel = verdict.get("angel", {})
    if not devil or not angel:
        return ""
    ds = devil.get("score", "?")
    as_ = angel.get("score", "?")
    if verdict.get("should_stop"):
        return f"  ⚖️  Court: Devil {ds}/{as_} ⛔"
    elif verdict.get("decision") == "warn":
        return f"  ⚖️  Court: Devil {ds} / Angel {as_} ⚠️"
    return f"  ⚖️  Court: Devil {ds} / Angel {as_} ✅"


# ── BTW ──

def btw_header() -> str:
    return "⚡ Quick Reply\n"


# ── Front desk delegation ──

def front_desk_task_id(task_id: str) -> str:
    return f"📨 Task {task_id} — running in background"


def front_desk_status(task_id: str, status: str, detail: str = "") -> str:
    line = f"📨 Task {task_id}: {status}"
    if detail:
        line += f" — {detail[:100]}"
    return line
