"""BAW built-in: resource_monitor — disk, memory, auto-cleanup.

Monitors BAW's resource usage (disk, memory, session files)
and provides auto-cleanup for stale data. Runs autonomously.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))
_DOCKER = shutil.which("docker")


def _run(cmd: list[str], timeout: int = 15) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "output": r.stdout.strip(), "error": r.stderr.strip() or None}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def _size_mb(path: Path) -> float:
    """Get total size of a path in MB."""
    if not path.exists():
        return 0
    r = _run(["du", "-sb", str(path)])
    if r["ok"] and r["output"]:
        try:
            bytes_val = int(r["output"].split()[0])
            return round(bytes_val / (1024 * 1024), 2)
        except (ValueError, IndexError):
            pass
    return 0


def _disk_report() -> dict:
    """Report disk usage of all BAW data directories."""
    dirs = {
        "sessions": _BAW_DATA / "sessions",
        "memory": _BAW_DATA / "memory",
        "logs": _BAW_DATA / "logs",
        "cron": _BAW_DATA / "cron" / "jobs.json",
        "tasks": _BAW_DATA / "tasks",
        "config": _BAW_DATA / "config.yaml",
        "evolve": _BAW_DATA / "evolve",
    }
    report = {}
    for name, path in dirs.items():
        if path.exists():
            if path.is_file():
                report[name] = f"{round(path.stat().st_size / 1024, 1)}KB"
            else:
                report[name] = f"{_size_mb(path)}MB"
        else:
            report[name] = "0MB"
    # Total BAW data dir
    report["total"] = f"{_size_mb(_BAW_DATA)}MB"
    # System disk
    r = _run(["df", "-h", "/"])
    if r["ok"]:
        lines = r["output"].split("\n")
        report["system_disk"] = lines[1] if len(lines) > 1 else r["output"]
    return report


def _stale_sessions() -> list[str]:
    """Find session files older than 24h."""
    sessions_dir = _BAW_DATA / "sessions"
    if not sessions_dir.exists():
        return []
    stale = []
    for f in sorted(sessions_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix == ".json" and time.time() - f.stat().st_mtime > 86400:
            stale.append(f.name)
    return stale


def _old_logs() -> list[str]:
    """Find log files >50MB or older than 7 days."""
    logs_dir = _BAW_DATA / "logs"
    if not logs_dir.exists():
        return []
    old = []
    for f in logs_dir.iterdir():
        if f.is_file():
            if time.time() - f.stat().st_mtime > 604800:  # 7 days
                old.append(f.name)
            elif f.stat().st_size > 50 * 1024 * 1024:  # >50MB
                old.append(f"{f.name} ({round(f.stat().st_size / (1024*1024), 1)}MB)")
    return old


def _cleanup(aggressive: bool = False) -> dict:
    """Clean up stale sessions, old logs, and temp files."""
    results = []

    # Sessions older than 24h
    stale = _stale_sessions()
    if stale:
        count = 0
        for s in stale:
            try:
                (Path(_BAW_DATA / "sessions" / s)).unlink(missing_ok=True)
                count += 1
            except Exception:
                pass
        results.append(f"Cleaned {count}/{len(stale)} stale sessions")

    # Docker system prune (not aggressive by default)
    if aggressive and _DOCKER:
        r = _run([_DOCKER, "system", "prune", "-f", "--filter", "until=24h"])
        if r["ok"]:
            results.append(f"Docker cleanup: {r['output'][:100]}")
        else:
            results.append("Docker cleanup skipped (not available)")

    # Token log compaction
    token_log = _BAW_DATA / "logs" / "tokens.jsonl"
    if token_log.exists() and token_log.stat().st_size > 5 * 1024 * 1024:  # >5MB
        try:
            lines = token_log.read_text().split("\n")
            # Keep last 1000 entries
            kept = lines[-1000:]
            token_log.write_text("\n".join(kept))
            results.append(f"Compacted token log: {len(lines)} → {len(kept)} entries")
        except Exception as e:
            results.append(f"Token log compaction failed: {e}")

    # Temp files
    if aggressive:
        r = _run(["find", "/tmp", "-type", "f", "-atime", "+1", "-delete", "-print"], timeout=30)
        if r["ok"]:
            deleted = len([l for l in r["output"].split("\n") if l.strip()]) if r["output"] else 0
            if deleted:
                results.append(f"Cleaned {deleted} temp files")

    return {"ok": True, "actions": results}


def _handler(
    action: str = "report",
    aggressive: bool = False,
) -> str:
    """Monitor and manage BAW's resource usage.

    Supported actions:
      report    — Show disk usage report for all BAW data directories
      cleanup   — Clean stale sessions, old logs, temp files
      stale     — List stale sessions (older than 24h)
      old_logs  — List large/old log files
    """
    actions = {
        "report": lambda: {"ok": True, "disk": _disk_report()},
        "cleanup": lambda: _cleanup(aggressive=aggressive),
        "stale": lambda: {"ok": True, "stale_sessions": _stale_sessions()},
        "old_logs": lambda: {"ok": True, "old_logs": _old_logs()},
    }

    handler = actions.get(action)
    if not handler:
        return json.dumps({"ok": False, "error": f"Unknown action: {action}"}, ensure_ascii=False)

    result = handler()
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "resource_monitor",
    "description": (
        "[SELF-OPERATION] Monitor and manage BAW's resources. "
        "Check disk usage of sessions/memory/logs, find stale sessions, "
        "clean up old data automatically. "
        "Part of BAW's self-operation capability."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["report", "cleanup", "stale", "old_logs"],
                "description": "Operation to perform.",
                "default": "report",
            },
            "aggressive": {
                "type": "boolean",
                "description": "Aggressive cleanup: Docker prune + temp file cleanup.",
                "default": False,
            },
        },
        "required": [],
    },
    "risk_level": "medium",
}
