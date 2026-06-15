"""P3: Health Dashboard — 10-point system health check.

Produces a single score 0-10 with per-subsystem breakdown.
Callable via `baw --doctor` or programmatically.
"""
from __future__ import annotations

import time
import json
import os
from pathlib import Path
from datetime import datetime, timezone


def health_check() -> dict:
    """Run a 10-point health check. Returns {score, checks, timestamp}."""
    checks = []
    total_score = 0
    max_score = 0

    # 1. Config file (1 point)
    config_path = Path.home() / ".baw" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            yaml.safe_load(config_path.read_text(encoding="utf-8"))
            checks.append({"name": "config", "score": 1, "status": "ok"})
            total_score += 1
        except Exception as e:
            checks.append({"name": "config", "score": 0, "status": "error", "detail": str(e)[:100]})
    else:
        checks.append({"name": "config", "score": 0, "status": "missing"})
    max_score += 1

    # 2. Environment / API keys (1 point)
    env_path = Path.home() / ".baw" / ".env"
    if env_path.exists():
        env_text = env_path.read_text(encoding="utf-8")
        key_count = sum(1 for line in env_text.split("\n") if "=" in line and not line.startswith("#"))
        if key_count >= 2:
            checks.append({"name": "api_keys", "score": 1, "status": "ok", "detail": f"{key_count} keys"})
            total_score += 1
        else:
            checks.append({"name": "api_keys", "score": 0, "status": "warning", "detail": f"only {key_count} keys"})
    else:
        checks.append({"name": "api_keys", "score": 0, "status": "missing"})
    max_score += 1

    # 3. Memory store (1 point)
    mem_path = Path.home() / ".baw" / "memory" / "store.jsonl"
    if mem_path.exists():
        try:
            lines = mem_path.read_text(encoding="utf-8").strip().split("\n")
            entries = [l for l in lines if l.strip()]
            checks.append({"name": "memory", "score": 1, "status": "ok", "detail": f"{len(entries)} entries"})
            total_score += 1
        except Exception:
            checks.append({"name": "memory", "score": 0, "status": "error"})
    else:
        checks.append({"name": "memory", "score": 0, "status": "missing"})
    max_score += 1

    # 4. Cron daemon (1 point)
    schedule_path = Path.home() / ".baw" / "schedule.yaml"
    state_path = Path.home() / ".baw" / "schedule_state.json"
    if schedule_path.exists() and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if len(state) >= 2:
                checks.append({"name": "cron", "score": 1, "status": "ok", "detail": f"{len(state)} jobs"})
                total_score += 1
            else:
                checks.append({"name": "cron", "score": 0, "status": "warning", "detail": f"only {len(state)} jobs"})
        except Exception:
            checks.append({"name": "cron", "score": 0, "status": "error"})
    else:
        checks.append({"name": "cron", "score": 0, "status": "missing"})
    max_score += 1

    # 5. Model health — check last latency log (1 point)
    latency_path = Path.home() / ".baw" / "logs" / "latency.jsonl"
    if latency_path.exists():
        try:
            lines = latency_path.read_text(encoding="utf-8").strip().split("\n")
            recent = [json.loads(l) for l in lines[-20:] if l.strip()]
            ok_count = sum(1 for r in recent if r.get("status") == "ok")
            if ok_count >= len(recent) * 0.5:
                checks.append({"name": "models", "score": 1, "status": "ok", "detail": f"{ok_count}/{len(recent)} ok"})
                total_score += 1
            else:
                checks.append({"name": "models", "score": 0, "status": "warning", "detail": f"only {ok_count}/{len(recent)} ok"})
        except Exception:
            checks.append({"name": "models", "score": 0, "status": "error"})
    else:
        checks.append({"name": "models", "score": 0, "status": "missing", "detail": "no latency log yet"})
    max_score += 1

    # 6. Exceptions (1 point — fewer is better)
    exc_path = Path.home() / ".baw" / "logs" / "exceptions.jsonl"
    if exc_path.exists():
        try:
            lines = exc_path.read_text(encoding="utf-8").strip().split("\n")
            recent_24h = 0
            cutoff = time.time() - 86400
            for l in lines:
                if not l.strip():
                    continue
                try:
                    e = json.loads(l)
                    if e.get("ts", 0) > cutoff:
                        recent_24h += 1
                except Exception:
                    pass
            if recent_24h <= 10:
                checks.append({"name": "exceptions", "score": 1, "status": "ok", "detail": f"{recent_24h} in 24h"})
                total_score += 1
            elif recent_24h <= 50:
                checks.append({"name": "exceptions", "score": 0.5, "status": "warning", "detail": f"{recent_24h} in 24h"})
                total_score += 0.5
            else:
                checks.append({"name": "exceptions", "score": 0, "status": "error", "detail": f"{recent_24h} in 24h"})
        except Exception:
            checks.append({"name": "exceptions", "score": 0, "status": "error"})
    else:
        checks.append({"name": "exceptions", "score": 1, "status": "ok", "detail": "no exceptions"})
        total_score += 1
    max_score += 1

    # 7. Court system (1 point)
    court_dir = Path.home() / ".baw" / "court" / "cases"
    if court_dir.exists():
        case_files = list(court_dir.glob("*.json"))
        checks.append({"name": "court", "score": 1, "status": "ok", "detail": f"{len(case_files)} cases"})
        total_score += 1
    else:
        checks.append({"name": "court", "score": 0, "status": "missing", "detail": "no court cases yet"})
    max_score += 1

    # 8. Skills (1 point)
    skills_dir = Path.home() / ".baw" / "skills"
    if skills_dir.exists():
        skill_files = list(skills_dir.rglob("SKILL.md")) + list(skills_dir.rglob("*.yaml"))
        if len(skill_files) >= 1:
            checks.append({"name": "skills", "score": 1, "status": "ok", "detail": f"{len(skill_files)} skills"})
            total_score += 1
        else:
            checks.append({"name": "skills", "score": 0.5, "status": "warning", "detail": "no skills"})
            total_score += 0.5
    else:
        checks.append({"name": "skills", "score": 0, "status": "missing"})
    max_score += 1

    # 9. Backups (1 point)
    backup_dir = Path.home() / ".baw" / "backups"
    if backup_dir.exists():
        backups = list(backup_dir.glob("*.tar.gz"))
        if backups:
            newest = max(b.stat().st_mtime for b in backups)
            age_hours = (time.time() - newest) / 3600
            if age_hours < 48:
                checks.append({"name": "backups", "score": 1, "status": "ok", "detail": f"{len(backups)} backups"})
                total_score += 1
            else:
                checks.append({"name": "backups", "score": 0.5, "status": "warning", "detail": f"oldest: {age_hours:.0f}h"})
                total_score += 0.5
        else:
            checks.append({"name": "backups", "score": 0, "status": "missing", "detail": "no backups yet"})
    else:
        checks.append({"name": "backups", "score": 0, "status": "missing", "detail": "no backup dir"})
    max_score += 1

    # 10. Uptime / process running (1 point)
    try:
        import subprocess
        result = subprocess.run(["pgrep", "-f", "baw-bot"], capture_output=True, text=True)
        if result.stdout.strip():
            checks.append({"name": "process", "score": 1, "status": "ok", "detail": "baw-bot running"})
            total_score += 1
        else:
            checks.append({"name": "process", "score": 0, "status": "error", "detail": "baw-bot not running"})
    except Exception:
        checks.append({"name": "process", "score": 0.5, "status": "unknown"})
        total_score += 0.5
    max_score += 1

    return {
        "score": round(total_score, 1),
        "max_score": max_score,
        "percentage": round(total_score / max_score * 100, 0) if max_score else 0,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def format_health_report(result: dict) -> str:
    """Format health check result as a readable string."""
    lines = [
        f"## 🏥 BAW Health: {result['score']}/{result['max_score']} ({result['percentage']:.0f}%)",
        f"*{result['timestamp'][:19]}*",
        "",
    ]
    for c in result["checks"]:
        emoji = {"ok": "✅", "warning": "⚠️", "error": "❌", "missing": "❓", "unknown": "❓"}
        e = emoji.get(c["status"], "❓")
        detail = f" — {c['detail']}" if c.get("detail") else ""
        lines.append(f"  {e} **{c['name']}**: {c['status']}{detail}")

    # Overall assessment
    score = result["score"]
    if score >= 9:
        assessment = "🟢 系統健康，一切正常"
    elif score >= 7:
        assessment = "🟡 有小問題，建議檢查"
    elif score >= 5:
        assessment = "🟠 需要關注，部分系統異常"
    else:
        assessment = "🔴 系統需要緊急修復"
    lines.append(f"\n**{assessment}**")

    return "\n".join(lines)
