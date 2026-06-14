"""BAW — Exception tracker for health monitoring.

Lightweight append-only JSONL log of exceptions.
Watchdog reads this to calculate error rates.
"""
from __future__ import annotations
import json
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone

_LOG_FILE = Path.home() / ".baw" / "logs" / "exceptions.jsonl"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def record_exception(exc: Exception, context: str = ""):
    """Record an exception to the JSONL log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "unix": time.time(),
        "type": type(exc).__name__,
        "msg": str(exc),
        "context": context,
        "traceback": traceback.format_exc()[-500:] if __debug__ else "",
    }
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # silently fail — don't crash the crash logger


def count_recent(hours: int = 1) -> tuple[int, list[dict]]:
    """Return (count, recent_entries) from last N hours."""
    if not _LOG_FILE.exists():
        return 0, []
    cutoff = time.time() - (hours * 3600)
    entries = []
    try:
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("unix", 0) >= cutoff:
                        entries.append(e)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return 0, []
    return len(entries), entries


def summary(hours: int = 24) -> str:
    count, entries = count_recent(hours)
    if count == 0:
        return f"✅ No exceptions in last {hours}h"
    types: dict[str, int] = {}
    for e in entries:
        t = e.get("type", "Unknown")
        types[t] = types.get(t, 0) + 1
    top = sorted(types.items(), key=lambda x: x[1], reverse=True)[:3]
    top_str = ", ".join(f"{t}({c})" for t, c in top)
    return f"🚨 {count} exception(s) in last {hours}h. Top: {top_str}"
