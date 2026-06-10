"""BAW — Concise Display Formatter

Shows: plan once upfront → step progress → errors if any.
No full reasoning dump — each step is one short line.
Hierarchical step labels: Step A ½, Step B ¼, etc.
"""

from __future__ import annotations


# ── Strip long plan descriptions ──

def _shorten(desc: str, max_len: int = 80) -> str:
    """Shorten a step description: strip tool/outcome, keep human language."""
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


# ── Plan: show once at start, grouped with headers ──

def phase_plan(steps: list[dict]) -> str:
    """Compact plan: grouped by letter with headers and sub-step counts."""
    if not steps:
        return ""
    lines = ["  📋 Plan:"]
    _last_group = ""
    for s in steps:
        _g = s.get("group", "A")
        _gn = s.get("group_name", "")
        _si = s.get("step_in_group", 0)
        _gt = s.get("group_total", 0)
        if _g != _last_group:
            _last_group = _g
            _header = f"  ## Group {_g}"
            if _gn:
                _header += f" — {_gn}"
            _header += f" ({_gt} step{'s' if _gt != 1 else ''})"
            lines.append(_header)
        lines.append(f"    {_g}-{_si}: {_shorten(s['desc'], 50)}")
    return "\n".join(lines) + "\n"


# ── Step progress: hierarchical labels ──

def _step_label(group: str, step_in_group: int, group_total: int) -> str:
    """Format as 'A ½', 'B ¼', etc."""
    return f"{group} {step_in_group}/{group_total}"


def phase_step_done(group: str, step_in_group: int, group_total: int,
                    desc: str, result: str = "") -> str:
    short = _shorten(desc, 50)
    label = _step_label(group, step_in_group, group_total)
    line = f"  ✅ Step {label}: {short}"
    if result:
        line += f" — {result[:60]}"
    return line


def phase_step_running(group: str, step_in_group: int, group_total: int,
                       desc: str) -> str:
    short = _shorten(desc, 50)
    label = _step_label(group, step_in_group, group_total)
    return f"  ▶️  Step {label}: {short}"


def phase_step_skip(group: str, step_in_group: int, group_total: int,
                    desc: str, reason: str = "") -> str:
    short = _shorten(desc, 50)
    label = _step_label(group, step_in_group, group_total)
    line = f"  ⏭️  Step {label}: {short}"
    if reason:
        line += f" — {reason[:60]}"
    return line


def phase_step_error(group: str, step_in_group: int, group_total: int,
                     desc: str, resolution: str = "") -> str:
    short = _shorten(desc, 50)
    label = _step_label(group, step_in_group, group_total)
    line = f"  ⚠️  Step {label}: {short}"
    if resolution:
        line += f"\n    → {resolution[:80]}"
    return line


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
    return "⚡ Quick Reply\n"


# ── Front desk delegation ──

def front_desk_task_id(task_id: str) -> str:
    return f"📨 Task {task_id} — running in background"


def front_desk_status(task_id: str, status: str, detail: str = "") -> str:
    line = f"📨 Task {task_id}: {status}"
    if detail:
        line += f" — {detail[:100]}"
    return line
