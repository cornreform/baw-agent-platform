"""BAW — Internal Scheduler

Cron-based task scheduler for BAW background tasks.
Tasks defined in ~/.baw/schedule.yaml.
Polls every 60s via a lightweight daemon thread.
"""

from __future__ import annotations
import os
import re
import time
import json
import uuid
import yaml
import shlex
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from croniter import croniter

from .display import front_desk_task_id, front_desk_status


# ── Constants ──

SCHEDULE_FILE = "schedule.yaml"
STATE_FILE = "schedule_state.json"
TASKS_DIR = "tasks"
POLL_INTERVAL = 60  # seconds


# ── Task definition ──

class ScheduledTask:
    """A single scheduled task from the YAML config."""

    def __init__(self, name: str, cron: str, command: str = "",
                 skill: str = "", skill_args: str = "",
                 enabled: bool = True, description: str = ""):
        self.name = name
        self.cron = cron
        self.command = command
        self.skill = skill
        self.skill_args = skill_args
        self.enabled = enabled
        self.description = description
        self._cron_iter = None

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledTask":
        return cls(
            name=d.get("name", "untitled"),
            cron=d.get("cron", "0 0 * * *"),
            command=d.get("command", ""),
            skill=d.get("skill", ""),
            skill_args=d.get("skill_args", ""),
            enabled=d.get("enabled", True),
            description=d.get("description", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "cron": self.cron,
            "command": self.command,
            "skill": self.skill,
            "skill_args": self.skill_args,
            "enabled": self.enabled,
            "description": self.description,
        }

    def next_run(self, base: datetime | None = None) -> datetime | None:
        """Calculate next run time from a base time (default: now)."""
        base = base or datetime.now(timezone.utc)
        try:
            if self._cron_iter is None:
                self._cron_iter = croniter(self.cron, base)
            else:
                self._cron_iter.set_current(base)
            return self._cron_iter.get_next(datetime)
        except (ValueError, KeyError):
            return None

    def should_run(self, base: datetime, last_run: datetime | None) -> bool:
        """Check if this task should fire now."""
        if not self.enabled:
            return False
        if last_run is None:
            # Never run — only fire if next run is within the last POLL_INTERVAL
            nxt = self.next_run(base)
            return nxt is not None and (base - nxt).total_seconds() < POLL_INTERVAL * 2
        nxt = self.next_run(last_run)
        return nxt is not None and nxt <= base


# ── Scheduler engine ──

class Scheduler:
    """Loads, tracks, and fires scheduled tasks."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self._tasks: list[ScheduledTask] = []
        self._state: dict[str, str] = {}  # task_name → last_run ISO timestamp
        self._running = False
        self._thread: threading.Thread | None = None
        self._cron_tz_name: str | None = None
        self._load()

    # ── File I/O ──

    def _schedule_path(self) -> Path:
        return self.data_dir / SCHEDULE_FILE

    def _state_path(self) -> Path:
        return self.data_dir / STATE_FILE

    def _load(self):
        """Load tasks from YAML and state from JSON."""
        sp = self._schedule_path()
        if sp.exists():
            try:
                raw = yaml.safe_load(sp.read_text(encoding="utf-8")) or []
                self._tasks = [ScheduledTask.from_dict(t) for t in raw]
            except Exception:
                self._tasks = []
        else:
            self._tasks = []

        st = self._state_path()
        if st.exists():
            try:
                self._state = json.loads(st.read_text(encoding="utf-8"))
            except Exception:
                self._state = {}

    def _save_state(self):
        self._state_path().write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_schedule(self):
        """Write tasks back to YAML."""
        sp = self._schedule_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(
            yaml.dump([t.to_dict() for t in self._tasks],
                       default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    # ── Task management ──

    def list_tasks(self) -> list[ScheduledTask]:
        return self._tasks

    def add_task(self, task: ScheduledTask):
        self._tasks.append(task)
        self.save_schedule()
        self._state[task.name] = ""
        self._save_state()

    def remove_task(self, name: str) -> bool:
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.name != name]
        if len(self._tasks) < before:
            self._state.pop(name, None)
            self.save_schedule()
            self._save_state()
            return True
        return False

    def toggle_task(self, name: str, enabled: bool | None = None) -> bool:
        for t in self._tasks:
            if t.name == name:
                t.enabled = enabled if enabled is not None else not t.enabled
                self.save_schedule()
                return True
        return False

    # ── Firing ──

    def _check_cron_jobs(self, now: datetime) -> list[dict]:
        """Also check user-created cron jobs from ~/.baw/cron/jobs.json."""
        fired = []
        cron_path = self.data_dir / "cron" / "jobs.json"
        if not cron_path.exists():
            return fired
        try:
            data = json.loads(cron_path.read_text(encoding="utf-8"))
            jobs = data.get("jobs", [])
            if not isinstance(jobs, list):
                return fired
            modified = False
            for job in jobs:
                if not job.get("enabled", True):
                    continue
                next_ts = job.get("next_run", 0)
                if isinstance(next_ts, (int, float)) and next_ts <= now.timestamp():
                    # Fire the job
                    task_id = f"cron-{int(now.timestamp())}-{job.get('name', 'job')}"
                    task_dir = self.data_dir / TASKS_DIR / task_id
                    task_dir.mkdir(parents=True, exist_ok=True)
                    prompt = job.get("prompt", job.get("name", ""))
                    (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
                    (task_dir / "status.txt").write_text("queued", encoding="utf-8")
                    # Execute via BAW agent
                    import subprocess as sp
                    sp.Popen(
                        ["baw", "--mode", "hybrid", "--task-id", task_id, prompt],
                        stdout=(task_dir / "stdout.txt").open("w"),
                        stderr=(task_dir / "stderr.txt").open("w"),
                        cwd=str(self.data_dir.parent),
                    )
                    # Update job timing
                    sched = job.get("schedule", "")
                    job["last_run"] = now.timestamp()
                    job["next_run"] = self._cron_next(sched, now.timestamp())
                    modified = True
                    # Log
                    log_dir = self.data_dir / "cron" / "logs"
                    log_dir.mkdir(parents=True, exist_ok=True)
                    log_entry = {
                        "action": "run", "trigger": "scheduler",
                        "time": now.timestamp(),
                        "result": f"Fired: {job.get('name', '?')} — {prompt[:100]}",
                    }
                    try:
                        with open(log_dir / f"{job.get('name', 'unknown')}.jsonl", "a") as f:
                            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                    except IOError:
                        pass
                    fired.append({
                        "task": job.get("name", "?"),
                        "task_id": task_id,
                        "cron": sched,
                        "time": now.isoformat(),
                    })
            if modified:
                cron_path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        except Exception:
            pass
        # ── Firing ──

    def _cron_tz(self) -> str:
        """Get configured cron timezone from config.yaml."""
        if self._cron_tz_name is not None:
            return self._cron_tz_name
        try:
            cfg_path = self.data_dir / "config.yaml"
            if cfg_path.exists():
                import yaml as _yaml
                cfg = _yaml.safe_load(cfg_path.read_text())
                tz_str = cfg.get("cron", {}).get("timezone", "UTC") or "UTC"
                self._cron_tz_name = tz_str
                return tz_str
        except Exception:
            pass
        self._cron_tz_name = "UTC"
        return "UTC"

    def _cron_next(self, sched: str, after: float) -> float:
        """Calculate next run timestamp from cronjob schedule format."""
        from datetime import timedelta
        now_dt = datetime.fromtimestamp(after, tz=timezone.utc)
        if sched == "hourly":
            next_t = now_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return next_t.timestamp()
        if sched.endswith("m") and sched[:-1].isdigit():
            return after + int(sched[:-1]) * 60
        if sched.endswith("h") and sched[:-1].isdigit():
            return after + int(sched[:-1]) * 3600
        if sched.startswith("daily@"):
            parts = sched.split("@")
            if len(parts) == 2 and ":" in parts[1]:
                h, m = map(int, parts[1].split(":"))
                return self._cron_next_tz(now_dt, h, m)
        if sched.startswith("weekly@"):
            parts = sched.split("@")
            if len(parts) == 3:
                day_names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
                target_dow = day_names.get(parts[1], 0)
                h, m = map(int, parts[2].split(":"))
                return self._cron_next_tz(now_dt, h, m, for_weekly=target_dow)
        return after + 3600  # fallback

    def _cron_next_tz(self, now_dt: datetime, h: int, m: int,
                      for_weekly: int | None = None) -> float:
        """Calculate next run respecting config timezone, return UTC timestamp."""
        tz_name = self._cron_tz()
        if tz_name and tz_name.upper() != "UTC":
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
                now_tz = datetime.now(tz)
                if for_weekly is None:
                    next_tz = now_tz.replace(hour=h, minute=m, second=0, microsecond=0)
                    if next_tz <= now_tz:
                        next_tz += timedelta(days=1)
                else:
                    target_dow = for_weekly
                    days_ahead = target_dow - now_tz.weekday()
                    if days_ahead <= 0 or (days_ahead == 0 and (now_tz.hour > h or (now_tz.hour == h and now_tz.minute >= m))):
                        days_ahead += 7
                    next_tz = (now_tz + timedelta(days=days_ahead)).replace(
                        hour=h, minute=m, second=0, microsecond=0
                    )
                return next_tz.astimezone(timezone.utc).timestamp()
            except Exception:
                pass
        # Fallback: UTC
        if for_weekly is None:
            next_t = now_dt.replace(hour=h, minute=m, second=0, microsecond=0)
            if next_t <= now_dt:
                next_t += timedelta(days=1)
        else:
            target_dow = for_weekly
            days_ahead = target_dow - now_dt.weekday()
            if days_ahead <= 0 or (days_ahead == 0 and (now_dt.hour > h or (now_dt.hour == h and now_dt.minute >= m))):
                days_ahead += 7
            next_t = (now_dt + timedelta(days=days_ahead)).replace(
                hour=h, minute=m, second=0, microsecond=0
            )
        return next_t.timestamp()

    def poll(self) -> list[dict]:
        """Check all tasks, fire any that are due. Returns list of fired tasks."""
        now = datetime.now(timezone.utc)
        fired = []

        # Check internal schedule.yaml tasks
        for task in self._tasks:
            last_run_str = self._state.get(task.name, "")
            last_run = None
            if last_run_str:
                try:
                    last_run = datetime.fromisoformat(last_run_str)
                except Exception:
                    last_run = None

            if task.should_run(now, last_run):
                # Fire the task
                task_id = self._execute(task, now)
                self._state[task.name] = now.isoformat()
                self._save_state()
                fired.append({
                    "task": task.name,
                    "task_id": task_id,
                    "cron": task.cron,
                    "time": now.isoformat(),
                })

        # Also check user-created cron jobs from cronjob tool
        try:
            fired.extend(self._check_cron_jobs(now))
        except Exception:
            pass

        return fired

    def _execute(self, task: ScheduledTask, now: datetime) -> str:
        """Delegate a scheduled task to the background executor.

        Supports two modes:
          - Normal: runs through BAW agent loop (default)
          - Shell:  if command starts with '!', runs as raw shell command
        """
        task_id = f"sched-{int(now.timestamp())}-{uuid.uuid4().hex[:6]}"
        task_dir = self.data_dir / TASKS_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Use the command if available, otherwise use skill
        prompt = task.command
        if not prompt and task.skill:
            prompt = f"[skill:{task.skill}] {task.skill_args}"

        # Save prompt
        (task_dir / "prompt.txt").write_text(prompt or task.name, encoding="utf-8")
        (task_dir / "status.txt").write_text("running", encoding="utf-8")

        # Shell command mode: if command starts with '!', run directly
        raw_cmd = prompt or task.name
        if raw_cmd.startswith("!"):
            shell_cmd = raw_cmd[1:].strip()
            # CRITICAL: whitelist allowed commands only
            _ALLOWED_SHELL = {
                "python3", "python", "bash", "cd", "ls", "cat", "echo",
                "docker", "docker-compose", "git", "make", "curl", "wget",
                "mkdir", "cp", "mv", "rm", "chmod", "chown",
            }
            cmd_base = shlex.split(shell_cmd)[0] if shell_cmd else ""
            cmd_clean = re.sub(r"^[/.]+", "", cmd_base)  # strip leading ../../
            cmd_base_name = cmd_clean.split("/")[-1] if "/" in cmd_clean else cmd_clean
            if cmd_base_name not in _ALLOWED_SHELL:
                print(f"[BAW-SCHED] Blocked shell command: {cmd_base_name} (from: {shell_cmd[:80]})")
                (task_dir / "status.txt").write_text("blocked", encoding="utf-8")
                return task_id
            import subprocess as sp
            # Run in background thread, capture result
            def _run_shell():
                try:
                    _result = sp.run(
                        shell_cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        cwd=str(self.data_dir.parent),
                    )
                    (task_dir / "stdout.txt").write_text(_result.stdout or "", encoding="utf-8")
                    (task_dir / "stderr.txt").write_text(_result.stderr or "", encoding="utf-8")
                    if _result.returncode == 0:
                        (task_dir / "status.txt").write_text("completed", encoding="utf-8")
                        print(f"[BAW-SCHED] ✅ {task.name} completed")
                    else:
                        (task_dir / "status.txt").write_text(f"failed({_result.returncode})", encoding="utf-8")
                        err_preview = (_result.stderr or "")[:200]
                        print(f"[BAW-SCHED] ❌ {task.name} failed (rc={_result.returncode}): {err_preview}")
                except sp.TimeoutExpired:
                    (task_dir / "status.txt").write_text("timeout", encoding="utf-8")
                    print(f"[BAW-SCHED] ⏰ {task.name} timed out after 300s")
                except Exception as _e:
                    (task_dir / "stderr.txt").write_text(str(_e), encoding="utf-8")
                    (task_dir / "status.txt").write_text(f"error: {str(_e)[:100]}", encoding="utf-8")
                    print(f"[BAW-SCHED] ❌ {task.name} error: {_e}")
            _th = threading.Thread(target=_run_shell, daemon=True)
            _th.start()
            return task_id

        # BAW agent mode
        import subprocess as sp
        sp.Popen(
            ["baw", "--mode", "hybrid", "--task-id", task_id, prompt or task.name],
            stdout=(task_dir / "stdout.txt").open("w"),
            stderr=(task_dir / "stderr.txt").open("w"),
            cwd=str(self.data_dir.parent),
        )
        return task_id

    # ── Daemon ──

    def start(self):
        """Start the polling daemon in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                fired = self.poll()
                if fired:
                    for f in fired:
                        print(front_desk_status(f["task"], "started", f["task_id"]))
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    # ── Status ──

    def status_report(self) -> str:
        """Generate a text summary of all scheduled tasks."""
        lines = ["📋 Scheduled Tasks:"]
        for t in self._tasks:
            last_run = self._state.get(t.name, "never")
            status = "✅" if t.enabled else "⏸️"
            nxt = t.next_run()
            nxt_str = nxt.strftime("%H:%M %Y-%m-%d") if nxt else "?"
            lines.append(
                f"  {status} {t.name} — `{t.cron}` — next: {nxt_str}"
                f"  {'— ' + t.description[:40] if t.description else ''}"
            )
        if not self._tasks:
            lines.append("  (no tasks scheduled)")
        return "\n".join(lines)
