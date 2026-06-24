#!/usr/bin/env python3
"""Initialize BAW scheduler tasks."""
import os, sys
sys.path.insert(0, os.path.expanduser("~/BAW"))
os.chdir(os.path.expanduser("~/BAW"))

from core.scheduler import Scheduler, ScheduledTask

data_dir = os.path.expanduser("~/.baw")
sched = Scheduler(data_dir)

tasks = [
    ("daily-self-report", "0 23 * * *", "Run self_diagnose and report"),
    ("daily-auto-heal", "0 3 * * *", "Auto-heal: self_diagnose with fix=True"),
    ("weekly-memory-quality", "0 4 * * 0", "Memory quality check"),
    ("weekly-session-synthesis", "0 5 * * 0", "Session synthesis"),
    ("weekly-self-evolution", "0 6 * * 0", "Self-evolution audit"),
]

for name, cron, prompt in tasks:
    try:
        task = ScheduledTask(name=name, cron=cron, command=prompt, enabled=True)
        sched.add_task(task)
        print(f"  OK: {name}")
    except Exception as e:
        print(f"  FAIL: {name}: {e}")

print("Done")
