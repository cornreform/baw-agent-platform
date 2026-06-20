"""P5: Monitoring — Error rate tracker + weekly reliability report.

Tracks: failures/hour per model, avg latency, health score history.
Generates: weekly reliability report.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

MONITOR_DIR = Path.home() / ".baw" / "monitor"
MONITOR_DIR.mkdir(parents=True, exist_ok=True)


def track_error(provider: str, model: str, error_type: str, detail: str = ""):
    """Record an error for monitoring."""
    entry = {
        "ts": time.time(),
        "provider": provider,
        "model": model,
        "error_type": error_type,
        "detail": detail[:200],
    }
    log_path = MONITOR_DIR / "errors.jsonl"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_error_rate(hours: int = 24) -> dict:
    """Get error rate per provider in the last N hours."""
    log_path = MONITOR_DIR / "errors.jsonl"
    if not log_path.exists():
        return {"total": 0, "by_provider": {}, "period_hours": hours}

    cutoff = time.time() - hours * 3600
    by_provider = defaultdict(int)
    total = 0

    try:
        for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                if e.get("ts", 0) > cutoff:
                    by_provider[e.get("provider", "unknown")] += 1
                    total += 1
            except json.JSONDecodeError:
                continue
    except Exception:
        pass

    return {
        "total": total,
        "by_provider": dict(by_provider),
        "period_hours": hours,
        "rate_per_hour": round(total / hours, 1) if hours and total else 0,
    }


def get_health_score_history(days: int = 7) -> list[dict]:
    """Get health score history."""
    log_path = MONITOR_DIR / "health_history.jsonl"
    if not log_path.exists():
        return []

    cutoff = time.time() - days * 86400
    history = []
    try:
        for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                if e.get("ts", 0) > cutoff:
                    history.append(e)
            except json.JSONDecodeError:
                continue
    except Exception:
        pass

    return history


def record_health_score(score: float):
    """Record a health score for history."""
    entry = {
        "ts": time.time(),
        "score": score,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    log_path = MONITOR_DIR / "health_history.jsonl"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def generate_weekly_report() -> str:
    """Generate a weekly reliability report."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    # Error stats
    errors = get_error_rate(hours=168)  # 7 days
    health = get_health_score_history(days=7)

    # Average health score
    avg_score = round(sum(h.get("score", 0) for h in health) / len(health), 1) if health else 0

    lines = [
        f"## [STATS] BAW 每週可靠度報告",
        f"*{week_ago.strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}*",
        "",
        f"### 健康度",
        f"  📈 平均分數: **{avg_score}/10** ({len(health)} 次檢查)",
    ]

    if health:
        scores = [h.get("score", 0) for h in health]
        lines.append(f"  🔺 最高: {max(scores)}/10")
        lines.append(f"  🔻 最低: {min(scores)}/10")

    lines.append("")
    lines.append(f"### 錯誤率 (7 天)")
    lines.append(f"  [STATS] 總錯誤: <b>{errors['total']}</b>")
    lines.append(f"  ⚡ 平均: {errors['rate_per_hour']}/小時")

    if errors["by_provider"]:
        lines.append(f"  Provider 分佈:")
        for provider, count in sorted(errors["by_provider"].items(), key=lambda x: -x[1]):
            lines.append(f"    • {provider}: {count} 次")

    # Assessment
    if avg_score >= 8 and errors["total"] < 10:
        assessment = "[OK] 系統穩定，無重大問題"
    elif avg_score >= 6 and errors["total"] < 50:
        assessment = "🟡 有少量問題，建議定期檢查"
    elif avg_score >= 4:
        assessment = "🟠 需要關注，錯誤率偏高"
    else:
        assessment = "[CRITICAL] 系統不穩定，需要立即處理"

    lines.append(f"\n<b>{assessment}</b>")
    lines.append(f"\n*報告生成: {now.strftime('%Y-%m-%d %H:%M UTC')}*")

    return "\n".join(lines)


def alert_if_high_error_rate(threshold: int = 5, hours: int = 1) -> str | None:
    """Check recent error rate and return alert message if too high.
    Returns None if rate is normal, or an alert string if elevated."""
    rate = get_error_rate(hours=hours)
    if rate["total"] >= threshold:
        providers_detail = ", ".join(
            f"{p}: {c}" for p, c in sorted(rate["by_provider"].items(), key=lambda x: -x[1])
        )
        return (
            f"🚨 <b>BAW 錯誤率偏高</b>\n"
            f"過去 {hours} 小時: **{rate['total']} 次錯誤** (threshold: {threshold})\n"
            f"分佈: {providers_detail}\n"
            f"建議: 執行 /doctor 檢查系統狀態"
        )
    return None
