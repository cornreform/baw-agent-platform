"""Scheduler tests — cron, task execution, state persistence."""
from __future__ import annotations

import pytest
import json
import time
from pathlib import Path
from datetime import datetime, timezone

pytestmark = [pytest.mark.unit, pytest.mark.regression]


class TestScheduledTask:
    """P0: Task parsing must be correct."""

    def test_task_from_dict(self):
        from core.scheduler import ScheduledTask
        d = {
            "name": "test-task",
            "cron": "0 * * * *",
            "command": "echo hello",
            "enabled": True,
        }
        task = ScheduledTask.from_dict(d)
        assert task.name == "test-task"
        assert task.cron == "0 * * * *"
        assert task.command == "echo hello"
        assert task.enabled is True

    def test_task_next_run(self):
        from core.scheduler import ScheduledTask
        task = ScheduledTask("test", "0 * * * *")
        nxt = task.next_run()
        assert nxt is not None
        assert nxt > datetime.now(timezone.utc)

    def test_disabled_task_never_runs(self):
        from core.scheduler import ScheduledTask
        task = ScheduledTask("test", "* * * * *", enabled=False)
        now = datetime.now(timezone.utc)
        assert task.should_run(now, None) is False


class TestSchedulerState:
    """P0: State must persist across restarts."""

    def test_state_save_load(self, temp_baw_home: Path):
        from core.scheduler import Scheduler
        sched = Scheduler(temp_baw_home)
        sched._state["test-task"] = datetime.now(timezone.utc).isoformat()
        sched._save_state()
        assert sched._state_path().exists()

        # New instance reads same state
        sched2 = Scheduler(temp_baw_home)
        assert "test-task" in sched2._state

    def test_add_remove_task(self, temp_baw_home: Path):
        from core.scheduler import Scheduler, ScheduledTask
        sched = Scheduler(temp_baw_home)
        task = ScheduledTask("new-task", "0 0 * * *", command="echo test")
        sched.add_task(task)
        assert any(t.name == "new-task" for t in sched.list_tasks())
        assert sched.remove_task("new-task") is True
        assert not any(t.name == "new-task" for t in sched.list_tasks())

    def test_toggle_task(self, temp_baw_home: Path):
        from core.scheduler import Scheduler, ScheduledTask
        sched = Scheduler(temp_baw_home)
        task = ScheduledTask("toggle-task", "0 0 * * *")
        sched.add_task(task)
        sched.toggle_task("toggle-task", enabled=False)
        tasks = sched.list_tasks()
        assert not tasks[0].enabled


class TestSchedulerPoll:
    """P1: Polling must fire due tasks."""

    def test_poll_no_tasks(self, temp_baw_home: Path):
        from core.scheduler import Scheduler
        sched = Scheduler(temp_baw_home)
        fired = sched.poll()
        assert fired == []

    def test_poll_fires_past_task(self, temp_baw_home: Path):
        from core.scheduler import Scheduler, ScheduledTask
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch
        sched = Scheduler(temp_baw_home)
        # Task that should have run 1 minute ago
        task = ScheduledTask("past-task", "* * * * *")
        sched.add_task(task)
        sched._state["past-task"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with patch.object(sched, '_execute', return_value="mock-id"):
            fired = sched.poll()
        assert len(fired) >= 1
        assert fired[0]["task"] == "past-task"
