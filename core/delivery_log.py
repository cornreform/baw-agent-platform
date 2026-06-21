"""BAW — Delivery Confirmation Log

Tracks every message sent to users and whether it was delivered.
Survives restarts via append-only JSONL log.

Usage:
    from core.delivery_log import record_send, record_error, recent_deliveries

    msg_id = record_send(chat_id="123", platform="telegram", content="Hello")
    # ... later if delivery confirmed:
    record_error(msg_id, "telegram", "HTTP 403 Forbidden")
"""
from __future__ import annotations
import json
import logging
import time
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("baw.delivery")

_LOG_DIR = Path.home() / ".baw" / "logs"
_MAX_ENTRIES = 10_000  # auto-prune after this many


def _log_path() -> Path:
    """Compute delivery log path from current _LOG_DIR."""
    return _LOG_DIR / "delivery.jsonl"


def _ensure_log():
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


def record_send(
    chat_id: str,
    platform: str,
    content: str,
    msg_type: str = "text",
    metadata: dict | None = None,
) -> int:
    """Record a message being sent. Returns entry_id (timestamp-based)."""
    _ensure_log()
    entry = {
        "ts": time.time(),
        "chat_id": chat_id,
        "platform": platform,
        "type": msg_type,
        "content_preview": content[:200],
        "content_len": len(content),
        "status": "sent",
        "error": None,
        "metadata": metadata or {},
    }
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _maybe_prune()
    except OSError as e:
        logger.warning(f"[Delivery] Failed to write log: {e}")
    return int(entry["ts"] * 1000)  # entry_id = ms timestamp


def record_error(
    entry_id: int,
    platform: str,
    error: str,
    fatal: bool = False,
):
    """Update a previously recorded send with an error/delivery failure."""
    _ensure_log()
    entry = {
        "ts": time.time(),
        "entry_id": entry_id,
        "platform": platform,
        "status": "error" if not fatal else "fatal",
        "error": error[:500],
    }
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"[Delivery] Failed to write error: {e}")


def record_delivery_confirmation(
    entry_id: int,
    platform: str,
    confirm_data: dict | None = None,
):
    """Record delivery confirmation (e.g. Telegram message ID)."""
    _ensure_log()
    entry = {
        "ts": time.time(),
        "entry_id": entry_id,
        "platform": platform,
        "status": "delivered",
        "confirm": confirm_data or {},
    }
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning(f"[Delivery] Failed to write confirmation: {e}")


def recent_deliveries(minutes: int = 60, limit: int = 50) -> list[dict]:
    """Get recent delivery records, newest first."""
    _ensure_log()
    if not _log_path().exists():
        return []
    cutoff = time.time() - (minutes * 60)
    entries = []
    try:
        with open(_log_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", 0) >= cutoff:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return entries[:limit]


def delivery_stats(minutes: int = 60) -> dict:
    """Aggregate delivery stats for the given time window."""
    entries = recent_deliveries(minutes=minutes, limit=10_000)
    total = len(entries)
    sends = [e for e in entries if e.get("status") == "sent"]
    delivered = [e for e in entries if e.get("status") == "delivered"]
    errors = [e for e in entries if e.get("status") in ("error", "fatal")]
    fatal = [e for e in entries if e.get("status") == "fatal"]
    return {
        "window_minutes": minutes,
        "total_entries": total,
        "sent": len(sends),
        "delivered": len(delivered),
        "errors": len(errors),
        "fatal_errors": len(fatal),
        "delivery_rate": f"{len(delivered)/max(total,1)*100:.1f}%" if total else "N/A",
    }


def _maybe_prune():
    """Keep _MAX_ENTRIES at most by rewriting the log."""
    try:
        if not _log_path().exists():
            return
        with open(_log_path(), "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _MAX_ENTRIES:
            return
        # Keep newest entries
        tail = lines[-_MAX_ENTRIES:]
        with open(_log_path(), "w", encoding="utf-8") as f:
            f.writelines(tail)
        logger.info(f"[Delivery] Pruned log: {len(lines)} → {len(tail)} entries")
    except OSError:
        pass
