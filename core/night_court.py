"""M4: 巡迴法庭夜報 — nightly summary of all court activity.

Fable 5 spec §4: 「一日一條摘要,唔好半夜彈 6 條 notification。」

Called by the cron job at 03:00. Reads the court case archive and
docket state, formats a single Telegram-friendly message with the
previous day's verdicts and any deferred issues. Returns the formatted
text — the cron job then sends it to User's Telegram via the bot.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional


def format_nightly_summary(now: Optional[float] = None) -> str:
    """Return the previous-24-hour court activity summary as a string.

    Args:
        now: epoch seconds for "now". Defaults to time.time(). Exposed
             for testing so the test can pin a fixed time.

    Returns:
        Multi-line string ready to send to Telegram. Always includes
        a header line so the user immediately knows what the message is.
    """
    now = now or time.time()
    archive_dir = Path.home() / ".baw" / "court" / "cases"
    cutoff = now - 86400  # 24h window

    # Read case archive
    today = []
    for p in sorted(archive_dir.glob("*.json")):
        try:
            import json
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("created_at", 0) >= cutoff:
            today.append(data)
    today.sort(key=lambda c: c.get("created_at", 0))

    lines = [
        f"🌙 巡迴法庭夜報 ({time.strftime('%Y-%m-%d', time.localtime(now))})",
        f"  過去 24 小時審案: {len(today)} 單",
    ]

    if not today:
        lines.append("  今日無案。")
        return "\n".join(lines)

    # Verdict breakdown
    verdicts: dict[str, int] = {}
    total_score = 0
    total_elapsed = 0.0
    for c in today:
        v = c.get("verdict", "unknown")
        verdicts[v] = verdicts.get(v, 0) + 1
        total_score += c.get("score", 0)
        total_elapsed += c.get("elapsed_sec", 0)
    avg_score = total_score / max(len(today), 1)
    avg_elapsed = total_elapsed / max(len(today), 1)

    # Tier breakdown
    by_tier: dict[int, int] = {}
    for c in today:
        t = c.get("tier", 0)
        by_tier[t] = by_tier.get(t, 0) + 1

    lines.append(f"  核准率: {100 * verdicts.get('approved', 0) / len(today):.0f}%")
    lines.append(
        f"  🔁 {verdicts.get('retry', 0)}  ·  📤 {verdicts.get('appeal', 0)}  ·  "
        f"🚫 {verdicts.get('dismissed', 0)}  ·  ⏸️ {verdicts.get('stay', 0)}"
    )
    lines.append(f"  平均 verdict: {avg_score:.1f}/10 · 平均 latency: {avg_elapsed:.1f}s")
    lines.append(
        f"  Tier: 0️⃣{by_tier.get(0,0)}  1️⃣{by_tier.get(1,0)}  2️⃣{by_tier.get(2,0)}  3️⃣{by_tier.get(3,0)}"
    )

    # Top 3 cases (longest or lowest-scoring)
    notable = sorted(today, key=lambda c: (c.get("score", 10), -c.get("elapsed_sec", 0)))[:3]
    if notable:
        lines.append("")
        lines.append("  📌 今日矚目:")
        emoji = {"approved": "✅", "retry": "🔁", "appeal": "📤",
                 "dismissed": "🚫", "stay": "⏸️"}
        for c in notable:
            e = emoji.get(c.get("verdict"), "⏳")
            goal = (c.get("goal") or "")[:50]
            lines.append(f"    {e} {c['case_id']} │ {c.get('score', '?')}/10 │ {c.get('elapsed_sec', 0):.0f}s │ {goal}")

    return "\n".join(lines)
