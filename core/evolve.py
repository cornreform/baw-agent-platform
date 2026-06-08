"""BAW — Self-Evolution Engine

Three-layer self-improvement system:

Layer 1 — Behavior Tracking: record every tool call + user interaction
Layer 2 — Pattern Detection: find repeated failures, user corrections, preference shifts
Layer 3 — Auto-Optimization: patch SOUL.md, config, or prompts based on detected patterns
"""
import json
import time
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


# ── Paths ────────────────────────────────────────────────────────

def _data_dir() -> Path:
    return Path.home() / ".baw"


def _log_dir() -> Path:
    p = _data_dir() / "evolve"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Layer 1: Behavior Tracking ───────────────────────────────────

_behavior_log: list[dict] = []  # in-memory buffer, flushed to disk periodically


def track_tool_call(
    name: str,
    args: dict,
    success: bool,
    duration: float,
    error: str = "",
    session_id: str = "",
) -> dict:
    """Record a tool call for behavioral analysis.

    Thread-safe: appends to in-memory buffer and flushes every 10 entries.
    """
    entry = {
        "ts": time.time(),
        "type": "tool_call",
        "tool": name,
        "args_sig": _args_signature(args),
        "success": success,
        "duration": round(duration, 3),
        "error": error[:200] if error else "",
        "session_id": session_id or "",
    }
    _behavior_log.append(entry)

    # Flush to disk every 10 entries
    if len(_behavior_log) >= 10:
        flush_behavior()

    return entry


def track_user_feedback(text: str, session_id: str = "") -> dict:
    """Record a user message that may contain corrections or feedback.

    Detects correction patterns like "唔好", "不要", "錯咗", "改做".
    """
    is_correction = bool(re.search(
        r'(唔好|不要|錯[咗了]|改[做為成]|唔[係啱岩]|不對|wrong|incorrect|fix|change|stop)',
        text,
        re.IGNORECASE,
    ))
    entry = {
        "ts": time.time(),
        "type": "user_feedback",
        "text_sig": text[:100],
        "is_correction": is_correction,
        "session_id": session_id or "",
    }
    _behavior_log.append(entry)

    if len(_behavior_log) >= 10:
        flush_behavior()

    return entry


def flush_behavior():
    """Flush in-memory buffer to disk."""
    if not _behavior_log:
        return
    log_path = _log_dir() / "behavior.jsonl"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            for entry in _behavior_log:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _behavior_log.clear()
    except Exception:
        pass  # silent — don't crash the agent for logging


def _args_signature(args: dict) -> str:
    """Short signature of tool arguments for pattern matching.

    Strips dynamic content (file contents, long strings), keeps structure.
    """
    sig_parts = []
    for k, v in sorted(args.items()):
        if isinstance(v, str) and len(v) > 80:
            sig_parts.append(f"{k}=...({len(v)}chars)")
        elif isinstance(v, str):
            sig_parts.append(f"{k}={v[:40]}")
        elif isinstance(v, (list, dict)):
            sig_parts.append(f"{k}=[...{len(v)}items]")
        else:
            sig_parts.append(f"{k}={v}")
    return " | ".join(sig_parts)


# ── Layer 2: Pattern Detection ───────────────────────────────────

def analyze(hours_back: int = 168) -> dict:
    """Analyze behavior log for patterns.

    Returns recommendations for self-improvement.
    """
    log_path = _log_dir() / "behavior.jsonl"
    if not log_path.exists():
        return _empty_analysis()

    cutoff = time.time() - hours_back * 3600
    entries = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("ts", 0) >= cutoff:
                    entries.append(e)
            except json.JSONDecodeError:
                continue

    if not entries:
        return _empty_analysis()

    tool_calls = [e for e in entries if e.get("type") == "tool_call"]
    feedback = [e for e in entries if e.get("type") == "user_feedback"]

    # ── Pattern 1: High-failure tools ──
    failures_by_tool: dict[str, list[dict]] = {}
    success_by_tool: dict[str, list[dict]] = {}
    for tc in tool_calls:
        t = tc.get("tool", "unknown")
        if tc.get("success"):
            success_by_tool.setdefault(t, []).append(tc)
        else:
            failures_by_tool.setdefault(t, []).append(tc)

    # ── Pattern 2: Slow tools ──
    slow_tools = []
    for tc in tool_calls:
        dur = tc.get("duration", 0)
        if dur > 30 and tc.get("success"):
            slow_tools.append((tc["tool"], dur))

    # ── Pattern 3: User corrections ──
    corrections = [f for f in feedback if f.get("is_correction")]

    # ── Pattern 4: Repeated same-tool failures ──
    tool_fail_chains = []
    consecutive = 1
    prev_tool = ""
    for tc in tool_calls:
        curr = tc.get("tool", "")
        if curr == prev_tool and not tc.get("success"):
            consecutive += 1
        else:
            if consecutive >= 3:
                tool_fail_chains.append({"tool": prev_tool, "count": consecutive})
            consecutive = 1
            prev_tool = curr
    if consecutive >= 3:
        tool_fail_chains.append({"tool": prev_tool, "count": consecutive})

    # ── Build recommendations ──
    recommendations = []

    for tool, fails in failures_by_tool.items():
        total = len(success_by_tool.get(tool, [])) + len(fails)
        if total >= 5 and len(fails) / total > 0.4:
            recommendations.append({
                "type": "high_failure_rate",
                "tool": tool,
                "rate": f"{len(fails)}/{total}",
                "suggestion": f"Consider reducing use of '{tool}' — {len(fails)}/{total} calls failed",
            })

    for t, d in slow_tools[:3]:
        recommendations.append({
            "type": "slow_tool",
            "tool": t,
            "duration": round(d, 1),
            "suggestion": f"'{t}' took {d:.0f}s — consider timeouts or retry strategy",
        })

    if len(corrections) >= 3:
        correction_texts = [f.get("text_sig", "") for f in corrections[:5]]
        recommendations.append({
            "type": "frequent_corrections",
            "count": len(corrections),
            "examples": correction_texts,
            "suggestion": f"User corrected BAW {len(corrections)} times — review response style",
        })

    for chain in tool_fail_chains:
        recommendations.append({
            "type": "failure_chain",
            "tool": chain["tool"],
            "count": chain["count"],
            "suggestion": f"'{chain['tool']}' failed {chain['count']}x consecutively — consider fallback strategy",
        })

    # Stats
    avg_success = 0
    if tool_calls:
        avg_success = sum(1 for tc in tool_calls if tc.get("success")) / len(tool_calls) * 100

    return {
        "period_hours": hours_back,
        "total_entries": len(entries),
        "tool_calls": len(tool_calls),
        "feedback_msgs": len(feedback),
        "success_rate": round(avg_success, 1),
        "corrections": len(corrections),
        "recommendations": recommendations,
    }


def _empty_analysis() -> dict:
    return {
        "period_hours": 0,
        "total_entries": 0,
        "tool_calls": 0,
        "feedback_msgs": 0,
        "success_rate": 0,
        "corrections": 0,
        "recommendations": [],
    }


# ── Layer 3: Auto-Optimization ────────────────────────────────────

def auto_optimize(dry_run: bool = False) -> dict:
    """Run analysis and apply optimizations based on detected patterns.

    Called during weekly dreaming.
    Returns a report of what was changed.
    """
    # Flush any buffered entries first
    flush_behavior()

    analysis = analyze(hours_back=168)  # Last 7 days
    result = {
        "analyzed": True,
        "patterns_found": len(analysis.get("recommendations", [])),
        "soul_patched": False,
        "config_patched": False,
        "patches": [],
    }

    # Only act if there are meaningful patterns
    recs = analysis.get("recommendations", [])
    if not recs:
        return result

    # ── Patch SOUL.md based on frequent corrections ──
    correction_recs = [r for r in recs if r.get("type") == "frequent_corrections"]
    if correction_recs and not dry_run:
        correction_count = correction_recs[0].get("count", 0)
        if correction_count >= 5:
            soul_path = _data_dir() / "SOUL.md"
            if soul_path.exists():
                soul = soul_path.read_text(encoding="utf-8")
                # Inject a learning section if not already present
                learn_marker = "<!-- evolve:learned-preferences -->"
                if learn_marker not in soul:
                    correction_texts = correction_recs[0].get("examples", [])
                    pref_note = (
                        f"\n\n{learn_marker}\n"
                        f"## Evolving Preferences (auto-detected {datetime.now().strftime('%Y-%m-%d')})\n"
                        f"\n"
                        f"Recent corrections suggest adjusting response style:\n"
                        + "\n".join(f"- User said: '{c[:60]}'" for c in correction_texts[:3])
                        + "\n"
                    )
                    soul += pref_note
                    soul_path.write_text(soul, encoding="utf-8")
                    result["soul_patched"] = True
                    result["patches"].append(
                        f"Added evolving preferences to SOUL.md ({correction_count} corrections detected)"
                    )

    # ── Patch config based on failure patterns ──
    failure_recs = [r for r in recs if r.get("type") == "high_failure_rate"]
    if failure_recs and not dry_run:
        import yaml
        cfg_path = _data_dir() / "config.yaml"
        if cfg_path.exists():
            config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            # Log failures in config for transparency (don't auto-disable tools)
            config.setdefault("evolve", {}).setdefault("known_issues", [])
            for r in failure_recs:
                issue = {
                    "tool": r["tool"],
                    "rate": r["rate"],
                    "detected": datetime.now().isoformat(),
                }
                # Avoid duplicates
                existing = [i for i in config["evolve"]["known_issues"] if i.get("tool") == r["tool"]]
                if not existing:
                    config["evolve"]["known_issues"].append(issue)
                    result["patches"].append(f"Logged failure issue for tool '{r['tool']}' ({r['rate']})")
                    result["config_patched"] = True

            if result["config_patched"]:
                cfg_path.write_text(
                    yaml.dump(config, default_flow_style=False, allow_unicode=True),
                    encoding="utf-8",
                )

    return result


# ── Stats (for /status command) ──

def get_evolve_stats() -> str:
    """Return a single-line summary of evolution state."""
    log_path = _log_dir() / "behavior.jsonl"
    if not log_path.exists():
        return "🧬 Evolution: no data yet"

    try:
        with open(log_path) as f:
            total = sum(1 for _ in f)
    except Exception:
        total = 0

    analysis = analyze(hours_back=24)
    recs = len(analysis.get("recommendations", []))
    rate = analysis.get("success_rate", 0)
    corr = analysis.get("corrections", 0)

    parts = [f"🧬 {total} events logged"]
    if rate:
        parts.append(f"{rate}% success rate")
    if corr:
        parts.append(f"{corr} corrections")
    if recs:
        parts.append(f"{recs} patterns detected")
    return " | ".join(parts)
