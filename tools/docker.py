from __future__ import annotations
"""BAW built-in: lifecycle — build, restart, status, logs, cleanup.

Universal runtime manager. Auto-detects Docker vs bare-metal (systemctl)
and uses the appropriate commands. Same interface, same output format.

Docker mode (container):
  - build/restart → docker compose
  - status/logs   → docker inspect / docker logs

Bare-metal mode (systemd):
  - build/restart → git pull + pip install + systemctl restart baw
  - status/logs   → systemctl is-active / journalctl
"""
import json
import os
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path

_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_SERVICE = os.environ.get("BAW_SERVICE", "baw")

# ── Runtime detection ─────────────────────────────────────────

_DOCKER_CLI = shutil.which("docker")
_IS_DOCKER = (
    Path("/.dockerenv").exists()
    or os.environ.get("BAW_RUNTIME") == "docker"
)
_IS_BARE = os.path.exists("/run/systemd/system") or os.environ.get("BAW_RUNTIME") == "bare"

_MODE = "docker" if _IS_DOCKER else "bare"
_HOSTNAME = os.uname().nodename


def _run(cmd: list[str], timeout: int = 120, cwd: str | None = None) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {
            "ok": r.returncode == 0,
            "output": r.stdout.strip(),
            "error": r.stderr.strip() or None,
            "exit_code": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"command timed out ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


# ── Operation handlers ────────────────────────────────────────


def _docker_build(no_cache: bool = False) -> dict:
    """Docker mode: docker compose build + up -d."""
    compose_dir = _BAW_HOME
    compose_file = compose_dir / "docker-compose.yml"
    if not compose_file.exists():
        return {"ok": False, "output": "", "error": f"docker-compose.yml not found at {compose_file}"}

    build_args = ["compose", "build"]
    if no_cache:
        build_args.append("--no-cache")

    r = _run(["docker"] + build_args, timeout=300, cwd=str(compose_dir))
    if not r["ok"]:
        return r

    up = _run(["docker", "compose", "up", "-d"], timeout=60, cwd=str(compose_dir))
    return up if up["ok"] else r  # return build result even if up fails


def _bare_build(no_cache: bool = False) -> dict:
    """Bare-metal mode: git pull + pip install -e . + systemctl restart."""
    results = []

    # Git pull
    if (_BAW_HOME / ".git").exists():
        r = _run(["git", "pull"], timeout=60, cwd=str(_BAW_HOME))
        results.append(f"git pull: {'ok' if r['ok'] else 'FAILED'}")
    else:
        results.append("git: not a git repo (skipped)")

    # Pip install
    r = _run(["pip3", "install", "-e", "."], timeout=120, cwd=str(_BAW_HOME))
    results.append(f"pip install: {'ok' if r['ok'] else 'FAILED'}")

    # Restart
    r = _run(["systemctl", "restart", _SERVICE], timeout=15)
    results.append(f"systemctl restart {_SERVICE}: {'ok' if r['ok'] else 'FAILED'}")

    ok = all("ok" in line for line in results)
    return {"ok": ok, "output": "; ".join(results), "error": None if ok else "Some steps failed"}


def _docker_restart() -> dict:
    return _run(["docker", "compose", "restart"], timeout=30, cwd=str(_BAW_HOME))


def _bare_restart() -> dict:
    return _run(["systemctl", "restart", _SERVICE], timeout=15)


def _docker_status() -> dict:
    r = _run(["docker", "inspect", "--format",
              "{{.State.Status}}|{{.State.StartedAt}}|{{.Config.Image}}",
              os.environ.get("BAW_CONTAINER", "baw-telegram")], timeout=10)
    if not r["ok"]:
        return {"ok": False, "output": "", "error": r.get("error", "container not found")}

    parts = r["output"].strip().split("|")
    status = parts[0] if len(parts) > 0 else "unknown"
    started = parts[1] if len(parts) > 1 else ""
    image = parts[2] if len(parts) > 2 else ""

    try:
        dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        uptime = str(datetime.now(timezone.utc) - dt).split(".")[0]
    except Exception:
        uptime = "unknown"

    return {
        "ok": True,
        "mode": "docker",
        "status": status,
        "image": image,
        "uptime": uptime,
        "started": started,
    }


def _bare_status() -> dict:
    r = _run(["systemctl", "is-active", _SERVICE, "--quiet"], timeout=5)
    active = r["ok"]  # is-active returns 0 if active

    r2 = _run(["systemctl", "show", _SERVICE,
               "--property=ActiveEnterTimestamp,ExecMainPID"], timeout=5)

    started = ""
    pid = ""
    for line in r2.get("output", "").split("\n"):
        if line.startswith("ActiveEnterTimestamp="):
            started = line.split("=", 1)[1]
        elif line.startswith("ExecMainPID="):
            pid = line.split("=", 1)[1]

    uptime = "unknown"
    if started:
        try:
            # Parse: "Wed 2026-06-17 21:00:00 HKT" or similar
            parts = started.split()
            if len(parts) >= 3:
                dt = datetime.fromisoformat(f"{parts[1]} {parts[2]}+08:00")
                uptime = str(datetime.now(timezone.utc) - dt).split(".")[0]
        except Exception:
            pass

    return {
        "ok": True,
        "mode": "bare",
        "status": "running" if active else "stopped",
        "service": _SERVICE,
        "uptime": uptime,
        "pid": pid,
        "host": _HOSTNAME,
    }


def _docker_logs(lines: int = 50) -> dict:
    return _run(["docker", "logs", "--tail", str(lines),
                 os.environ.get("BAW_CONTAINER", "baw-telegram")], timeout=15)


def _bare_logs(lines: int = 50) -> dict:
    r = _run(["journalctl", "-u", _SERVICE, "-n", str(lines),
              "--no-pager", "-q"], timeout=15)
    return r


def _docker_cleanup(aggressive: bool = False) -> dict:
    if aggressive:
        r = _run(["docker", "system", "prune", "-f", "--filter", "until=24h"], timeout=30)
    else:
        r = _run(["docker", "image", "prune", "-f", "--filter",
                  "dangling=true"], timeout=15)
    return r


def _bare_cleanup(aggressive: bool = False) -> dict:
    results = []
    # pip cache
    r = _run(["pip3", "cache", "purge"], timeout=30)
    results.append(f"pip cache: {'cleared' if r['ok'] else 'skipped'}")
    # tmp
    if aggressive:
        r = _run(["find", "/tmp", "-type", "f", "-mtime", "+1", "-delete"], timeout=15)
        results.append("tmp files: cleaned (24h+)")
    return {"ok": True, "output": "; ".join(results), "error": None}


# ── Public API ────────────────────────────────────────────────


def _handler(
    action: str = "status",
    no_cache: bool = False,
    aggressive: bool = False,
    lines: int = 50,
) -> str:
    """Manage BAW lifecycle — works on Docker or bare-metal.

    Auto-detects runtime mode. Same commands, transparent backend.

    Args:
        action: 'status' | 'build' | 'restart' | 'logs' | 'cleanup'
        no_cache: For 'build' — skip Docker cache or pip reinstall
        aggressive: For 'cleanup' — aggressive prune
        lines: For 'logs' — number of lines (max 500)
    """
    lines = min(lines, 500)

    ops = {
        "status": lambda: _docker_status() if _IS_DOCKER else _bare_status(),
        "build": lambda: _docker_build(no_cache=no_cache) if _IS_DOCKER else _bare_build(no_cache=no_cache),
        "restart": lambda: _docker_restart() if _IS_DOCKER else _bare_restart(),
        "logs": lambda: _docker_logs(lines=lines) if _IS_DOCKER else _bare_logs(lines=lines),
        "cleanup": lambda: _docker_cleanup(aggressive=aggressive) if _IS_DOCKER else _bare_cleanup(aggressive=aggressive),
    }

    handler = ops.get(action)
    if not handler:
        return json.dumps({
            "ok": False,
            "error": f"Unknown action: {action}. Use: status | build | restart | logs | cleanup",
        }, ensure_ascii=False, indent=2)

    result = handler()
    result["mode"] = _MODE
    if "ok" not in result:
        result["ok"] = True

    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "lifecycle",
    "description": (
        "[LIFECYCLE] Manage BAW's own lifecycle — status, build, restart, logs, cleanup. "
        "Universal: works on Docker or bare-metal (systemd). "
        "Auto-detects runtime mode. Same commands on any platform."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "build", "restart", "logs", "cleanup"],
                "description": "Lifecycle operation to perform.",
                "default": "status",
            },
            "no_cache": {
                "type": "boolean",
                "description": "Build without cache (Docker: --no-cache, Bare: full pip reinstall).",
                "default": False,
            },
            "aggressive": {
                "type": "boolean",
                "description": "Aggressive cleanup mode.",
                "default": False,
            },
            "lines": {
                "type": "integer",
                "description": "Number of log lines to return (max 500).",
                "default": 50,
            },
        },
        "required": [],
    },
    "risk_level": "high",
}
