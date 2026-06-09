"""
BAW — Dreaming: On-Hold Task Checker + Light Memory Curation
Reads task manager state, flags stuck/on-hold tasks, does light memory cleanup.
Silent unless there's something to report.
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone


def dream(data_dir: Path, dry_run: bool = False) -> dict:
    """
    Weekly dream pass.
    1. Check for on-hold/stuck tasks (PRIMARY)
    2. Light memory curation (secondary, fast path only)
    Returns a report dict.
    """
    tasks_dir = data_dir / "tasks"
    memory_path = data_dir / "memory" / "store.jsonl"
    soul_path = data_dir / "SOUL.md"
    report = {
        "on_hold_tasks": [],
        "stale_tasks": [],
        "memory_archived": 0,
        "changes": [],
    }

    # ── Step 1: Check for on-hold/stuck tasks (PRIMARY) ──
    if tasks_dir.exists():
        now = time.time()
        for d in sorted(tasks_dir.iterdir()):
            if not d.is_dir():
                continue

            status_file = d / "status.txt"
            pid_file = d / "pid.txt"
            prompt_file = d / "prompt.txt"

            status = ""
            if status_file.exists():
                status = status_file.read_text(encoding="utf-8").strip()

            prompt = ""
            if prompt_file.exists():
                prompt = prompt_file.read_text(encoding="utf-8").strip()[:80]

            # ── Check 1: Stuck "running" tasks with dead PIDs ──
            if status == "running":
                is_stuck = False
                reason = ""
                if pid_file.exists():
                    try:
                        pid = int(pid_file.read_text().strip())
                        os.kill(pid, 0)  # Check if alive
                    except (ValueError, OSError, ProcessLookupError):
                        is_stuck = True
                        reason = "PID dead — process crashed without cleanup"
                else:
                    # No PID file — task was started but never wrote PID
                    # Check how old the task is
                    try:
                        mtime = os.path.getmtime(status_file)
                        age_hours = (now - mtime) / 3600
                        if age_hours > 1:  # Over 1 hour "running" with no PID = stuck
                            is_stuck = True
                            reason = f"No PID file, running for {age_hours:.1f}h — likely orphaned"
                    except OSError:
                        pass

                if is_stuck:
                    report["on_hold_tasks"].append({
                        "id": d.name,
                        "prompt": prompt,
                        "status": status,
                        "reason": reason,
                    })
                    # Auto-fix: mark as failed
                    if not dry_run:
                        (d / "status.txt").write_text(
                            f"failed (orphaned: {reason})", encoding="utf-8"
                        )
                    report["changes"].append(
                        f"Task '{d.name}' stuck (running with {reason}) → auto-marked failed"
                    )

            # ── Check 2: Very old tasks (>7 days, any non-done status) ──
            if status and status not in ("done", "archived"):
                try:
                    mtime = os.path.getmtime(status_file) if status_file.exists() else 0
                    age_days = (now - mtime) / 86400
                    if age_days > 7:
                        report["stale_tasks"].append({
                            "id": d.name,
                            "prompt": prompt,
                            "status": status,
                            "age_days": round(age_days, 1),
                        })
                        report["changes"].append(
                            f"Stale task '{d.name}' — {status} for {age_days:.0f} days"
                        )
                except OSError:
                    pass

    # ── Step 2: Light memory curation (fast path only) ──
    if memory_path.exists():
        try:
            lines = memory_path.read_text(encoding="utf-8").strip().split("\n")
            low_score = []
            kept = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    score = entry.get("score", 0)
                    # Only archive truly dead memories (score < 0.05, not just 0.15)
                    if score < 0.05:
                        low_score.append(entry)
                    else:
                        kept.append(line)
                except json.JSONDecodeError:
                    kept.append(line)

            if low_score and not dry_run:
                archive_path = (
                    data_dir
                    / "memory"
                    / f"archive-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
                )
                with open(archive_path, "w", encoding="utf-8") as f:
                    for entry in low_score:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                with open(memory_path, "w", encoding="utf-8") as f:
                    for line in kept:
                        f.write(line + "\n")
                report["memory_archived"] = len(low_score)
                report["changes"].append(
                    f"Archived {len(low_score)} near-zero memories"
                )
        except Exception as e:
            report["changes"].append(f"Memory curation skipped: {e}")

    # ── Step 3: Dream timestamp ──
    if not dry_run and report["changes"]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if soul_path.exists():
            soul = soul_path.read_text(encoding="utf-8")
            if "<!-- last-dream:" in soul:
                soul = re.sub(
                    r"<!-- last-dream:.*?-->",
                    f"<!-- last-dream: {today} -->",
                    soul,
                )
            else:
                soul += f"\n\n<!-- last-dream: {today} -->"
            soul_path.write_text(soul, encoding="utf-8")

    # ── Step 4: Write dream log ──
    if report["changes"]:
        _write_dream_log(data_dir, report)

    return report


def _write_dream_log(data_dir: Path, report: dict):
    """Write human-readable dream log."""
    log_path = data_dir / "dream-log.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [f"## Dream — {now}", ""]

    if report["on_hold_tasks"]:
        lines.append("### 🔴 On-Hold Tasks (Stuck)")
        lines.append("")
        for t in report["on_hold_tasks"]:
            lines.append(f"- **{t['id']}** — `{t['status']}`")
            lines.append(f"  - Prompt: {t['prompt']}")
            lines.append(f"  - Reason: {t['reason']}")
        lines.append("")

    if report["stale_tasks"]:
        lines.append("### 🟡 Stale Tasks (>7 days)")
        lines.append("")
        for t in report["stale_tasks"]:
            lines.append(
                f"- **{t['id']}** — `{t['status']}` ({t['age_days']}d old)\n"
                f"  - {t['prompt']}"
            )
        lines.append("")

    if report["memory_archived"]:
        lines.append(f"### 📦 Memory — {report['memory_archived']} entries archived")
        lines.append("")

    if not any([report["on_hold_tasks"], report["stale_tasks"], report["memory_archived"]]):
        lines.append("✅ No issues found. All tasks healthy, memory clean.")
        lines.append("")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── time.time() for dream.py standalone use ──
import time
