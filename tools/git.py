"""BAW built-in: git — commit, push, pull, status, log.

Allows BAW to manage its own source code repository using the host's
git binary (mounted via docker-compose bind mount: /usr/bin/docker).

Git config (user.name / user.email) is auto-configured on first use.
Authentication: SSH key or GITHUB_TOKEN from ~/.baw/.env is respected.
"""
import json
import os
import subprocess
import shutil
from pathlib import Path


# ── Repo path ────────────────────────────────────────────────

_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_GIT = shutil.which("git")


def _ensure_git() -> bool:
    """Check git binary exists."""
    if not _GIT:
        return False
    return True


def _ensure_configured() -> bool:
    """Ensure git user config is set."""
    if not _GIT:
        return False
    try:
        r = subprocess.run([_GIT, "config", "--global", "user.name"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0 or not r.stdout.strip():
            subprocess.run([_GIT, "config", "--global", "user.name", "BAW Agent"],
                           capture_output=True, timeout=5)
        r = subprocess.run([_GIT, "config", "--global", "user.email"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0 or not r.stdout.strip():
            subprocess.run([_GIT, "config", "--global", "user.email", "baw@baw.agent"],
                           capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def _git(*args: str, timeout: int = 30) -> dict:
    """Run a git command in the BAW repo. Returns {ok, output, error}."""
    if not _ensure_git():
        return {"ok": False, "output": "", "error": "git binary not available"}
    _ensure_configured()
    try:
        r = subprocess.run(
            [_GIT] + list(args),
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_BAW_HOME),
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
        return {"ok": False, "output": "", "error": f"git command timed out ({timeout}s)"}
    except FileNotFoundError:
        return {"ok": False, "output": "", "error": "git binary not found"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


# ── Tool handler ─────────────────────────────────────────────

def _commit_with_backup(message: str) -> dict:
    """Commit with auto-backup before the operation."""
    if not message:
        return {"ok": False, "output": "", "error": "commit requires a message"}
    # Auto-backup before destructive git operation
    try:
        from core.backup import auto_pre_mod_backup
        bkp = auto_pre_mod_backup()
    except Exception:
        pass  # non-fatal
    return _git("commit", "-m", message)

def _handler(
    action: str = "status",
    message: str = "",
    count: int = 5,
    branch: str = "",
) -> str:
    """Execute a git operation in the BAW repo.

    Supported actions:
      status    — Show working tree status (modified/untracked files)
      log       — Show recent commits (use `count` param)
      diff      — Show uncommitted changes
      add       — Stage all changes
      commit    — Commit staged changes (requires `message`)
      push      — Push commits to remote
      pull      — Pull latest from remote
      branch    — List branches (or create/switch with `branch` param)
      checkout  — Switch to a branch or restore a file
    """
    actions = {
        "status": lambda: _git("status", "--short"),
        "log": lambda: _git("log", f"--oneline", "-n", str(min(count, 50))),
        "diff": lambda: _git("diff"),
        "add": lambda: _git("add", "-A"),
        "commit": lambda: _commit_with_backup(message),
        "push": lambda: _git("push"),
        "pull": lambda: _git("pull"),
        "branch": lambda: _git("branch") if not branch else _git("checkout", "-b", branch)
                   if branch else _git("branch"),
        "checkout": lambda: _git("checkout", branch) if branch else
                    {"ok": False, "output": "", "error": "checkout requires a branch name"},
    }

    handler = actions.get(action)
    if not handler:
        return json.dumps({
            "ok": False,
            "error": f"Unknown action: {action}. Supported: {', '.join(actions.keys())}",
        }, ensure_ascii=False)

    result = handler()
    result["action"] = action
    result["repo"] = str(_BAW_HOME)
    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "git",
    "description": (
        "[SELF-DEPLOY] Git operations for BAW's own source code repository. "
        "Use this to commit code changes, push/pull from remote, check status and logs. "
        "Combined with the docker tool, BAW can fully self-deploy: "
        "code → commit → push → rebuild → restart."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "log", "diff", "add", "commit", "push", "pull", "branch", "checkout"],
                "description": "Git operation to perform.",
                "default": "status",
            },
            "message": {
                "type": "string",
                "description": "Commit message (required for action=commit).",
                "default": "",
            },
            "count": {
                "type": "integer",
                "description": "Number of log entries (for action=log, max 50).",
                "default": 5,
            },
            "branch": {
                "type": "string",
                "description": "Branch name (for action=checkout or action=branch to create new).",
                "default": "",
            },
        },
        "required": [],
    },
    "risk_level": "medium",
}
