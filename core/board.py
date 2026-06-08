"""BAW — Dashboard Generator

Generates a self-contained HTML dashboard showing:
- Scheduled tasks (next run, status)
- Recent activity (completed tasks + activity log)
- Available skills
- System status (git, disk, uptime, memory, model)
"""

from __future__ import annotations
import os
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from .scheduler import Scheduler
from .skills import SkillRegistry

DASHBOARD_FILE = "dashboard.html"
MAX_RECENT_TASKS = 20
ACTIVITY_LOG = "activity.jsonl"


def _read_task_status(task_dir: Path) -> dict:
    """Read a background task's status files."""
    status = "unknown"
    prompt = ""
    output = ""
    if (task_dir / "status.txt").exists():
        status = (task_dir / "status.txt").read_text(encoding="utf-8").strip()
    if (task_dir / "prompt.txt").exists():
        prompt = (task_dir / "prompt.txt").read_text(encoding="utf-8").strip()
    if (task_dir / "stdout.txt").exists():
        out = (task_dir / "stdout.txt").read_text(encoding="utf-8").strip()
        output = out[:500] if len(out) > 500 else out
    return {"status": status, "prompt": prompt, "output": output, "type": "task"}


def _read_activity_log(data_dir: Path) -> list[dict]:
    """Read recent activity log entries."""
    log_file = data_dir / ACTIVITY_LOG
    if not log_file.exists():
        return []
    entries = []
    try:
        for line in log_file.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                entry = json.loads(line)
                entry["type"] = "activity"
                entries.append(entry)
    except Exception:
        pass
    # Return newest first (file is append-only, so reverse)
    return list(reversed(entries))[:MAX_RECENT_TASKS]


def _collect_recent_activity(data_dir: Path) -> list[dict]:
    """Merge background tasks + activity log into one timeline, newest first."""
    tasks = _collect_recent_tasks(data_dir)
    activities = _read_activity_log(data_dir)

    # Merge by timestamp
    combined = []
    for t in tasks:
        combined.append({
            "id": t.get("id", "task"),
            "desc": t.get("prompt", "Background task"),
            "status": t.get("status", "unknown"),
            "ts": t.get("mtime", ""),
            "type": "task",
        })
    for a in activities:
        combined.append({
            "id": a.get("ts", ""),
            "desc": a.get("desc", ""),
            "status": "done",
            "ts": a.get("ts", ""),
            "type": "activity",
        })

    # Sort by timestamp descending (newest first)
    combined.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return combined[:MAX_RECENT_TASKS]


def _collect_recent_tasks(data_dir: Path) -> list[dict]:
    """Get recent background tasks, newest first."""
    tasks_dir = data_dir / "tasks"
    if not tasks_dir.exists():
        return []
    entries = []
    for d in sorted(tasks_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir():
            info = _read_task_status(d)
            info["id"] = d.name
            info["mtime"] = datetime.fromtimestamp(d.stat().st_mtime).isoformat()
            entries.append(info)
    return entries[:MAX_RECENT_TASKS]


def _system_info() -> dict:
    """Collect basic system info + BAW stats."""
    info = {"uptime": "?", "disk": "?", "git": "?", "memory": "?", "mem_count": "?", "mem_score": "?", "model": "?"}
    try:
        uptime_sec = time.monotonic()
        days = int(uptime_sec // 86400)
        hours = int((uptime_sec % 86400) // 3600)
        info["uptime"] = f"{days}d {hours}h"
    except Exception:
        pass
    try:
        import shutil
        usage = shutil.disk_usage(Path.home())
        info["disk"] = f"{usage.used // (2**30)}G / {usage.total // (2**30)}G ({usage.used * 100 // usage.total}%)"
    except Exception:
        pass
    try:
        result = os.popen("cd ~/baw && git log --oneline -1 2>/dev/null").read().strip()
        if result:
            info["git"] = result
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    info["memory"] = f"{kb // 1024} MB"
                    break
    except Exception:
        pass
    # BAW memory stats
    try:
        data_dir = os.environ.get("BAW_DATA_DIR", str(Path.home() / ".baw"))
        mem_file = Path(data_dir) / "memory" / "store.jsonl"
        if mem_file.exists():
            lines = mem_file.read_text(encoding="utf-8").strip().splitlines()
            info["mem_count"] = str(len(lines))
            # avg score
            scores = []
            for line in lines:
                try:
                    entry = json.loads(line)
                    scores.append(entry.get("score", 0))
                except Exception:
                    pass
            if scores:
                avg = sum(scores) / len(scores)
                info["mem_score"] = f"{avg:.2f}"
    except Exception:
        pass
    # Model info from config
    try:
        import yaml
        cfg_path = Path(data_dir) / "config.yaml"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            model = cfg.get("model", {})
            info["model"] = model.get("default", "?")
    except Exception:
        pass
    return info


def generate(data_dir: Path | str) -> str:
    """Generate the dashboard HTML and return the file path."""
    data_dir = Path(data_dir)
    scheduler = Scheduler(data_dir)
    skills_reg = SkillRegistry(data_dir)
    recent = _collect_recent_activity(data_dir)
    sysinfo = _system_info()
    sched_tasks = scheduler.list_tasks()
    all_skills = skills_reg.list_skills()

    # Build task rows
    sched_rows = ""
    for t in sched_tasks:
        nxt = t.next_run()
        nxt_str = nxt.strftime("%Y-%m-%d %H:%M") if nxt else "?"
        status_icon = "🟢" if t.enabled else "⏸️"
        sched_rows += f"""<tr>
            <td>{status_icon} {t.name}</td>
            <td><code>{t.cron}</code></td>
            <td>{nxt_str}</td>
            <td>{t.description[:60]}</td>
        </tr>"""

    if not sched_rows:
        sched_rows = """<tr><td colspan="4" class="muted">No scheduled tasks</td></tr>"""

    # Recent activity (merged from tasks + activity log)
    recent_rows = ""
    for r in recent:
        icon_map = {"running": "🔄", "done": "✅", "queued": "⏳", "failed": "❌", "unknown": "❓"}
        icon = icon_map.get(r.get("status", "unknown"), "❓")
        rtype_icon = "⚙️" if r.get("type") == "task" else "📝"
        desc_short = r.get("desc", r.get("id", "?"))[:80]
        ts_short = r.get("ts", "?")[:19] if r.get("ts") else "?"
        recent_rows += f"""<tr>
            <td>{rtype_icon}</td>
            <td>{desc_short}</td>
            <td><span class="badge {r.get('status', 'unknown')}">{r.get('status', 'unknown')}</span></td>
            <td>{ts_short}</td>
        </tr>"""

    if not recent_rows:
        recent_rows = """<tr><td colspan="4" class="muted">No recent activity</td></tr>"""

    # Skills
    skill_rows = ""
    for s in all_skills:
        skill_rows += f"""<tr>
            <td>{s.name}</td>
            <td>{s.description[:80]}</td>
            <td>{', '.join(s.tools) or '—'}</td>
            <td class="badge">{s.get_mode()}</td>
        </tr>"""

    if not skill_rows:
        skill_rows = """<tr><td colspan="4" class="muted">No skills installed</td></tr>"""

    # Build memory/model grid extra
    extra_cards = ""
    if sysinfo.get("mem_count") and sysinfo.get("mem_count") != "?":
        extra_cards += f"""<div class="card"><div class="label">🧠 Memories</div><div class="value">{sysinfo['mem_count']}</div></div>"""
    if sysinfo.get("mem_score") and sysinfo.get("mem_score") != "?":
        extra_cards += f"""<div class="card"><div class="label">📊 Mem Avg Score</div><div class="value">{sysinfo['mem_score']}</div></div>"""
    if sysinfo.get("model") and sysinfo.get("model") != "?":
        extra_cards += f"""<div class="card"><div class="label">🤖 Model</div><div class="value" style="font-size:0.9em;"><code>{sysinfo['model']}</code></div></div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BAW Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
         background: #0d1117; color: #c9d1d9; padding: 20px; }}
  h1 {{ color: #58a6ff; margin-bottom: 20px; }}
  h2 {{ color: #8b949e; font-size: 1em; text-transform: uppercase; letter-spacing: 1px;
        margin: 30px 0 10px; border-bottom: 1px solid #21262d; padding-bottom: 5px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
           gap: 12px; margin: 15px 0; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; }}
  .card .label {{ color: #8b949e; font-size: 0.8em; }}
  .card .value {{ font-size: 1.3em; font-weight: 600; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.9em; }}
  th {{ text-align: left; color: #8b949e; padding: 8px 6px; border-bottom: 1px solid #30363d; }}
  td {{ padding: 6px; border-bottom: 1px solid #21262d; }}
  .muted {{ color: #484f58; font-style: italic; text-align: center; padding: 20px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; }}
  .running {{ background: #1f6feb33; color: #58a6ff; }}
  .done {{ background: #23863633; color: #3fb950; }}
  .failed {{ background: #da363333; color: #f85149; }}
  .queued {{ background: #d2992233; color: #d29922; }}
  .activity {{ background: #8b949e33; color: #c9d1d9; }}
  code {{ background: #21262d; padding: 2px 5px; border-radius: 3px; font-size: 0.9em; }}
  .time {{ color: #484f58; font-size: 0.85em; text-align: right; margin-top: 20px; }}
  .status-bar {{ display: flex; gap: 8px; margin: 10px 0; flex-wrap: wrap; }}
  .status-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }}
  .dot-green {{ background: #3fb950; }}
  .dot-yellow {{ background: #d29922; }}
  .dot-red {{ background: #f85149; }}
</style>
</head>
<body>
<h1>⚫️ BAW Dashboard</h1>

<div class="status-bar">
  <span><span class="status-dot dot-green"></span> System Online</span>
  <span><span class="status-dot dot-green"></span> {sysinfo['uptime']}</span>
</div>

<h2>📊 System</h2>
<div class="grid">
  <div class="card"><div class="label">Uptime</div><div class="value">{sysinfo['uptime']}</div></div>
  <div class="card"><div class="label">Disk</div><div class="value">{sysinfo['disk']}</div></div>
  <div class="card"><div class="label">Memory</div><div class="value">{sysinfo['memory']}</div></div>
  <div class="card"><div class="label">Git HEAD</div><div class="value" style="font-size:0.9em;"><code>{sysinfo['git']}</code></div></div>
  {extra_cards}
</div>

<h2>📅 Scheduled Tasks</h2>
<table><thead><tr><th>Task</th><th>Cron</th><th>Next Run</th><th>Description</th></tr></thead>
<tbody>{sched_rows}</tbody></table>

<h2>🧠 Skills</h2>
<table><thead><tr><th>Name</th><th>Description</th><th>Tools</th><th>Mode</th></tr></thead>
<tbody>{skill_rows}</tbody></table>

<h2>📋 Recent Activity</h2>
<table><thead><tr><th>Type</th><th>Action</th><th>Status</th><th>Time</th></tr></thead>
<tbody>{recent_rows}</tbody></table>

<div class="time">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
</body>
</html>"""

    out_path = data_dir / DASHBOARD_FILE
    out_path.write_text(html, encoding="utf-8")
    return str(out_path.resolve())
