"""
BAW built-in: cronjob — autonomous task scheduling tool.

Manages recurring tasks via CRUD operations.
Uses simple interval syntax (no croniter dependency).
Jobs stored in ~/.baw/cron/jobs.json.

Schedule format:
  "30m"        — every 30 minutes
  "2h"         — every 2 hours
  "hourly"     — every hour at :00
  "daily@HH:MM" — daily at specific time (e.g. daily@09:00)
  "weekly@DAY@HH:MM" — weekly (e.g. weekly@sun@09:00)

M3 review consensus:
- skip_if_running=True default (no overlap)
- max 20 jobs total
- Execution log at ~/.baw/cron/logs/<name>.jsonl
"""

import json
import time
import os
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta


CRON_DIR = None
_CRON_TZ_CACHE: str | None = None


def _get_cron_timezone() -> str:
    """Read the configured cron timezone from BAW config (default UTC)."""
    global _CRON_TZ_CACHE
    if _CRON_TZ_CACHE is not None:
        return _CRON_TZ_CACHE
    try:
        cfg_path = Path.home() / ".baw" / "config.yaml"
        if cfg_path.exists():
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text())
            tz_str = cfg.get("cron", {}).get("timezone", "UTC") or "UTC"
            _CRON_TZ_CACHE = tz_str
            return tz_str
    except Exception:
        pass
    _CRON_TZ_CACHE = "UTC"
    return "UTC"
_MAX_JOBS = 20
_LOCK = threading.Lock()


def _get_cron_dir(data_dir: str | None = None) -> Path:
    global CRON_DIR
    if CRON_DIR:
        return CRON_DIR
    base = Path(data_dir).expanduser() if data_dir else Path.home() / ".baw"
    cron_dir = base / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "logs").mkdir(parents=True, exist_ok=True)
    CRON_DIR = cron_dir
    return cron_dir


def _load_jobs(cron_dir: Path) -> list[dict]:
    path = cron_dir / "jobs.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return data.get("jobs", [])
    except (json.JSONDecodeError, IOError, AttributeError):
        return []


def _save_jobs(cron_dir: Path, jobs: list[dict]) -> str:
    path = cron_dir / "jobs.json"
    path.write_text(
        json.dumps({"jobs": jobs, "updated": time.time()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return f"[OK] {len(jobs)} job(s) saved."


def _parse_schedule(schedule: str) -> str | None:
    """Validate schedule string and return None (valid) or error message."""
    if schedule in ("hourly",):
        return None
    if schedule.endswith("m") and schedule[:-1].isdigit():
        return None
    if schedule.endswith("h") and schedule[:-1].isdigit():
        return None
    if schedule.startswith("daily@"):
        parts = schedule.split("@")
        if len(parts) == 2 and ":" in parts[1]:
            return None
    if schedule.startswith("weekly@"):
        parts = schedule.split("@")
        valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        if len(parts) == 3 and parts[1] in valid_days and ":" in parts[2]:
            return None
    return f"Invalid schedule format: '{schedule}'. Use '30m', '2h', 'hourly', 'daily@09:00', 'weekly@sun@09:00'."


def _apply_tz(schedule: str, h: int, m: int, now_utc: datetime,
              for_weekly: tuple[int, int] | None = None) -> float:
    """Calculate next run in user's timezone, return UTC timestamp.

    for_weekly: (target_dow, days_ahead_base) for weekly schedule.
    """
    tz_name = _get_cron_timezone()
    if tz_name and tz_name.upper() != "UTC":
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz_name)
            now_tz = datetime.now(tz)
            if for_weekly is None:
                # daily@
                next_tz = now_tz.replace(hour=h, minute=m, second=0, microsecond=0)
                if next_tz <= now_tz:
                    next_tz += timedelta(days=1)
            else:
                # weekly@
                target_dow, _ = for_weekly
                days_ahead = target_dow - now_tz.weekday()
                if days_ahead <= 0 or (days_ahead == 0 and (now_tz.hour > h or (now_tz.hour == h and now_tz.minute >= m))):
                    days_ahead += 7
                next_tz = (now_tz + timedelta(days=days_ahead)).replace(
                    hour=h, minute=m, second=0, microsecond=0
                )
            return next_tz.astimezone(timezone.utc).timestamp()
        except Exception:
            pass
    # Fallback: interpret as UTC
    if for_weekly is None:
        next_t = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
        if next_t <= now_utc:
            next_t += timedelta(days=1)
        return next_t.timestamp()
    else:
        target_dow, _ = for_weekly
        days_ahead = target_dow - now_utc.weekday()
        if days_ahead <= 0 or (days_ahead == 0 and (now_utc.hour > h or (now_utc.hour == h and now_utc.minute >= m))):
            days_ahead += 7
        next_t = (now_utc + timedelta(days=days_ahead)).replace(
            hour=h, minute=m, second=0, microsecond=0
        )
        return next_t.timestamp()


def _next_run(schedule: str) -> float:
    """Calculate next run timestamp from schedule string."""
    now = datetime.now(timezone.utc)
    if schedule == "hourly":
        next_t = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return next_t.timestamp()
    if schedule.endswith("m") and schedule[:-1].isdigit():
        mins = int(schedule[:-1])
        return now.timestamp() + mins * 60
    if schedule.endswith("h") and schedule[:-1].isdigit():
        hours = int(schedule[:-1])
        return now.timestamp() + hours * 3600
    if schedule.startswith("daily@"):
        time_str = schedule.split("@")[1]
        h, m = map(int, time_str.split(":"))
        return _apply_tz(schedule, h, m, now)
    if schedule.startswith("weekly@"):
        parts = schedule.split("@")
        day_names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        target_dow = day_names.get(parts[1], 0)
        h, m = map(int, parts[2].split(":"))
        return _apply_tz(schedule, h, m, now, for_weekly=(target_dow, 0))
    return now.timestamp() + 3600  # Fallback: 1 hour


def cronjob(
    action: str,
    schedule: str = "",
    prompt: str = "",
    name: str = "",
    data_dir: str | None = None,
) -> str:
    """Manage recurring cron jobs.

    Args:
        action: create, list, remove, pause, resume, run.
        schedule: Interval syntax ("30m", "2h", "hourly", "daily@09:00", "weekly@sun@09:00").
        prompt: Task prompt to execute when job fires.
        name: Job name identifier (auto-generated if empty on create).
        data_dir: BAW data directory (default: ~/.baw).

    Returns:
        Status message.
    """
    cron_dir = _get_cron_dir(data_dir)
    action = action.lower()

    # ── list ──
    if action == "list":
        jobs = _load_jobs(cron_dir)
        if not jobs:
            return "[cronjob] No jobs scheduled."
        lines = [f"<b>Cron Jobs</b> ({len(jobs)}/{_MAX_JOBS})"]
        for j in jobs:
            status = "▶" if j.get("enabled", True) else "⏸"
            lines.append(
                f"  {status} <b>{j.get('name', '?')}</b> — `{j.get('schedule', '?')}`"
                f"\n      → {j.get('prompt', '')[:80]}"
            )
        return "\n".join(lines)

    # ── remove ──
    if action == "remove":
        if not name:
            return "[cronjob] Error: 'name' required for remove."
        jobs = _load_jobs(cron_dir)
        before = len(jobs)
        jobs = [j for j in jobs if j.get("name") != name]
        if len(jobs) == before:
            return f"[cronjob] Job '{name}' not found."
        _save_jobs(cron_dir, jobs)
        return f"[cronjob] Removed: {name}"

    # ── pause ──
    if action == "pause":
        if not name:
            return "[cronjob] Error: 'name' required for pause."
        jobs = _load_jobs(cron_dir)
        found = False
        for j in jobs:
            if j.get("name") == name:
                j["enabled"] = False
                found = True
                break
        if not found:
            return f"[cronjob] Job '{name}' not found."
        _save_jobs(cron_dir, jobs)
        return f"[cronjob] Paused: {name}"

    # ── resume ──
    if action == "resume":
        if not name:
            return "[cronjob] Error: 'name' required for resume."
        jobs = _load_jobs(cron_dir)
        found = False
        for j in jobs:
            if j.get("name") == name:
                j["enabled"] = True
                found = True
                break
        if not found:
            return f"[cronjob] Job '{name}' not found."
        _save_jobs(cron_dir, jobs)
        return f"[cronjob] Resumed: {name}"

    # ── create ──
    if action == "create":
        if not schedule:
            return "[cronjob] Error: 'schedule' required for create."
        if not prompt:
            return "[cronjob] Error: 'prompt' required for create."

        err = _parse_schedule(schedule)
        if err:
            return f"[cronjob] {err}"

        jobs = _load_jobs(cron_dir)
        if len(jobs) >= _MAX_JOBS:
            return f"[cronjob] Error: max {_MAX_JOBS} jobs reached."

        if not name:
            name = f"job-{len(jobs) + 1}"

        # Check for duplicate name
        if any(j.get("name") == name for j in jobs):
            return f"[cronjob] Error: job '{name}' already exists."

        job = {
            "name": name,
            "schedule": schedule,
            "prompt": prompt,
            "enabled": True,
            "created": time.time(),
            "next_run": _next_run(schedule),
            "last_run": None,
            "last_result": None,
        }
        jobs.append(job)
        _save_jobs(cron_dir, jobs)
        return (
            f"[cronjob] Created: <b>{name}</b>\n"
            f"  Schedule: `{schedule}`\n"
            f"  Prompt: {prompt[:120]}"
        )

    # ── run (once immediately) ──
    if action == "run":
        if not name:
            return "[cronjob] Error: 'name' required for run."
        jobs = _load_jobs(cron_dir)
        job = next((j for j in jobs if j.get("name") == name), None)
        if not job:
            return f"[cronjob] Job '{name}' not found."
        if not job.get("enabled", True):
            return f"[cronjob] Job '{name}' is paused. Resume first."

        # Log run
        log_dir = cron_dir / "logs"
        log_file = log_dir / f"{name}.jsonl"
        entry = {
            "action": "run",
            "trigger": "manual",
            "time": time.time(),
            "result": f"Queued: {job.get('prompt', '')[:100]}",
        }
        try:
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except IOError:
            pass

        job["last_run"] = time.time()
        job["next_run"] = _next_run(job.get("schedule", ""))
        _save_jobs(cron_dir, jobs)
        return f"[cronjob] Run queued: <b>{name}</b> — {job.get('prompt', '')[:120]}"

    return f"[cronjob] Unknown action: '{action}'. Use: create, list, remove, pause, resume, run."


TOOL_DEF = {
    "name": "cronjob",
    "description": (
        "Manage recurring scheduled tasks. Use this to set up periodic operations "
        "like daily status checks, hourly monitoring, or scheduled commands. "
        "Schedule format: '30m' (every 30 min), '2h' (every 2 hours), "
        "'hourly' (every hour at :00), 'daily@09:00' (daily at 9am), "
        "'weekly@sun@09:00' (Sundays at 9am). "
        "Max 20 jobs. Auto-skips overlapping runs."
    ),
    "handler": cronjob,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "remove", "pause", "resume", "run"],
                "description": "Operation: create (add job), list (show all), "
                               "remove (delete by name), pause (disable), "
                               "resume (re-enable), run (execute once now).",
            },
            "schedule": {
                "type": "string",
                "description": "Schedule string (required for create). "
                               "Format: '30m', '2h', 'hourly', 'daily@09:00', 'weekly@sun@09:00'.",
                "default": "",
            },
            "prompt": {
                "type": "string",
                "description": "Task prompt (required for create). Executed when job fires.",
                "default": "",
            },
            "name": {
                "type": "string",
                "description": "Job name (auto-generated if empty on create; required for remove/pause/resume/run).",
                "default": "",
            },
        },
        "required": ["action"],
    },
    "risk_level": "medium",
}
