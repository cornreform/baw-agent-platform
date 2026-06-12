"""M4: Court docket — per-user concurrent case queue.

Fable 5 spec §4: 「每用戶並行案件 2,第 3 單入 docket;全系統並行 sub-agent 4;
Tier 0 永不排隊;優先級 用戶互動 > cron > backlog;同級 FIFO」

This module is the queueing layer. The agent loop (or the cron runner,
or the Telegram bot) calls docket.enqueue(case_meta) and gets a
docket position back. The main loop polls docket.get_next_ready()
and yields cases to court.file_case() when a slot opens.

State is persisted to ~/.baw/court/docket.jsonl (append-only log)
plus a snapshot at ~/.baw/court/docket_state.json for fast restart.
"""

from __future__ import annotations

import json
import time
import uuid
import fcntl
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict
from enum import Enum


# ── Config (Fable 5 spec, §4 rules) ────────────────────────────────

DOCKET_DIR = Path.home() / ".baw" / "court"
DOCKET_LOG = DOCKET_DIR / "docket.jsonl"          # append-only
DOCKET_STATE = DOCKET_DIR / "docket_state.json"   # snapshot

MAX_CONCURRENT_PER_USER = 2
MAX_CONCURRENT_SYSTEM = 4
TIER0_NEVER_QUEUED = True
CIRCUIT_NIGHTLY_HOUR = 3  # cron: 03:00


class Priority(str, Enum):
    USER_INTERACTIVE = "user"     # user-initiated (highest)
    CRON = "cron"                  # scheduled (middle)
    BACKLOG = "backlog"            # retry / rescheduled (lowest)


@dataclass
class DocketEntry:
    """One queued case waiting for a slot."""
    queue_id: str
    case_id: str
    user_id: str
    priority: Priority
    tier: int
    goal_preview: str  # first 80 chars
    enqueued_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    status: str = "queued"  # queued | running | done | failed | cancelled


# ── File-lock helper (multi-process safe) ────────────────────────

class _DocketLock:
    """Advisory file lock so concurrent processes don't corrupt the docket."""

    def __init__(self):
        DOCKET_DIR.mkdir(parents=True, exist_ok=True)
        self._lock_path = DOCKET_DIR / ".docket.lock"
        self._lock_path.touch(exist_ok=True)
        self._fd = None

    def __enter__(self):
        self._fd = open(self._lock_path, "r")
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None


# ── Snapshot IO ─────────────────────────────────────────────────

def _load_snapshot() -> dict:
    """Read the current docket state. Empty if no snapshot exists."""
    if not DOCKET_STATE.exists():
        return {"entries": [], "running": []}
    try:
        return json.loads(DOCKET_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": [], "running": []}


def _save_snapshot(state: dict) -> None:
    """Persist the docket state atomically (write-temp + rename)."""
    DOCKET_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DOCKET_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DOCKET_STATE)


def _append_log(entry: dict) -> None:
    """Append-only audit log. Not used for state recovery, just history."""
    DOCKET_DIR.mkdir(parents=True, exist_ok=True)
    with open(DOCKET_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Public API ──────────────────────────────────────────────────

def enqueue(case_id: str, user_id: str, tier: int, goal: str,
            priority: Priority = Priority.USER_INTERACTIVE) -> DocketEntry:
    """Add a case to the docket. Returns the DocketEntry (with status)."""
    entry = DocketEntry(
        queue_id=f"Q{int(time.time()*1000):013d}{uuid.uuid4().hex[:6]}",
        case_id=case_id,
        user_id=user_id,
        priority=priority,
        tier=tier,
        goal_preview=goal[:80],
        enqueued_at=time.time(),
        status="queued",
    )

    # Tier 0 short-circuit: never queued, mark as immediately running.
    if TIER0_NEVER_QUEUED and tier == 0:
        entry.status = "running"
        entry.started_at = time.time()

    with _DocketLock():
        state = _load_snapshot()
        state["entries"].append(asdict(entry))
        _save_snapshot(state)
        _append_log({"event": "enqueue", **asdict(entry)})

    return entry


def get_queue_position(case_id: str) -> Optional[int]:
    """Return the queue position (1-indexed) for a queued case, or None if running/done."""
    state = _load_snapshot()
    queued = [e for e in state["entries"] if e["status"] == "queued"]
    for i, e in enumerate(queued, 1):
        if e["case_id"] == case_id:
            return i
    return None


def get_next_ready() -> Optional[DocketEntry]:
    """Return the next case to run, respecting concurrency limits and priority.

    Rules (Fable 5 §4):
      1. Per-user concurrency cap: skip if user already has MAX_CONCURRENT_PER_USER running.
      2. System-wide concurrency cap: skip if total running >= MAX_CONCURRENT_SYSTEM.
      3. Tier 0 always ready (short-circuited at enqueue time, but checked again).
      4. Priority order: user > cron > backlog.
      5. Same-priority FIFO (oldest enqueued_at first).
    """
    state = _load_snapshot()
    running = state.get("running", [])
    entries = state.get("entries", [])

    # Count running by user
    by_user: dict[str, int] = {}
    for r in running:
        by_user[r["user_id"]] = by_user.get(r["user_id"], 0) + 1

    # Candidate: queued entries, sorted by priority then FIFO
    queued = [e for e in entries if e["status"] == "queued"]
    priority_order = {Priority.USER_INTERACTIVE.value: 0, Priority.CRON.value: 1, Priority.BACKLOG.value: 2}
    queued.sort(key=lambda e: (priority_order.get(e.get("priority", "backlog"), 3),
                                e.get("enqueued_at", 0)))

    for e in queued:
        user_id = e.get("user_id", "default")
        # Per-user cap
        if by_user.get(user_id, 0) >= MAX_CONCURRENT_PER_USER:
            continue
        # System cap
        if len(running) >= MAX_CONCURRENT_SYSTEM:
            return None
        return DocketEntry(**e)
    return None


def mark_running(queue_id: str) -> bool:
    """Move a queued entry to running. Returns True if successful."""
    with _DocketLock():
        state = _load_snapshot()
        for e in state.get("entries", []):
            if e["queue_id"] == queue_id and e["status"] == "queued":
                e["status"] = "running"
                e["started_at"] = time.time()
                state.setdefault("running", []).append(e)
                _save_snapshot(state)
                _append_log({"event": "running", "queue_id": queue_id})
                return True
    return False


def mark_done(queue_id: str, success: bool = True) -> None:
    """Move a running entry to done/failed."""
    with _DocketLock():
        state = _load_snapshot()
        for e in state.get("entries", []):
            if e["queue_id"] == queue_id and e["status"] == "running":
                e["status"] = "done" if success else "failed"
                e["completed_at"] = time.time()
                # Remove from running list
                state["running"] = [r for r in state.get("running", []) if r["queue_id"] != queue_id]
                _save_snapshot(state)
                _append_log({"event": "done" if success else "failed", "queue_id": queue_id})
                return


def cancel(queue_id: str) -> bool:
    """Cancel a queued or running case. Returns True if cancelled."""
    with _DocketLock():
        state = _load_snapshot()
        for e in state.get("entries", []):
            if e["queue_id"] == queue_id and e["status"] in ("queued", "running"):
                e["status"] = "cancelled"
                e["completed_at"] = time.time()
                state["running"] = [r for r in state.get("running", []) if r["queue_id"] != queue_id]
                _save_snapshot(state)
                _append_log({"event": "cancelled", "queue_id": queue_id})
                return True
    return False


def get_status() -> dict:
    """Return docket stats for /court stats and dashboards."""
    state = _load_snapshot()
    entries = state.get("entries", [])
    running = state.get("running", [])
    queued = [e for e in entries if e["status"] == "queued"]
    done_today = [e for e in entries
                  if e["status"] in ("done", "failed")
                  and e.get("completed_at", 0) >= time.time() - 86400]
    return {
        "queued": len(queued),
        "running": len(running),
        "done_today": len(done_today),
        "max_concurrent_system": MAX_CONCURRENT_SYSTEM,
        "max_concurrent_per_user": MAX_CONCURRENT_PER_USER,
        "users_currently_running": list({r["user_id"] for r in running}),
    }


def pickup_crashed() -> int:
    """Recover entries that were 'running' when the process died.

    Called at startup. Marks all 'running' entries as 'queued' again so
    they get re-dispatched. Returns the number of recovered entries.
    """
    recovered = 0
    with _DocketLock():
        state = _load_snapshot()
        for e in state.get("entries", []):
            if e["status"] == "running":
                e["status"] = "queued"
                e["started_at"] = None
                recovered += 1
        # Clear running list (all entries are queued again)
        state["running"] = []
        if recovered:
            _save_snapshot(state)
            _append_log({"event": "pickup_crashed", "recovered": recovered})
    return recovered
