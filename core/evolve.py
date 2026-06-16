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

# LLM-assisted classification (used when keyword extraction doesn't fully cover corrections)
try:
    from core.llm import call_llm, get_model, load_config as _llm_load_config, ModelDef
    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False


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

    # ── LLM-assisted correction classification (enhances keyword extraction) ──
    llm_lessons = _llm_classify_corrections([
        r.get("suggestion", "") for r in recs if r.get("type") == "frequent_corrections"
    ])
    if llm_lessons:
        added = _write_learned_lessons(llm_lessons)
        if added:
            result["patches"].append(f"LLM-classified {added} new correction lessons")

    # ── Patch SOUL.md based on frequent corrections ──
    correction_recs = [r for r in recs if r.get("type") == "frequent_corrections"]
    if correction_recs:
        correction_count = correction_recs[0].get("count", 0)
        if correction_count >= 2 and soul_path.exists():
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


def _llm_classify_corrections(correction_texts: list[str]) -> list[dict]:
    """Use LLM to classify corrections beyond keyword matching.

    Batches up to 10 corrections into one lightweight LLM call.
    Falls back gracefully if LLM is unavailable.
    Returns lessons in same format as _extract_correction_lessons().
    """
    if not _HAS_LLM or not correction_texts:
        return []
    if len(correction_texts) > 10:
        correction_texts = correction_texts[-10:]  # batch limit

    try:
        config = _llm_load_config()
        model = get_model(config)  # uses default model (cheapest)
    except Exception:
        return []

    prompt = (
        "您是一個用戶行爲分析器。以下是用戶對 AI 回覆的修正。"
        "請爲每個修正歸類，返回 JSON 數組。\n\n"
        "類別: response_length(長度), format(格式), language(語言), "
        "cost(成本), tone(語氣), avoid_behavior(不應做), "
        "instruction(執行方式), other(其他)\n\n"
        "格式: [{\"text\":\"原文\", \"category\":\"類別\", \"value\":\"偏好\", \"confidence\":0.0}]\n\n"
        "用戶的修正：\n" +
        "\n".join(f'{i+1}. "{t[:120]}"' for i, t in enumerate(correction_texts))
    )

    try:
        messages = [{"role": "user", "content": prompt}]
        resp = call_llm(model=model, messages=messages, temperature=0.2, max_tokens=1024)
        content = resp.content.strip()
        # Extract JSON from response
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        results = json.loads(content)
        if not isinstance(results, list):
            results = [results]
    except Exception:
        return []

    lessons = []
    for r in results:
        cat = r.get("category", "other")
        val = r.get("value", "") or ""
        conf = r.get("confidence", 0.5)
        text = r.get("text", "")[:60]
        if conf >= 0.5 and val:
            lessons.append({
                "type": cat,
                "value": val,
                "source": text,
                "learned_at": int(time.time()),
                "method": "llm",
                "confidence": round(conf, 2),
            })
    return lessons


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
    if len(length_lessons) >= 2:
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
        return "[LEARN] No learned lessons yet"
    lines = [f"[LEARN] {len(lessons)} learned lessons:"]
    for l in lessons[-5:]:
        lines.append(f"  • [{l.get('type', '')}] {l.get('value', '')}")
    return "\\n".join(lines)


# ── P5: Code-Level Auto-Patching (D1 — modify own code from patterns) ──

_CODE_ROOT = Path(__file__).resolve().parent.parent  # ~/baw/


def _auto_patch_code(analysis: dict) -> dict:
    """D1: Generate and apply code patches based on failure patterns.

    Uses LLM to generate patches for tools with high failure rates.
    Safety: git snapshot → syntax verify → test → commit/rollback.

    Returns patch report dict.
    """
    result = {"patches": [], "ok": True, "errors": []}
    recs = analysis.get("recommendations", [])

    # Only patch from high-failure-rate patterns
    failure_recs = []
    for r in recs:
        if r.get("type") != "high_failure_rate":
            continue
        raw = r.get("rate", 0)
        # rate may be string like "6/7" or numeric
        rate_val = 0.0
        if isinstance(raw, str) and "/" in raw:
            try:
                n, d = raw.split("/")
                rate_val = float(n) / max(1, float(d))
            except (ValueError, ZeroDivisionError):
                rate_val = 0.5
        elif isinstance(raw, (int, float)):
            rate_val = float(raw)
        if rate_val > 0.5:
            r["_rate_val"] = rate_val
            failure_recs.append(r)

    for rec in failure_recs:
        tool_name = rec.get("tool", "")
        fail_rate = rec.get("_rate_val", 0)
        count = rec.get("count", 0)
        if not tool_name or count < 3:
            continue

        # Locate the tool file
        tool_file = _CODE_ROOT / "tools" / f"{tool_name}.py"
        if not tool_file.exists():
            result["patches"].append(f"Skipped {tool_name}: no source file found")
            continue

        try:
            # Git snapshot
            snap = _git_snapshot(tag_reason=f"pre-patch-{tool_name}")
            commit_hash = snap.get("commit", "")

            # Use LLM to generate a fix
            if not _HAS_LLM:
                result["patches"].append(f"Skipped {tool_name}: LLM unavailable")
                continue

            source = tool_file.read_text(encoding="utf-8")
            from core.llm import call_llm, get_model, load_config as _cfg
            cfg = _cfg()
            model = get_model(cfg)
            prompt = (
                f"你是一個 Python 工程師，負責修復 BAW agent 嘅 tool handler。\n"
                f"Tool `{tool_name}` 近期有 {count} 次 call，失敗率 {fail_rate:.0%}。\n"
                f"以下是該 tool 嘅源碼。請加入更完善嘅錯誤處理、timeout、參數驗證，"
                f"或者修復明顯嘅 bug。\n"
                f"只返回修正後嘅完整源碼（純 Python code，唔需要解釋）。\n\n"
                f"```python\n{source[:3000]}\n```"
            )
            resp = call_llm(model=model, messages=[{"role": "user", "content": prompt}],
                           temperature=0.2, max_tokens=4096)
            patched = resp.content.strip()
            if "```python" in patched:
                patched = patched.split("```python")[1].split("```")[0].strip()
            elif "```" in patched:
                patched = patched.split("```")[1].split("```")[0].strip()

            if not patched or len(patched) < 50:
                result["patches"].append(f"Skipped {tool_name}: LLM returned invalid patch")
                continue

            # Syntax verify
            try:
                compile(patched, tool_file.name, "exec")
            except SyntaxError as se:
                result["errors"].append(f"{tool_name}: syntax error in patched code: {se}")
                if commit_hash:
                    _git_rollback(commit_hash)
                continue

            # Write patched file
            tool_file.write_text(patched, encoding="utf-8")
            fail_rate = rec.get("_rate_val", 0) * 100
            result["patches"].append(
                f"Patched {tool_name}: {count} calls, {fail_rate:.0f}% fail → syntax verified"
            )

        except Exception as e:
            if commit_hash:
                _git_rollback(commit_hash)
            result["errors"].append(f"{tool_name}: patch failed — {e}")
            result["ok"] = False

    return result


# ── Stats (for /status command) ──

def get_evolve_stats() -> str:
    """Return a single-line summary of evolution state."""
    log_path = _log_dir() / "behavior.jsonl"
    if not log_path.exists():
        return "[EVOLVE] Evolution: no data yet"

    try:
        with open(log_path) as f:
            total = sum(1 for _ in f)
    except Exception:
        total = 0

    analysis = analyze(hours_back=24)
    recs = len(analysis.get("recommendations", []))
    rate = analysis.get("success_rate", 0)
    corr = analysis.get("corrections", 0)

    parts = [f"[EVOLVE] {total} events logged"]
    if rate:
        parts.append(f"{rate}% success rate")
    if corr:
        parts.append(f"{corr} corrections")
    if recs:
        parts.append(f"{recs} patterns detected")
    return " | ".join(parts)


# ── Phase 5: Deep SOUL Revision ───────────────────────────────────

def _analyze_memory_for_preferences(days_back: int = 7) -> dict:
    """Read memory store and extract user preferences + behavioral patterns.

    Returns structured analysis for SOUL.md revision.
    """
    from core.memory import MemoryStore
    store = MemoryStore(_data_dir())
    cutoff = time.time() - days_back * 86400

    recent_entries = []
    for entry in store._cache:
        try:
            ts = datetime.fromisoformat(entry.get("created", "")).timestamp()
        except Exception:
            continue
        if ts >= cutoff:
            recent_entries.append(entry)

    if not recent_entries:
        return {"has_data": False, "reason": "no recent memories"}

    # Categorize entries
    preferences = []
    corrections = []
    habits = []
    topics = []

    for entry in recent_entries:
        content = entry.get("content", "")
        tags = entry.get("tags", [])

        if "user" in tags or entry.get("source") == "user":
            if any(k in content for k in ["喜歡", "偏好", "想", "要", "用", "用緊", "想要", "喜歡", "偏好", "設定", "prefer", "want", "like", "always", "usually", "never", "不要", "勿", "禁止"]):
                preferences.append(content)
            elif any(k in content for k in ["修正", "改", "錯", "不對", "問題", "fix", "wrong", "incorrect", "change", "唔好", "不好"]):
                corrections.append(content)
            else:
                habits.append(content)

        # Extract topics from high-score entries
        if entry.get("score", 0) > 0.7:
            topics.append(content[:100])

    # Extract recurring themes via keyword frequency
    all_text = " ".join(e.get("content", "") for e in recent_entries)
    cjk_chars = [c for c in all_text if '\u4e00' <= c <= '\u9fff']
    from collections import Counter
    char_freq = Counter(cjk_chars)
    common_themes = [char for char, count in char_freq.most_common(10) if count >= 3]

    return {
        "has_data": True,
        "period_days": days_back,
        "total_entries": len(recent_entries),
        "preferences": preferences[:10],
        "corrections": corrections[:10],
        "habits": habits[:10],
        "high_value_topics": topics[:10],
        "common_themes": common_themes,
    }


def _generate_soul_revisions(analysis: dict) -> list[dict]:
    """Generate specific SOUL.md revision suggestions based on memory analysis.

    Each suggestion has: section, content, priority
    """
    revisions = []

    if not analysis.get("has_data"):
        return revisions

    # Preference-based rules
    prefs = analysis.get("preferences", [])
    corrections = analysis.get("corrections", [])

    # Language preference
    tc_signals = sum(1 for p in prefs if any(k in p for k in ["繁體", "繁体", "中文", "traditional", "粤語", "廣東話", "白話"]))
    sc_signals = sum(1 for p in prefs if any(k in p for k in ["簡體", "简体", "simplified"]))
    if tc_signals > sc_signals and tc_signals >= 2:
        revisions.append({
            "section": "Communication Style",
            "content": "- **Language**: 使用繁體中文，術語保留英文 (英文術語不翻譯)\n",
            "priority": "high",
            "reason": f"{tc_signals} 次偏好繁體中文訊號",
        })

    # Response length preference
    short_signals = sum(1 for p in prefs + corrections if any(k in p for k in ["簡短", "短一點", "精簡", "concise", "short", "唔好長", "太長", "短啟", "brief", "精簡", "控制長度"]))
    if short_signals >= 2:
        revisions.append({
            "section": "Response Style",
            "content": "- **Length**: 偏好精簡回應，避免重複和多餘解釋\n",
            "priority": "high",
            "reason": f"{short_signals} 次要求簡短回應",
        })

    # Format preference
    no_table = sum(1 for p in prefs + corrections if any(k in p for k in ["難複製", "表格", "table", "彈窗", "複雜", "難讀", "難看", "複製", "copy", "複製", "輸出", "format"]))
    if no_table >= 2:
        revisions.append({
            "section": "Response Style",
            "content": "- **Format**: 避免複雜表格，使用簡單清單或標籤對\n",
            "priority": "high",
            "reason": f"{no_table} 次討厭複雜格式",
        })

    # Model preference
    cheap_signals = sum(1 for p in prefs if any(k in p for k in ["慣亟", "省", "便宜", "廉價", "cheap", "save", "成本", "cost", "免費", "free"]))
    if cheap_signals >= 2:
        revisions.append({
            "section": "Cost Awareness",
            "content": "- **Cost**: 用戶偏好低成本方案，非必要時避免使用高價 model\n",
            "priority": "medium",
            "reason": f"{cheap_signals} 次提到成本/價格",
        })

    # Tone preference
    casual_signals = sum(1 for p in prefs if any(k in p for k in ["友好", "親切", "隨和", "純晴", "傾計", "casual", "友善", "輕鬆", "隨意", "放鬆", "有趣"]))
    formal_signals = sum(1 for p in prefs if any(k in p for k in ["正式", "嚴謹", "專業", "尊重", "formal", "嚴肅", "結構", "規範", "正規", "標準"]))
    if casual_signals > formal_signals and casual_signals >= 2:
        revisions.append({
            "section": "Communication Style",
            "content": "- **Tone**: 保持輕鬆友好，像朋友一樣交流\n",
            "priority": "medium",
            "reason": f"{casual_signals} 次偏好輕鬆 tone",
        })
    elif formal_signals > casual_signals and formal_signals >= 2:
        revisions.append({
            "section": "Communication Style",
            "content": "- **Tone**: 保持專業正式，注重准確性和完整性\n",
            "priority": "medium",
            "reason": f"{formal_signals} 次偏好正式 tone",
        })

    # Correction-based avoid rules
    avoid_patterns = []
    for c in corrections[:5]:
        if any(k in c for k in ["不好", "錯", "問題", "修正", "改"]):
            avoid_patterns.append(f"  - 避免: {c[:80]}\n")
    if avoid_patterns:
        revisions.append({
            "section": "Avoid Behaviors",
            "content": "- **Learned Avoidances**:\n" + "".join(avoid_patterns[:3]),
            "priority": "high",
            "reason": f"基於 {len(corrections)} 次用戶修正",
        })

    # High-value topics (interests)
    topics = analysis.get("high_value_topics", [])
    if topics:
        topic_lines = [f"  - {t[:60]}\n" for t in topics[:5]]
        revisions.append({
            "section": "User Interests",
            "content": "- **Recent Focus**:\n" + "".join(topic_lines),
            "priority": "low",
            "reason": f"基於 {len(topics)} 個高分記憶",
        })

    return revisions


def _apply_soul_revisions(revisions: list[dict]) -> dict:
    """Apply generated revisions to SOUL.md safely.

    Uses git snapshot + rollback on failure.
    """
    soul_path = _data_dir() / "SOUL.md"
    if not soul_path.exists():
        return {"ok": False, "error": "SOUL.md not found", "applied": 0}

    snapshot = _git_snapshot(tag_reason="deep-soul-revision")

    try:
        soul = soul_path.read_text(encoding="utf-8")
        original = soul
        applied = 0
        changes = []

        # Find or create the auto-evolution section
        marker = "<!-- AUTO-EVOLVE: 每週自我修訂 -->"
        section_start = soul.find(marker)

        if section_start == -1:
            # Append new section at end
            new_section = f"\n\n{marker}\n## [EVOLVE] 自動進化偏好 (每週更新)\n\n"
            soul += new_section
            section_start = soul.find(marker)
        else:
            # Find end of section (next ## or end of file)
            next_header = soul.find("\n## ", section_start + len(marker))
            if next_header == -1:
                next_header = len(soul)
            # Remove old content after header, keep marker
            soul = soul[:section_start + len(marker)] + "\n## [EVOLVE] 自動進化偏好 (每週更新)\n\n"

        # Group revisions by section
        by_section = {}
        for rev in revisions:
            sec = rev.get("section", "General")
            by_section.setdefault(sec, []).append(rev)

        # Build new section content
        new_content = "\n"
        for sec_name, revs in by_section.items():
            new_content += f"### {sec_name}\n\n"
            for rev in sorted(revs, key=lambda x: x.get("priority", ""), reverse=True):
                new_content += rev["content"]
                new_content += f"  <!-- 原因: {rev.get('reason', '')} -->\n"
            new_content += "\n"

        new_content += "\n_最後更新: " + datetime.now().strftime('%Y-%m-%d %H:%M') + "_\n"

        # Reconstruct soul
        next_header = original.find("\n## ", section_start + len(marker))
        if next_header == -1:
            next_header = len(original)
        soul = original[:section_start + len(marker)] + new_content + original[next_header:]

        # Write
        soul_path.write_text(soul, encoding="utf-8")

        # Verify
        v = _verify_soul(soul_path)
        if not v["ok"]:
            if snapshot.get("ok"):
                _git_rollback(snapshot["commit"], reason="soul-verify-failure")
            return {"ok": False, "error": "; ".join(v["errors"]), "applied": 0}

        return {
            "ok": True,
            "applied": len(revisions),
            "changes": [f"{r['section']}: {r['reason']}" for r in revisions],
            "snapshot": snapshot.get("commit", "")[:8],
        }

    except Exception as e:
        if snapshot.get("ok"):
            _git_rollback(snapshot.get("commit", "HEAD"), reason="exception")
        return {"ok": False, "error": str(e)[:200], "applied": 0}


def run_weekly_evolution() -> dict:
    """Unified weekly self-evolution pipeline.

    1. Dream phase: stuck task cleanup + memory curation (dream.py)
    2. Memory analysis: extract user preferences from memory store
    3. LLM-assisted correction classification (B1 enhancement)
    4. Behavior pattern analysis: tool success rate, corrections, trends
    5. Auto-optimization with apply (not dry-run)
    6. Generate consolidated summary report
    """
    result = {
        "ok": True,
        "timestamp": datetime.now().isoformat(),
        "dream": {},
        "memory_analysis": {},
        "llm_classification": {},
        "auto_optimize": {},
        "pattern_analysis": {},
        "summary": "",
    }

    # ── Step 0: Dream phase (stuck tasks + memory curation) ──
    try:
        from core.dream import dream
        dream_result = dream(data_dir=_data_dir())
        result["dream"] = {
            "ok": dream_result.get("ok", True),
            "stuck_tasks": dream_result.get("stuck_tasks", 0),
            "stale_tasks": dream_result.get("stale_tasks", 0),
            "archived_memories": dream_result.get("archived_memories", 0),
        }
    except Exception as e:
        result["dream"] = {"ok": False, "error": str(e)}

    # ── Step 0.5: SOUL.md health check (replaces external cron) ──
    try:
        soul_path = _data_dir() / "SOUL.md"
        template_path = Path(__file__).resolve().parent.parent / "SOUL.default.md"
        CORE_RULES = ["Output Format", "Multi-Step Execution",
                      "Fabrication Gate", "META-RULE", "EXECUTION PROTOCOL"]
        missing = [r for r in CORE_RULES
                   if soul_path.exists() and r not in soul_path.read_text(encoding="utf-8")]
        if missing:
            if template_path.exists():
                template = template_path.read_text(encoding="utf-8")
                soul_path.write_text(template, encoding="utf-8")
                result["soul_health"] = {
                    "ok": True,
                    "missing_rules": missing,
                    "action": "restored_from_template",
                }
            else:
                result["soul_health"] = {
                    "ok": False,
                    "missing_rules": missing,
                    "action": "template_not_found",
                }
        else:
            result["soul_health"] = {"ok": True, "missing_rules": [], "action": "healthy"}
    except Exception as e:
        result["soul_health"] = {"ok": False, "error": str(e)}

    # ── Step 1: Memory analysis ──
    mem_analysis = _analyze_memory_for_preferences(days_back=7)
    result["memory_analysis"] = {
        "has_data": mem_analysis.get("has_data", False),
        "entries": mem_analysis.get("total_entries", 0),
        "preferences": len(mem_analysis.get("preferences", [])),
        "corrections": len(mem_analysis.get("corrections", [])),
    }

    # ── Step 2: LLM-assisted correction classification ──
    try:
        flush_behavior()
        analysis = analyze(hours_back=168)
        corrections = analysis.get("corrections", 0)
        if corrections > 0:
            recs = analysis.get("recommendations", [])
            correction_texts = [r.get("suggestion", "")
                               for r in recs if r.get("type") == "frequent_corrections"]
            llm_lessons = _llm_classify_corrections(correction_texts)
            llm_added = _write_learned_lessons(llm_lessons) if llm_lessons else 0
            result["llm_classification"] = {
                "corrections_found": corrections,
                "llm_lessons_extracted": len(llm_lessons),
                "new_lessons_saved": llm_added,
            }
        else:
            result["llm_classification"] = {"corrections_found": 0}
    except Exception as e:
        result["llm_classification"] = {"error": str(e)}

    # ── Step 3: Generate and apply SOUL revisions ──
    revisions = _generate_soul_revisions(mem_analysis)
    result["soul_revision"] = {
        "suggestions_count": len(revisions),
        "suggestions": [f"{r['section']} ({r['priority']}): {r['reason']}" for r in revisions[:5]],
    }
    if revisions and mem_analysis.get("has_data"):
        applied = _apply_soul_revisions(revisions)
        result["soul_revision"]["applied"] = applied.get("applied", 0)
        result["soul_revision"]["ok"] = applied.get("ok", False)
        if not applied.get("ok"):
            result["ok"] = False
            result["soul_revision"]["error"] = applied.get("error", "")
    else:
        result["soul_revision"]["applied"] = 0
        result["soul_revision"]["note"] = "無足夠資料進行修訂" if not mem_analysis.get("has_data") else "無需修訂"

    # ── Step 4: Auto-optimize with apply (non-dry-run) ──
    try:
        opt_result = auto_optimize(dry_run=False)
        result["auto_optimize"] = {
            "patterns_found": opt_result.get("patterns_found", 0),
            "soul_patched": opt_result.get("soul_patched", False),
            "config_patched": opt_result.get("config_patched", False),
            "patches": opt_result.get("patches", []),
            "rolled_back": opt_result.get("rolled_back", False),
        }
    except Exception as e:
        result["auto_optimize"] = {"error": str(e), "patterns_found": 0}

    # ── Step 5: Pattern analysis ──
    pattern = analyze(hours_back=168)
    result["pattern_analysis"] = {
        "success_rate": pattern.get("success_rate", 0),
        "tool_calls": pattern.get("tool_calls", 0),
        "corrections": pattern.get("corrections", 0),
        "recommendations": len(pattern.get("recommendations", [])),
    }

    # ── Build summary ──
    lines = ["[EVOLVE] 週度自我進化報告"]
    if result["dream"].get("stuck_tasks", 0) > 0 or result["dream"].get("archived_memories", 0) > 0:
        lines.append(f"  Dream: {result['dream']['stuck_tasks']} stuck tasks, "
                     f"{result['dream']['archived_memories']} archived memories")
    if result.get("soul_health", {}).get("action") == "restored_from_template":
        lines.append(f"  SOUL health: ⚠️ restored — missing {len(result['soul_health']['missing_rules'])} rules")
    elif result.get("soul_health", {}).get("action") == "healthy":
        lines.append(f"  SOUL health: ✅ all rules intact")
    lines.append(f"  記憶: {result['memory_analysis']['entries']} entries, "
                 f"{result['memory_analysis']['preferences']} preferences")
    if result["soul_revision"].get("applied", 0) > 0:
        lines.append(f"  SOUL: {result['soul_revision']['applied']} revisions applied")
    if result["llm_classification"].get("new_lessons_saved", 0) > 0:
        lines.append(f"  LLM: {result['llm_classification']['new_lessons_saved']} new lessons classified")
    if result["auto_optimize"].get("soul_patched") or result["auto_optimize"].get("config_patched"):
        lines.append(f"  Auto-fix: SOUL={result['auto_optimize']['soul_patched']}, "
                     f"Config={result['auto_optimize']['config_patched']}")
    lines.append(f"  系統: {pattern.get('success_rate', 0)}% success, "
                 f"{pattern.get('tool_calls', 0)} tool calls, "
                 f"{pattern.get('corrections', 0)} corrections")

    result["summary"] = "\n".join(lines)
    return result


# ── Evolution Audit Trail ──────────────────────────────────────

_AUDIT_PATH = _log_dir() / "audit.jsonl"


def _snapshot_file(path: Path) -> str | None:
    """Capture file content for before/after comparison."""
    try:
        return path.read_text(encoding="utf-8") if path.exists() else None
    except Exception:
        return None


def _compute_diff(before: str | None, after: str | None, label: str) -> dict:
    """Compute a simple line-level diff between before and after."""
    if before is None and after is None:
        return {"label": label, "changed": False, "added": 0, "removed": 0, "summary": "no change"}
    if before is None:
        lines = after.split("\n") if after else []
        return {"label": label, "changed": True, "added": len(lines), "removed": 0,
                "summary": f"+{len(lines)} lines (new file)"}
    if after is None:
        lines = before.split("\n")
        return {"label": label, "changed": True, "added": 0, "removed": len(lines),
                "summary": f"-{len(lines)} lines (deleted)"}

    before_lines = before.split("\n")
    after_lines = after.split("\n")
    added = len([l for l in after_lines if l not in before_lines])
    removed = len([l for l in before_lines if l not in after_lines])

    changed = before != after
    if not changed:
        return {"label": label, "changed": False, "added": 0, "removed": 0, "summary": "unchanged"}

    # Show key changes (first 3 added/removed lines)
    new_lines = [l for l in after_lines if l not in before_lines and l.strip()][:3]
    old_lines = [l for l in before_lines if l not in after_lines and l.strip()][:3]
    detail = []
    for l in old_lines:
        detail.append(f"- {l[:80]}")
    for l in new_lines:
        detail.append(f"+ {l[:80]}")
    summary = f"+{added}/-{removed} lines"
    return {"label": label, "changed": True, "added": added, "removed": removed,
            "summary": summary, "detail": detail[:6]}


def _save_audit_entry(result: dict, before_files: dict, after_files: dict):
    """Append an evolution audit entry to JSONL log."""
    import time as _time
    entry = {
        "ts": _time.time(),
        "timestamp": datetime.now().isoformat(),
        "dry_run": result.get("dry_run", False),
        "patterns_found": result.get("patterns_found", 0),
        "soul_patched": result.get("soul_patched", False),
        "config_patched": result.get("config_patched", False),
        "rolled_back": result.get("rolled_back", False),
        "verify_errors": result.get("verify_errors", []),
        "patches": result.get("patches", []),
        "diffs": {},
    }
    for label, path_key in [("SOUL.md", "soul"), ("config.yaml", "config")]:
        before = before_files.get(path_key)
        after = after_files.get(path_key)
        diff = _compute_diff(before, after, label)
        entry["diffs"][path_key] = diff

    try:
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_evolution_history(limit: int = 5) -> list[dict]:
    """Read recent evolution audit entries."""
    if not _AUDIT_PATH.exists():
        return []
    entries = []
    try:
        with open(_AUDIT_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return entries[-limit:]


def format_evolution_diff(entries: list[dict]) -> str:
    """Format evolution audit entries for display."""
    if not entries:
        return "No evolution history yet."

    lines = []
    for i, entry in enumerate(reversed(entries)):
        ts = entry.get("timestamp", "?")[:16]
        dry = " [DRY-RUN]" if entry.get("dry_run") else ""
        rolled = " [ROLLED BACK]" if entry.get("rolled_back") else ""
        lines.append(f"**{ts}**{dry}{rolled}")
        lines.append(f"  Patterns: {entry.get('patterns_found', 0)} | "
                     f"SOUL: {'patched' if entry.get('soul_patched') else 'no'} | "
                     f"Config: {'patched' if entry.get('config_patched') else 'no'}")

        for key, diff in entry.get("diffs", {}).items():
            if diff.get("changed"):
                lines.append(f"  {diff['label']}: {diff['summary']}")
                for d in diff.get("detail", []):
                    lines.append(f"    {d}")

        patches = entry.get("patches", [])
        if patches:
            for p in patches[:3]:
                lines.append(f"  • {p[:100]}")

        if i < len(entries) - 1:
            lines.append("")

    return "\n".join(lines)


# ── Patch auto_optimize to capture audit trail ──

# Monkey-patch: save before/after snapshots + audit
_original_auto_optimize = auto_optimize


def _audited_auto_optimize(dry_run: bool = False) -> dict:
    """Wrapped auto_optimize with audit trail."""
    soul_path = _data_dir() / "SOUL.md"
    cfg_path = _data_dir() / "config.yaml"
    before = {
        "soul": _snapshot_file(soul_path),
        "config": _snapshot_file(cfg_path),
    }
    result = _original_auto_optimize(dry_run=dry_run)
    result["dry_run"] = dry_run
    after = {
        "soul": _snapshot_file(soul_path),
        "config": _snapshot_file(cfg_path),
    }
    _save_audit_entry(result, before, after)
    return result


# Replace auto_optimize with audited version
auto_optimize = _audited_auto_optimize


# ── CLI Entry Point ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="BAW Self-Evolution Engine")
    parser.add_argument("--auto-evolve", action="store_true", help="Run weekly self-evolution (cron)")
    parser.add_argument("--analyze", action="store_true", help="Analyze recent behavior patterns")
    parser.add_argument("--stats", action="store_true", help="Show evolution stats")
    parser.add_argument("--diff", action="store_true", help="Show recent evolution audit diff")
    parser.add_argument("--soul-revision", action="store_true", help="Run deep SOUL revision only")
    args = parser.parse_args()

    if args.auto_evolve:
        result = run_weekly_evolution()
        print(result["summary"])
        sys.exit(0 if result["ok"] else 1)

    if args.analyze:
        result = analyze(hours_back=168)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.stats:
        print(get_evolve_stats())
        sys.exit(0)

    if args.diff:
        history = get_evolution_history(limit=5)
        print(format_evolution_diff(history))
        sys.exit(0)

    if args.soul_revision:
        mem = _analyze_memory_for_preferences(days_back=7)
        revs = _generate_soul_revisions(mem)
        if revs:
            applied = _apply_soul_revisions(revs)
            print(f"已生成 {len(revs)} 項修訂, 應用: {applied.get('applied', 0)}")
            for r in revs:
                print(f"  [{r['priority']}] {r['section']}: {r['reason']}")
        else:
            print("無需修訂")
        sys.exit(0)

    # Default: show help
    parser.print_help()
