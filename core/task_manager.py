"""BAW — Async Task Manager

Proper background task management with queue, concurrency limits,
cancellation, status tracking, and integration with the scheduler.
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
        if self.process and self.process.poll() is None:
            self.process.terminate()
            # Give it 3s, then kill
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
    """Manages background task lifecycle — queue, execute, cancel, track.

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
                if task.status == "running":
                    self._running.add(d.name)

    def submit(self, prompt: str, baw_path: str | None = None,
               mode: str = "hybrid", verbose: bool = False) -> AsyncTask:
        """Submit a new background task. Returns immediately."""
        task_id = f"task-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        task_dir = self.data_dir / TASKS_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        task = AsyncTask(task_id, prompt, task_dir)
        (task_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        task.status = "queued"

        with self._lock:
            self._tasks[task_id] = task
            self._queue.append(task_id)

        # Start the execution thread (non-blocking)
        self._start_consumer()
        return task

    def _start_consumer(self):
        """Ensure the background consumer thread is running."""
        if self._cleaner_running:
            return
        self._cleaner_running = True
        self._cleaner_thread = threading.Thread(target=self._consumer_loop, daemon=True)
        self._cleaner_thread.start()

    def _consumer_loop(self):
        """Continuously process queued tasks, respecting MAX_CONCURRENT."""
        while True:
            with self._lock:
                # Pick next queued task if we have capacity
                available = MAX_CONCURRENT - len(self._running)
                to_start = []
                while available > 0 and self._queue:
                    tid = self._queue.pop(0)
                    if tid in self._tasks:
                        to_start.append(tid)
                        available -= 1

            # Start selected tasks (outside lock)
            for tid in to_start:
                self._execute(tid)

            # Check for stale running tasks
            self._check_stale()

            time.sleep(POLL_INTERVAL)

    def _execute(self, task_id: str):
        """Launch a task in a subprocess."""
        task = self._tasks.get(task_id)
        if not task:
            return

        task.status = "running"
        task._started = datetime.now()
        with self._lock:
            self._running.add(task_id)

        baw_path = sys.argv[0] if sys.argv and sys.argv[0] else "baw"
        task.process = sp.Popen(
            [baw_path, "--mode", "hybrid", "--task-id", task_id, task.prompt],
            stdout=(task.dir / "stdout.txt").open("w"),
            stderr=(task.dir / "stderr.txt").open("w"),
            cwd=str(self.data_dir.parent),
        )

    def _check_stale(self):
        """Check if any running tasks have completed/failed."""
        stale = []
        for tid in list(self._running):
            task = self._tasks.get(tid)
            if not task:
                stale.append(tid)
                continue
            if task.process and task.process.poll() is not None:
                rc = task.process.returncode
                if task.status == "running":
                    task.status = "done" if rc == 0 else f"failed (rc={rc})"
                task._finished = datetime.now()
                stale.append(tid)
            elif task.status != "running":
                # Status changed externally (e.g. cancelled via --task-cancel)
                if task.process and task.process.poll() is None:
                    task.process.terminate()
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
