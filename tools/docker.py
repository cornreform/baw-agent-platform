"""BAW built-in: docker — build, restart, status, logs, cleanup.

Allows BAW to manage its own Docker lifecycle: rebuild image, restart
container, check health, view logs.

Depends on Docker socket + CLI bind-mounted by docker-compose.yml:
  - /var/run/docker.sock   → Docker daemon access
  - /usr/bin/docker         → Docker CLI
  - /usr/libexec/docker/cli-plugins/docker-compose  → Docker Compose plugin
"""
import json
import os
import subprocess
import shutil
from pathlib import Path


# ── Paths ────────────────────────────────────────────────────

_COMPOSE_DIR = Path(os.environ.get("BAW_HOME", "/app"))
_DOCKER = shutil.which("docker")
_CONTAINER = "baw-telegram"
_SERVICE = "baw-telegram"


# ── Helpers ──────────────────────────────────────────────────

def _docker(*args: str, timeout: int = 120) -> dict:
    """Run docker command. Returns {ok, output, error}."""
    if not _DOCKER:
        return {"ok": False, "output": "", "error": "docker CLI not available (check bind mount)"}
    try:
        r = subprocess.run(
            [_DOCKER] + list(args),
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_COMPOSE_DIR),
        )
        output = r.stdout.strip()
        error = r.stderr.strip()
        return {
            "ok": r.returncode == 0,
            "output": output,
            "error": error or None,
            "exit_code": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"docker command timed out ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def _compose(*args: str, timeout: int = 180) -> dict:
    """Run docker compose command. Returns {ok, output, error}."""
    if not _DOCKER:
        return {"ok": False, "output": "", "error": "docker CLI not available"}
    try:
        cmd = [_DOCKER, "compose"] + list(args)
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_COMPOSE_DIR),
        )
        output = r.stdout.strip()
        error = r.stderr.strip()
        return {
            "ok": r.returncode == 0,
            "output": output,
            "error": error or None,
            "exit_code": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"docker compose command timed out ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


# ── Tool handler ─────────────────────────────────────────────

def _handler(
    action: str = "status",
    lines: int = 30,
) -> str:
    """Execute a Docker operation for BAW's own container.

    Supported actions:
      status      — Show container health and uptime status
      build       — Rebuild BAW image from current code
      restart     — Rebuild + restart BAW container (zero-downtime via compose)
      logs        — Show recent container logs (use `lines` param)
      images      — List BAW Docker images with sizes
      cleanup     — Remove unused Docker images / build cache
      health      — Run healthcheck and report result
    """
    actions = {
        "status": lambda: _docker("ps", "--filter", f"name={_CONTAINER}",
                                  "--format", "{{.Names}} {{.Status}} {{.Image}}"),
        "build": lambda: _compose("build", _SERVICE),
        "restart": lambda: _compose("up", "-d", _SERVICE, "--build"),
        "logs": lambda: _docker(
            "logs", _CONTAINER, "--tail", str(min(lines, 200)),
            "--timestamps"
        ),
        "images": lambda: _docker(
            "images", "baw-baw-telegram", "--format",
            "{{.Repository}}:{{.Tag}} {{.Size}} {{.CreatedSince}}"
        ),
        "cleanup": lambda: _docker("system", "prune", "-f", "--filter", "until=24h"),
        "health": lambda: _docker(
            "inspect", _CONTAINER,
            "--format", "{{.State.Health.Status}} {{.State.StartedAt}}"
        ),
    }

    handler = actions.get(action)
    if not handler:
        return json.dumps({
            "ok": False,
            "error": f"Unknown action: {action}. Supported: {', '.join(actions.keys())}",
        }, ensure_ascii=False)

    result = handler()
    result["action"] = action
    result["container"] = _CONTAINER
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "docker",
    "description": (
        "[SELF-DEPLOY] Docker operations for BAW's own container lifecycle. "
        "Use this to rebuild the BAW image after code changes, restart the container, "
        "check health/status, view logs, and cleanup old images. "
        "Combined with the git tool, BAW can fully self-deploy: "
        "code → commit → push → rebuild → restart."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "build", "restart", "logs", "images", "cleanup", "health"],
                "description": "Docker operation to perform.",
                "default": "status",
            },
            "lines": {
                "type": "integer",
                "description": "Number of log lines (for action=logs, max 200).",
                "default": 30,
            },
        },
        "required": [],
    },
    "risk_level": "high",
}
