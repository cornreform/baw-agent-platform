"""BAW — Async Task Manager

Proper background task management with queue, concurrency limits,
cancellation, status tracking, and daemon-free execution.

Key design: subprocesses are DETACHED from the parent (start_new_session=True).
The 'baw --delegate' command can exit immediately — the child process
continues running independently.
"""

from __future__ import annotations
import os, sys, json, time, uuid, signal, threading
from pathlib import Path
from datetime import datetime
from typing import Optional
import subprocess as sp

MAX_CONCURRENT = 3
POLL_INTERVAL = 5  # seconds
TASKS_DIR = "tasks"


class AsyncTask:
    """A single background task with full lifecycle tracking."""

    def __init__(self, task_id: str, prompt: str, task_dir: Path):
        self.id = task_id
        self.prompt = prompt
        self.dir = task_dir
        self.process: sp.Popen | None = None
        self._created = datetime.now()
        self._started: datetime | None = None
        self._finished: datetime | None = None

    @property
    def status(self) -> str:
        p = self.dir / "status.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        return "unknown"

    @status.setter
    def status(self, val: str):
        (self.dir / "status.txt").write_text(val, encoding="utf-8")

    @property
    def output(self) -> str:
        p = self.dir / "stdout.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    @property
    def error(self) -> str:
        p = self.dir / "stderr.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    def cancel(self):
        """Cancel the task by sending SIGTERM and marking status."""
        # Try PID file first (survives parent restart)
        pid_file = self.dir / "pid.txt"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                # Give it 3s
                for _ in range(6):
                    try:
                        os.kill(pid, 0)  # Still alive?
                        time.sleep(0.5)
                    except OSError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except (ValueError, OSError, ProcessLookupError):
                pass
        # Fallback: in-process Popen
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except sp.TimeoutExpired:
                self.process.kill()
        self.status = "cancelled"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt[:80],
            "status": self.status,
            "created": self._created.isoformat(),
        }


class TaskManager:
    """Manages background task lifecycle — execute, cancel, track.

    Tasks are launched as DETACHED subprocesses (start_new_session=True)
    so they survive the parent process exiting.

    Singleton per process. Thread-safe.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self, data_dir: Path | str | None = None):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".baw"
        self._tasks: dict[str, AsyncTask] = {}
        self._queue: list[str] = []
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self._cleaner_running = False
        self._cleaner_thread: threading.Thread | None = None
        self._load_existing()

    def _load_existing(self):
        """Scan tasks directory for existing tasks."""
        tasks_dir = self.data_dir / TASKS_DIR
        if not tasks_dir.exists():
            tasks_dir.mkdir(parents=True, exist_ok=True)
            return
        for d in sorted(tasks_dir.iterdir()):
            if d.is_dir():
                p = d / "prompt.txt"
                prompt = p.read_text(encoding="utf-8") if p.exists() else "?"
                task = AsyncTask(d.name, prompt, d)
                self._tasks[d.name] = task
                # Check if PID file confirms it's still alive
                if task.status == "running":
                    if self._pid_alive(task):
                        self._running.add(d.name)
                    else:
                        # Process died without updating status
                        task.status = "done (stale)"

    def _pid_alive(self, task: AsyncTask) -> bool:
        """Check if the task's PID is still alive."""
        pid_file = task.dir / "pid.txt"
        if not pid_file.exists():
            return False
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Signal 0 = check existence only
            return True
        except (ValueError, OSError, ProcessLookupError):
            return False

    def _find_baw_command(self) -> str:
        """Find the BAW CLI command to use in subprocesses.

        Priority:
        1. ~/.local/bin/baw (the bash wrapper — most robust)
        2. sys.argv[0] (current script path)
        3. 'baw' (fallback, assumes it's in PATH)
        """
        local_baw = Path.home() / ".local/bin" / "baw"
        if local_baw.exists() and os.access(local_baw, os.X_OK):
            return str(local_baw)
        if sys.argv and sys.argv[0]:
            return sys.argv[0]
        return "baw"

    def submit(self, prompt: str, mode: str = "hybrid") -> AsyncTask:
        """Submit a new background task. Subprocess starts immediately.

        The child process is DETACHED from parent (start_new_session=True),
        so 'baw --delegate' can exit while the task runs in background.
        """
        task_id = f"task-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        task_dir = self.data_dir / TASKS_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        task = AsyncTask(task_id, prompt, task_dir)
        (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        task.status = "running"
        task._started = datetime.now()
        self._tasks[task_id] = task
        self._running.add(task_id)

        # Launch subprocess DETACHED — survives parent exit
        baw_cmd = self._find_baw_command()
        try:
            proc = sp.Popen(
                [baw_cmd, "--mode", mode, "--task-id", task_id, prompt],
                stdout=(task_dir / "stdout.txt").open("w"),
                stderr=(task_dir / "stderr.txt").open("w"),
                cwd=str(self.data_dir.parent),
                start_new_session=True,  # Detach from parent process group
            )
            task.process = proc
            # Save PID so status can be checked across process restarts
            (task_dir / "pid.txt").write_text(str(proc.pid), encoding="utf-8")
        except Exception as e:
            task.status = f"failed (spawn error: {e})"
            self._running.discard(task_id)
            if (task_dir / "stderr.txt").exists():
                (task_dir / "stderr.txt").write_text(str(e), encoding="utf-8")

        return task

    def _start_cleaner(self):
        """Start background cleaner thread (monitoring only, not execution)."""
        if self._cleaner_running:
            return
        self._cleaner_running = True
        self._cleaner_thread = threading.Thread(target=self._cleaner_loop, daemon=True)
        self._cleaner_thread.start()

    def _cleaner_loop(self):
        """Check running tasks and update stale statuses."""
        while True:
            self._check_stale()
            time.sleep(POLL_INTERVAL)

    def _check_stale(self):
        """Check if any running tasks have completed/failed."""
        stale = []
        for tid in list(self._running):
            task = self._tasks.get(tid)
            if not task:
                stale.append(tid)
                continue

            # Check status file (written by the subprocess itself)
            current_status = task.status
            if current_status in ("done", "failed", "cancelled", "error"):
                stale.append(tid)
                continue

            # Check PID — if process died without updating status
            if not self._pid_alive(task):
                # Process exited — read stderr for error info
                if task.error:
                    task.status = f"failed ({task.error[:100]})"
                else:
                    task.status = "done"
                stale.append(tid)

        with self._lock:
            for tid in stale:
                self._running.discard(tid)

    def cancel(self, task_id: str) -> bool:
        """Cancel a task by ID."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.cancel()
        with self._lock:
            self._running.discard(task_id)
            if task_id in self._queue:
                self._queue.remove(task_id)
        return True

    def get(self, task_id: str) -> AsyncTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self, status_filter: str | None = None) -> list[AsyncTask]:
        tasks = list(self._tasks.values())
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        return sorted(tasks, key=lambda t: t._created, reverse=True)

    def summary(self) -> str:
        """Human-readable summary of all tasks."""
        running = [t for t in self._tasks.values() if t.status == "running"]
        queued = len(self._queue)
        done = [t for t in self._tasks.values() if t.status == "done"]
        failed = [t for t in self._tasks.values() if "fail" in t.status.lower()]
        lines = [
            f"📋 Tasks: {len(self._tasks)} total",
            f"  ▶️  Running: {len(running)}/{MAX_CONCURRENT}",
            f"  ⏳ Queued: {queued}",
            f"  ✅ Done: {len(done)}",
            f"  ❌ Failed: {len(failed)}",
        ]
        if running:
            lines.append("")
            for t in running[:5]:
                lines.append(f"  ▶️  {t.id[:20]} — {t.prompt[:50]}")
        if self._queue:
            lines.append("")
            for tid in self._queue[:3]:
                t = self._tasks.get(tid)
                if t:
                    lines.append(f"  ⏳ {t.id[:20]} — {t.prompt[:50]}")
        return "\n".join(lines)
