"""BAW built-in: system — service, cron, log management.

Manages BAW's own runtime environment: systemd services,
cron jobs, log rotation, disk usage.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))
_SYSTEMCTL = shutil.which("systemctl")
_SYSTEMD = _SYSTEMCTL is not None


def _run(cmd: list[str], timeout: int = 15) -> dict:
    """Run a shell command and return {ok, output, error}."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "output": r.stdout.strip(), "error": r.stderr.strip() or None, "exit_code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"Command timed out ({timeout}s)"}
    except FileNotFoundError:
        return {"ok": False, "output": "", "error": "Command not found"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def _service_status() -> list[dict]:
    """List BAW-related systemd services."""
    if not _SYSTEMD:
        return []
    r = _run([_SYSTEMCTL, "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "baw*"])
    if not r["ok"]:
        return []
    result = []
    for line in r["output"].split("\n"):
        parts = line.split(None, 3)
        if len(parts) >= 3:
            result.append({"name": parts[0], "load": parts[1], "active": parts[2],
                          "description": parts[3] if len(parts) > 3 else ""})
    return result


def _disk_usage() -> dict:
    """Check disk usage of key directories."""
    data = {}
    for path in [_BAW_DATA, _BAW_HOME / "logs"]:
        p = Path(path)
        if p.exists():
            r = _run(["du", "-sh", str(p)])
            data[str(p)] = r["output"] if r["ok"] else "N/A"
    return data


def _cron_status() -> list[dict]:
    """List BAW's internal cron jobs."""
    cron_file = _BAW_DATA / "cron" / "jobs.json"
    if cron_file.exists():
        try:
            jobs = json.loads(cron_file.read_text())
            if isinstance(jobs, list):
                return [{"id": j.get("id", "?"), "schedule": j.get("schedule", "?"),
                        "name": j.get("name", j.get("prompt", ""))[:50]} for j in jobs]
            return []
        except Exception:
            return []
    return []


def _handler(
    action: str = "status",
    name: str = "",
    log_lines: int = 30,
) -> str:
    """Manage BAW's runtime system.

    Supported actions:
      status        — Show overall system health (services, cron, disk, uptime)
      services      — List BAW systemd services with status
      restart       — Restart a systemd service (requires `name`)
      cron          — List internal cron jobs
      logs          — Show recent Docker logs (use `log_lines`)
      disk          — Show disk usage of key directories
    """
    actions = {
        "status": lambda: _overall_status(),
        "services": lambda: {"ok": True, "services": _service_status()},
        "restart": lambda: _run([_SYSTEMCTL, "restart", name]) if _SYSTEMD and name else
                   {"ok": False, "error": "systemd not available or no service name given"},
        "cron": lambda: {"ok": True, "cron_jobs": _cron_status()},
        "logs": lambda: _run(["docker", "logs", "baw-telegram", "--tail", str(min(log_lines, 200)), "--timestamps"]),
        "disk": lambda: {"ok": True, "disk_usage": _disk_usage()},
    }

    handler = actions.get(action)
    if not handler:
        return json.dumps({"ok": False, "error": f"Unknown action: {action}. Supported: {', '.join(actions.keys())}"},
                          ensure_ascii=False)

    result = handler()
    return json.dumps(result, ensure_ascii=False, indent=2)


def _overall_status() -> dict:
    """Combine all status checks into one."""
    services = _service_status()
    cron = _cron_status()
    disk = _disk_usage()
    # Container uptime
    uptime = ""
    r = _run(["docker", "inspect", "baw-telegram", "--format", "{{.State.StartedAt}}"])
    if r["ok"]:
        uptime = r["output"]
    # Container health
    health = ""
    r2 = _run(["docker", "inspect", "baw-telegram", "--format", "{{.State.Health.Status}}"])
    if r2["ok"]:
        health = r2["output"]
    return {
        "ok": True,
        "uptime": uptime,
        "health": health or "N/A",
        "services": services,
        "cron_jobs": cron,
        "disk_usage": disk,
    }


TOOL_DEF = {
    "name": "system",
    "description": (
        "[SELF-OPERATION] Manage BAW's own runtime system. "
        "Check health status, list/manage systemd services, view cron jobs, "
        "check disk usage, and view container logs. "
        "Part of BAW's self-operation capability."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "services", "restart", "cron", "logs", "disk"],
                "description": "Operation to perform.",
                "default": "status",
            },
            "name": {
                "type": "string",
                "description": "Service name (required for action=restart).",
                "default": "",
            },
            "log_lines": {
                "type": "integer",
                "description": "Number of log lines (for action=logs, max 200).",
                "default": 30,
            },
        },
        "required": [],
    },
    "risk_level": "medium",
}
