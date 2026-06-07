"""BAW — Internal Scheduler

Cron-based task scheduler for BAW background tasks.
Tasks defined in ~/.baw/schedule.yaml.
Polls every 60s via a lightweight daemon thread.
"""

from __future__ import annotations
import os
import time
import json
import uuid
import yaml
import threading
from pathlib import Path
from datetime import datetime, timezone
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

    def poll(self) -> list[dict]:
        """Check all tasks, fire any that are due. Returns list of fired tasks."""
        now = datetime.now(timezone.utc)
        fired = []

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

        return fired

    def _execute(self, task: ScheduledTask, now: datetime) -> str:
        """Delegate a scheduled task to the background executor."""
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

        # Spawn background
        import subprocess as sp
        baw_path = __import__("sys").argv[0] if __import__("sys").argv else "baw"
        sp.Popen(
            [baw_path, "--mode", "hybrid", "--task-id", task_id, prompt or task.name],
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
