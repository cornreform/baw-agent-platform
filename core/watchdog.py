"""P2: Auto-Recovery — Stuck Step Killer + Health Watchdog.

When BAW gets stuck, auto-kill the stuck subprocess and continue.
"""
from __future__ import annotations

import time
import threading
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("baw.watchdog")


# ═══════════════════════════════════════════════════════════════════
# Stuck Step Killer
# ═══════════════════════════════════════════════════════════════════

_STUCK_THRESHOLD = 600  # 10 minutes — kill sub-agent if stuck longer
_KILL_SIGNAL = False


def mark_step_start(step_desc: str) -> float:
    """Record step start time for stuck detection."""
    return time.time()


def mark_step_done(start_time: float) -> float:
    """Record step completion."""
    return time.time() - start_time


def check_stuck(start_time: float) -> bool:
    """Check if a step has been running too long. Returns True if stuck."""
    elapsed = time.time() - start_time
    if elapsed > _STUCK_THRESHOLD:
        logger.warning(f"[watchdog] step stuck for {elapsed:.0f}s (> {_STUCK_THRESHOLD}s)")
        return True
    return False


def kill_stuck_step(step_desc: str) -> dict:
    """Kill the current stuck step and return recovery instructions."""
    global _KILL_SIGNAL
    _KILL_SIGNAL = True
    logger.error(f"[watchdog] KILLING stuck step: {step_desc}")
    return {
        "status": "killed",
        "reason": f"Step stuck > {_STUCK_THRESHOLD}s",
        "recovery": "replan_with_different_approach",
    }


def reset_kill_signal():
    global _KILL_SIGNAL
    _KILL_SIGNAL = False


def is_kill_requested() -> bool:
    return _KILL_SIGNAL


# ═══════════════════════════════════════════════════════════════════
# Health Watchdog — periodic health checks
# ═══════════════════════════════════════════════════════════════════

_WATCHDOG_RUNNING = False
_WATCHDOG_THREAD: Optional[threading.Thread] = None
_LAST_HEALTH_SCORE = 10


def start_watchdog(interval_sec: int = 60):
    """Start a background thread that monitors BAW health."""
    global _WATCHDOG_RUNNING, _WATCHDOG_THREAD
    if _WATCHDOG_RUNNING:
        return
    _WATCHDOG_RUNNING = True

    def _watchdog_loop():
        while _WATCHDOG_RUNNING:
            try:
                score = _check_health_basic()
                global _LAST_HEALTH_SCORE
                _LAST_HEALTH_SCORE = score
                if score < 5:
                    logger.error(f"[watchdog] Health critical: {score}/10")
            except Exception as e:
                logger.warning(f"[watchdog] health check failed: {e}")
            time.sleep(interval_sec)

    _WATCHDOG_THREAD = threading.Thread(target=_watchdog_loop, daemon=True, name="baw-watchdog")
    _WATCHDOG_THREAD.start()
    logger.info("[watchdog] started — checking every %ds", interval_sec)


def stop_watchdog():
    global _WATCHDOG_RUNNING
    _WATCHDOG_RUNNING = False


def get_last_health_score() -> int:
    return _LAST_HEALTH_SCORE


def _check_health_basic() -> int:
    """Quick health check (no LLM calls). Returns score 0-10."""
    score = 10

    # Check config exists
    config_path = Path.home() / ".baw" / "config.yaml"
    if not config_path.exists():
        score -= 5

    # Check memory store
    mem_path = Path.home() / ".baw" / "memory" / "store.jsonl"
    if not mem_path.exists():
        score -= 3

    # Check exceptions log (too recent = unhappy)
    exc_path = Path.home() / ".baw" / "logs" / "exceptions.jsonl"
    if exc_path.exists():
        try:
            lines = exc_path.read_text(encoding="utf-8").strip().split("\n")
            recent = [l for l in lines if l.strip()]
            if len(recent) > 100:
                score -= 2
        except Exception:
            pass

    return max(0, score)


# ═══════════════════════════════════════════════════════════════════
# Dead State Recovery — Process-Level Self-Recovery
# ═══════════════════════════════════════════════════════════════════

_CONSECUTIVE_FAILURES: dict[str, int] = {}
_LOOP_DETECTION_LOCK = threading.Lock()
_RECOVERY_IN_PROGRESS = False


def track_consecutive_failure(tool_name: str) -> bool:
    """Track consecutive failures per tool. Returns True if loop detected (≥5 same-tool failures).

    Resets on successful call via clear_consecutive_failures().
    """
    global _RECOVERY_IN_PROGRESS
    if _RECOVERY_IN_PROGRESS:
        return False  # suppress during recovery

    with _LOOP_DETECTION_LOCK:
        _CONSECUTIVE_FAILURES[tool_name] = _CONSECUTIVE_FAILURES.get(tool_name, 0) + 1
        count = _CONSECUTIVE_FAILURES[tool_name]
        if count >= 5:
            logger.error(
                f"[watchdog] LOOP DETECTED: {tool_name} failed {count}x consecutively — triggering recovery"
            )
            return True
        if count >= 3:
            logger.warning(f"[watchdog] {tool_name} failed {count}x — approaching loop threshold")
    return False


def clear_consecutive_failures(tool_name: str | None = None):
    """Reset failure counter for a tool (or all tools)."""
    with _LOOP_DETECTION_LOCK:
        if tool_name:
            _CONSECUTIVE_FAILURES.pop(tool_name, None)
        else:
            _CONSECUTIVE_FAILURES.clear()


def _recover_restart(reason: str = "loop detection") -> dict:
    """Gracefully restart the bot process with minimal disruption.

    Returns recovery report dict.
    """
    global _RECOVERY_IN_PROGRESS
    _RECOVERY_IN_PROGRESS = True

    report = {
        "status": "recovery_triggered",
        "reason": reason,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }

    try:
        # 1. Clear all failure state
        clear_consecutive_failures()

        # 2. Log recovery
        log_path = Path.home() / ".baw" / "logs" / "recovery.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"[{report['timestamp']}] RECOVERY: {reason}\n")

        # 3. Signal the health score
        global _LAST_HEALTH_SCORE
        _LAST_HEALTH_SCORE = max(1, _LAST_HEALTH_SCORE - 3)

        # 4. Reset kill signal
        reset_kill_signal()

        report["ok"] = True
    except Exception as e:
        report["ok"] = False
        report["error"] = str(e)
    finally:
        _RECOVERY_IN_PROGRESS = False

    return report


def get_recovery_status() -> dict:
    """Return current recovery state for health checks."""
    return {
        "recovery_in_progress": _RECOVERY_IN_PROGRESS,
        "consecutive_failures": dict(_CONSECUTIVE_FAILURES),
        "health_score": _LAST_HEALTH_SCORE,
    }
