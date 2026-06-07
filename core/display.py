"""BAW — Concise Display Formatter

Shows: plan once upfront → step progress → errors if any.
No full reasoning dump — each step is one short line.
"""

from __future__ import annotations


# ── Strip long plan descriptions ──

def _shorten(desc: str, max_len: int = 50) -> str:
    """Shorten a step description: strip tool/outcome/details."""
    # Strip common verbose patterns
    for sep in (" — ", " —", " - ", " — ", "by running ", "to get ", "to see ",
                "to confirm ", "to check ", "to determine ", "to verify ",
                " — ", "– ", "- ", ": "):
        if sep in desc:
            desc = desc.split(sep)[0].strip()
    # Also cut at first period if it's long
    if len(desc) > max_len and ". " in desc:
        desc = desc.split(". ")[0].strip()
    if len(desc) > max_len:
        desc = desc[:max_len-1] + "…"
    return desc


# ── Plan: show once at start, compact ──

def phase_plan(steps: list[dict]) -> str:
    """Compact plan: each step on its own line."""
    if not steps:
        return ""
    lines = ["  📋 Plan:"]
    for s in steps:
        lines.append(f"    {s['num']}. {_shorten(s['desc'], 50)}")
    return "\n".join(lines) + "\n"


# ── Step progress: concise per-step line ──

def phase_step_done(current: int, total: int, desc: str, result: str = "") -> str:
    short = _shorten(desc, 50)
    line = f"  ✅ Step {current}/{total}: {short}"
    if result:
        line += f" — {result[:60]}"
    return line


def phase_step_running(current: int, total: int, desc: str) -> str:
    short = _shorten(desc, 50)
    return f"  ▶️  Step {current}/{total}: {short}"


def phase_step_skip(current: int, total: int, desc: str, reason: str = "") -> str:
    short = _shorten(desc, 50)
    line = f"  ⏭️  Step {current}/{total}: {short}"
    if reason:
        line += f" — {reason[:60]}"
    return line


def phase_step_error(current: int, total: int, desc: str, resolution: str = "") -> str:
    short = _shorten(desc, 50)
    line = f"  ⚠️  Step {current}/{total}: {short}"
    if resolution:
        line += f"\n    → {resolution[:80]}"
    return line


# ── Strategy recovery / error ──

def strategy_recovery(strategies: list[str], resolution: str = "") -> str:
    if not strategies:
        return ""
    lines = ["  🔄 Issue detected — trying next approach"]
    if resolution:
        lines.append(f"    → {resolution[:80]}")
    return "\n".join(lines)


# ── Done banner ──

def done(steps: int, total: int, elapsed: float, cost: float) -> str:
    pct = 100 if steps >= total else int(100 * steps / max(total, 1))
    return f"\n  ✅ Done — {steps}/{total} steps ({pct}%), {elapsed:.1f}s, ${cost:.5f}"


# ── Court (keep brief) ──

def court_verdict(verdict: dict) -> str:
    devil = verdict.get("devil", {})
    angel = verdict.get("angel", {})
    if not devil or not angel:
        return ""
    ds = devil.get("score", "?")
    as_ = angel.get("score", "?")
    if verdict.get("should_stop"):
        return f"  ⚖️  Court: Devil {ds}/{as_} ⛔ Blocked"
    elif verdict.get("decision") == "warn":
        return f"  ⚖️  Court: Devil {ds} / Angel {as_} ⚠️"
    return f"  ⚖️  Court: Devil {ds} / Angel {as_} ✅"


# ── BTW ──

def btw_header() -> str:
    return "⚡ Quick Reply"


# ── Front desk delegation ──

def front_desk_task_id(task_id: str) -> str:
    return f"📨 Task {task_id} — running in background"


def front_desk_status(task_id: str, status: str, detail: str = "") -> str:
    line = f"📨 Task {task_id}: {status}"
    if detail:
        line += f" — {detail[:100]}"
    return line
