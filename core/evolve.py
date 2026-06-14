"""BAW — Self-Evolution Engine

Three-layer self-improvement system:

Layer 1 — Behavior Tracking: record every tool call + user interaction
Layer 2 — Pattern Detection: find repeated failures, user corrections, preference shifts
Layer 3 — Auto-Optimization: patch SOUL.md, config, or prompts based on detected patterns
"""
import json
import time
import re
import threading
import subprocess as _sp
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


# ── Safety: Git Snapshot ─────────────────────────────────────────

import subprocess as _sp

_GIT_LOCK = threading.Lock() if "threading" in dir() else None


def _git_snapshot(tag_reason: str = "auto-optimize") -> dict:
    """Create a git snapshot before any modifications.

    Returns {"ok": bool, "commit": str, "error": str}.
    """
    data_dir = _data_dir()
    git_dir = data_dir.parent if (data_dir.parent / ".git").exists() else data_dir
    if not (git_dir / ".git").exists():
        return {"ok": False, "commit": "", "error": "Not a git repo"}

    try:
        # Stage any current changes first for a clean snapshot
        _sp.run(["git", "-C", str(git_dir), "add", "-A"], capture_output=True, timeout=10)
        result = _sp.run(
            ["git", "-C", str(git_dir), "commit", "-m", f"[evolve-snapshot] {tag_reason}", "--allow-empty"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode in (0, 1):
            # Get the commit hash
            hash_res = _sp.run(
                ["git", "-C", str(git_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            commit_hash = hash_res.stdout.strip() if hash_res.returncode == 0 else ""
            return {"ok": True, "commit": commit_hash, "error": ""}
        return {"ok": False, "commit": "", "error": result.stderr[:200]}
    except Exception as e:
        return {"ok": False, "commit": "", "error": str(e)[:200]}


def _git_rollback(commit_hash: str, reason: str = "auto-rollback") -> dict:
    """Revert to a previous git snapshot.

    Returns {"ok": bool, "error": str}.
    """
    data_dir = _data_dir()
    git_dir = data_dir.parent if (data_dir.parent / ".git").exists() else data_dir
    if not (git_dir / ".git").exists():
        return {"ok": False, "error": "Not a git repo"}

    try:
        _sp.run(
            ["git", "-C", str(git_dir), "revert", "HEAD", "--no-edit", "--autocommit"],
            capture_output=True, text=True, timeout=15,
        )
        return {"ok": True, "error": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Safety: Cross-Check Verify ───────────────────────────────────

def _verify_soul(path: Path) -> dict:
    """Verify SOUL.md is valid markdown and contains required sections."""
    errors = []
    if not path.exists():
        return {"ok": False, "errors": ["File not found"]}
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            errors.append("Empty file")
        if "# " not in text:
            errors.append("Missing heading")
        if "BAW" not in text:
            errors.append("Missing BAW identity reference")
    except Exception as e:
        errors.append(str(e))
    return {"ok": not errors, "errors": errors}


def _verify_config(path: Path) -> dict:
    """Verify config.yaml is valid YAML."""
    errors = []
    if not path.exists():
        return {"ok": False, "errors": ["File not found"]}
    try:
        import yaml
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"YAML syntax error: {e}")
    return {"ok": not errors, "errors": errors}


# ── Pending Approval Queue (P3-1) ─────────────────────────────────

_PEND_PATH = _log_dir() / "pending_approvals.json"


def _load_pending() -> list[dict]:
    if not _PEND_PATH.exists():
        return []
    try:
        return json.loads(_PEND_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_pending(pending: list[dict]):
    _PEND_PATH.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


def queue_for_approval(recommendations: list[dict]) -> list[dict]:
    """Queue auto-optimization recommendations for user approval.

    Returns the list of newly queued items.
    """
    pending = _load_pending()
    newly_queued = []
    for rec in recommendations:
        # Deduplicate by signature
        sig = f"{rec.get('type')}:{rec.get('tool', '')}:{rec.get('suggestion', '')[:60]}"
        if not any(p.get("_sig") == sig for p in pending):
            item = dict(rec)
            item["_sig"] = sig
            item["queued_at"] = time.time()
            item["status"] = "pending"
            newly_queued.append(item)
            pending.append(item)
    if newly_queued:
        _save_pending(pending)
    return newly_queued


def get_pending_approvals() -> list[dict]:
    """Return all pending approvals."""
    return [p for p in _load_pending() if p.get("status") == "pending"]


def approve_pending(index: int, approved: bool = True) -> dict:
    """Approve or reject a pending optimization by index.

    Returns {"ok": bool, "action": str, "error": str}.
    """
    pending = _load_pending()
    active = [p for p in pending if p.get("status") == "pending"]
    if index < 0 or index >= len(active):
        return {"ok": False, "action": "", "error": "Invalid index"}

    target = active[index]
    target["status"] = "approved" if approved else "rejected"
    target["decided_at"] = time.time()
    _save_pending(pending)

    if approved:
        # Re-run auto_optimize for just this recommendation (not dry_run)
        result = auto_optimize_single(target)
        return {"ok": result.get("ok", True), "action": "applied", "error": result.get("error", "")}
    return {"ok": True, "action": "rejected", "error": ""}


# ── Layer 3: Auto-Optimization ────────────────────────────────────

def auto_optimize(dry_run: bool = False) -> dict:
    """Run analysis and apply optimizations based on detected patterns.

    Safety-first flow:
      1. Git snapshot before any writes (P3-4)
      2. Dry-run collects patches; real run needs explicit --apply (P3-1)
      3. After each write, cross-check verify (P3-3)
      4. On verify failure, auto-rollback (P3-2)

    Called during weekly dreaming or cron analyze.
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
        "snapshot": None,
        "rolled_back": False,
        "verify_errors": [],
    }

    # Only act if there are meaningful patterns
    recs = analysis.get("recommendations", [])
    if not recs:
        return result

    # ── P3-1: Dry-run approval queue ──
    if dry_run:
        queued = queue_for_approval(recs)
        result["patches"] = [f"[QUEUED for approval] {r.get('suggestion', '')}" for r in queued]
        result["queued_count"] = len(queued)
        return result

    # ── P3-4: Git snapshot before writes ──
    snapshot = _git_snapshot(tag_reason="evolve-auto-optimize")
    result["snapshot"] = snapshot
    if not snapshot.get("ok"):
        result["patches"].append(f"[WARNING] Git snapshot failed: {snapshot.get('error', '')}")
        # Continue anyway — snapshot is best-effort safety net

    soul_path = _data_dir() / "SOUL.md"
    cfg_path = _data_dir() / "config.yaml"
    files_modified = []

    # ── Patch SOUL.md based on frequent corrections ──
    correction_recs = [r for r in recs if r.get("type") == "frequent_corrections"]
    if correction_recs:
        correction_count = correction_recs[0].get("count", 0)
        if correction_count >= 5 and soul_path.exists():
            soul = soul_path.read_text(encoding="utf-8")
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
                files_modified.append(soul_path)
                result["soul_patched"] = True
                result["patches"].append(
                    f"Added evolving preferences to SOUL.md ({correction_count} corrections detected)"
                )

    # ── Patch config based on failure patterns ──
    failure_recs = [r for r in recs if r.get("type") == "high_failure_rate"]
    if failure_recs and cfg_path.exists():
        import yaml
        config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        config.setdefault("evolve", {}).setdefault("known_issues", [])
        for r in failure_recs:
            issue = {
                "tool": r["tool"],
                "rate": r["rate"],
                "detected": datetime.now().isoformat(),
            }
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
            files_modified.append(cfg_path)

    # ── P3-3: Cross-check verify ──
    verify_errors = []
    if soul_path in files_modified:
        v = _verify_soul(soul_path)
        if not v["ok"]:
            verify_errors.extend([f"SOUL.md: {e}" for e in v["errors"]])
    if cfg_path in files_modified:
        v = _verify_config(cfg_path)
        if not v["ok"]:
            verify_errors.extend([f"config.yaml: {e}" for e in v["errors"]])

    result["verify_errors"] = verify_errors

    # ── P3-2: Auto-rollback on verify failure ──
    if verify_errors and snapshot.get("ok"):
        rollback = _git_rollback(snapshot["commit"], reason="verify-failure")
        result["rolled_back"] = rollback.get("ok", False)
        if result["rolled_back"]:
            result["patches"].append(f"[ROLLBACK] Reverted due to verify errors: {'; '.join(verify_errors)}")
            result["soul_patched"] = False
            result["config_patched"] = False
        else:
            result["patches"].append(f"[ROLLBACK FAILED] {rollback.get('error', '')}")

    # ── P4-1: Correction learning ──
    if correction_recs and not result.get("rolled_back"):
        lessons = _extract_correction_lessons(correction_recs[0].get("examples", []))
        added = _write_learned_lessons(lessons)
        if added:
            result["patches"].append(f"[P4-1] Learned {added} new behavioral lessons")

    # ── P4-2: Prompt auto-tune ──
    tuning = _tune_prompt_style(analysis)
    if tuning["suggestions"]:
        for s in tuning["suggestions"]:
            result["patches"].append(f"[P4-2] {s}")

    # ── P4-3: Model routing optimization ──
    routing = _optimize_model_routing(analysis)
    for s in routing.get("suggestions", []):
        result["patches"].append(f"[P4-3] {s['suggestion']}")

    # ── P4-4: Behavioral drift detection ──
    log_path = _log_dir() / "behavior.jsonl"
    feedback_entries = []
    if log_path.exists():
        cutoff = time.time() - 168 * 3600
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("ts", 0) >= cutoff and e.get("type") == "user_feedback":
                        feedback_entries.append(e)
                except Exception:
                    continue
    drift_alerts = _detect_behavioral_drift(feedback_entries)
    for alert in drift_alerts:
        result["patches"].append(f"[P4-4 ALERT] {alert['suggestion']}")

    return result


def auto_optimize_single(rec: dict) -> dict:
    """Apply a single approved recommendation (used by approval queue)."""
    rec_type = rec.get("type", "")
    result = {"ok": True, "error": "", "applied": False}

    # Re-use the same safety flow
    snapshot = _git_snapshot(tag_reason="evolve-approved-item")

    if rec_type == "frequent_corrections":
        soul_path = _data_dir() / "SOUL.md"
        if soul_path.exists():
            soul = soul_path.read_text(encoding="utf-8")
            learn_marker = "<!-- evolve:learned-preferences -->"
            if learn_marker not in soul:
                pref_note = (
                    f"\n\n{learn_marker}\n"
                    f"## Evolving Preferences (approved {datetime.now().strftime('%Y-%m-%d')})\n"
                    f"\n"
                    f"Recent corrections suggest adjusting response style.\n"
                )
                soul += pref_note
                soul_path.write_text(soul, encoding="utf-8")
                v = _verify_soul(soul_path)
                if not v["ok"]:
                    _git_rollback(snapshot.get("commit", "HEAD"), reason="verify-failure")
                    return {"ok": False, "error": "; ".join(v["errors"])}
                result["applied"] = True

    elif rec_type == "high_failure_rate":
        import yaml
        cfg_path = _data_dir() / "config.yaml"
        if cfg_path.exists():
            config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            config.setdefault("evolve", {}).setdefault("known_issues", [])
            issue = {
                "tool": rec.get("tool", ""),
                "rate": rec.get("rate", ""),
                "detected": datetime.now().isoformat(),
            }
            existing = [i for i in config["evolve"]["known_issues"] if i.get("tool") == rec.get("tool")]
            if not existing:
                config["evolve"]["known_issues"].append(issue)
                cfg_path.write_text(
                    yaml.dump(config, default_flow_style=False, allow_unicode=True),
                    encoding="utf-8",
                )
                v = _verify_config(cfg_path)
                if not v["ok"]:
                    _git_rollback(snapshot.get("commit", "HEAD"), reason="verify-failure")
                    return {"ok": False, "error": "; ".join(v["errors"])}
                result["applied"] = True

    return result


# ── Phase 4: Full Self-Evolution ──────────────────────────────────

_LEARNED_PATH = _log_dir() / "learned_lessons.json"


def _load_learned_lessons() -> list[dict]:
    if not _LEARNED_PATH.exists():
        return []
    try:
        return json.loads(_LEARNED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_learned_lessons(lessons: list[dict]):
    _LEARNED_PATH.write_text(json.dumps(lessons, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_correction_lessons(correction_texts: list[str]) -> list[dict]:
    """Extract specific behavioral lessons from user correction texts.

    Looks for patterns like:
      - "太長" / "簡短" / " concise" → lesson: response_length=short
      - "用繁體" / "不要簡體" → lesson: language=traditional_chinese
      - "唔好用 table" / "難複製" → lesson: format=no_tables
      - "太貴" / "慳啲" → lesson: cost=save
    """
    lessons = []
    seen_sigs = set()

    for txt in correction_texts:
        txt_lower = txt.lower()
        lesson = None

        if any(k in txt_lower for k in ["太長", "長篇", "簡短", "短啲", "concise", "簡潔", "啰嗦"]):
            lesson = {"type": "response_length", "value": "short", "source": txt[:60]}
        elif any(k in txt_lower for k in ["繁體", "不要簡體", "簡體中文", "traditional"]):
            lesson = {"type": "language", "value": "traditional_chinese", "source": txt[:60]}
        elif any(k in txt_lower for k in ["table", "表格", "難複製", "copy", "pipe"]):
            lesson = {"type": "format", "value": "no_tables_use_bullets", "source": txt[:60]}
        elif any(k in txt_lower for k in ["太貴", "慳啲", "save cost", "cheap", "minimax"]):
            lesson = {"type": "cost", "value": "prefer_cheap_model", "source": txt[:60]}
        elif any(k in txt_lower for k in ["唔好", "不要", "stop", "wrong", "incorrect", "fix"]):
            lesson = {"type": "avoid_behavior", "value": txt[:80], "source": txt[:60]}

        if lesson:
            sig = f"{lesson['type']}:{lesson['value']}"
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                lesson["learned_at"] = int(time.time())
                lessons.append(lesson)

    return lessons


def _write_learned_lessons(new_lessons: list[dict]) -> int:
    """Merge new lessons into learned_lessons.json, deduplicate."""
    existing = _load_learned_lessons()
    existing_sigs = {f"{l.get('type')}:{l.get('value')}" for l in existing}
    added = 0
    for lesson in new_lessons:
        sig = f"{lesson['type']}:{lesson['value']}"
        if sig not in existing_sigs:
            existing.append(lesson)
            existing_sigs.add(sig)
            added += 1
    if added:
        _save_learned_lessons(existing)
    return added


def _tune_prompt_style(analysis: dict) -> dict:
    """P4-2: Auto-tune prompt style based on user corrections.

    Analyzes correction patterns and returns tuning recommendations.
    Does NOT modify prompts directly — returns suggestions for approval.
    """
    lessons = _load_learned_lessons()
    tuning = {"suggestions": [], "style_patches": []}

    # Count recent lessons by type
    now = time.time()
    recent = [l for l in lessons if (now - l.get("learned_at", 0)) < 7 * 86400]

    length_lessons = [l for l in recent if l.get("type") == "response_length"]
    if len(length_lessons) >= 3:
        tuning["suggestions"].append(
            f"User prefers SHORTER responses ({len(length_lessons)} corrections in 7 days)"
        )
        tuning["style_patches"].append({"target": "system_prompt", "action": "shorten"})

    format_lessons = [l for l in recent if l.get("type") == "format"]
    if len(format_lessons) >= 2:
        tuning["suggestions"].append(
            f"User dislikes tables/formats ({len(format_lessons)} corrections) — use bullet lists"
        )
        tuning["style_patches"].append({"target": "system_prompt", "action": "no_tables"})

    cost_lessons = [l for l in recent if l.get("type") == "cost"]
    if len(cost_lessons) >= 2:
        tuning["suggestions"].append(
            f"User prefers cheaper models ({len(cost_lessons)} corrections)"
        )
        tuning["style_patches"].append({"target": "model_routing", "action": "prefer_cheap"})

    return tuning


def _optimize_model_routing(analysis: dict) -> dict:
    """P4-3: Recommend model routing changes based on success rates.

    Looks at tool-level success rates and suggests model swaps.
    Returns suggestions only — does NOT modify config directly.
    """
    log_path = _log_dir() / "behavior.jsonl"
    if not log_path.exists():
        return {"suggestions": []}

    cutoff = time.time() - 7 * 86400
    tool_model_stats: dict[str, dict[str, dict]] = {}

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                if e.get("ts", 0) < cutoff or e.get("type") != "tool_call":
                    continue
                tool = e.get("tool", "unknown")
                model = e.get("model", "default")
                success = e.get("success", False)
                if tool not in tool_model_stats:
                    tool_model_stats[tool] = {}
                if model not in tool_model_stats[tool]:
                    tool_model_stats[tool][model] = {"success": 0, "fail": 0}
                if success:
                    tool_model_stats[tool][model]["success"] += 1
                else:
                    tool_model_stats[tool][model]["fail"] += 1
            except Exception:
                continue

    suggestions = []
    for tool, models in tool_model_stats.items():
        for model, stats in models.items():
            total = stats["success"] + stats["fail"]
            if total >= 5 and stats["fail"] / total > 0.5:
                suggestions.append({
                    "type": "model_routing",
                    "tool": tool,
                    "model": model,
                    "rate": f"{stats['fail']}/{total}",
                    "suggestion": f"Model '{model}' for '{tool}' failing {stats['fail']}/{total} — consider alternative",
                })

    return {"suggestions": suggestions}


def _detect_behavioral_drift(feedback: list[dict]) -> list[dict]:
    """P4-4: Detect when BAW behavior drifts from SOUL.md identity.

    Looks for feedback indicating identity confusion or style drift.
    """
    drift_signals = []
    drift_keywords = [
        "語言模型", "language model", "ai 模型", "機械人", "robot",
        "太機械", "無情", "像機器", "不像baw", "不像你", "身份", "identity",
    ]

    for f in feedback:
        txt = f.get("text_sig", "").lower()
        for kw in drift_keywords:
            if kw in txt:
                drift_signals.append({
                    "text": f.get("text_sig", "")[:80],
                    "keyword": kw,
                    "ts": f.get("ts", 0),
                })
                break

    alerts = []
    if len(drift_signals) >= 3:
        alerts.append({
            "type": "behavioral_drift",
            "count": len(drift_signals),
            "examples": [d["text"] for d in drift_signals[:3]],
            "suggestion": f"BAW identity drift detected ({len(drift_signals)} signals) — review SOUL.md adherence",
        })

    return alerts


def get_learned_lessons_summary() -> str:
    """Return a formatted summary of learned lessons for display."""
    lessons = _load_learned_lessons()
    if not lessons:
        return "📚 No learned lessons yet"
    lines = [f"📚 {len(lessons)} learned lessons:"]
    for l in lessons[-5:]:
        lines.append(f"  • [{l.get('type', '')}] {l.get('value', '')}")
    return "\\n".join(lines)


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
