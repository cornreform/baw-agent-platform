"""
BAW — Auto-Save & Version Control Integration
Every significant BAW action is auto-committed to git with timestamped messages.

Behaviour:
  - Before each file write: log to FileHistory
  - After each meaningful step: auto git commit
  - Commit messages include: [BAW] <action> — <timestamp>
  - Never ask user about commits — BAW handles versioning autonomously
"""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone


# Track last commit to avoid redundant commits
_last_commit: Optional[str] = None
_last_commit_time: float = 0


def auto_commit(repo_dir: Path | str, message: str,
                author: str = "BAW Agent <baw@local>") -> Optional[str]:
    """Auto-commit changes to git repo.

    Args:
        repo_dir: Path to git repo
        message: Commit message
        author: Git author string

    Returns:
        Commit hash if committed, None if nothing to commit or error
    """
    global _last_commit, _last_commit_time

    p = Path(repo_dir).expanduser().resolve()
    if not (p / ".git").exists():
        return None  # Not a git repo

    # Stage all changes
    result = subprocess.run(
        ["git", "add", "-A"],
        cwd=str(p),
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return None

    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(p),
        capture_output=True, timeout=10,
    )
    if result.returncode == 0:
        return None  # Nothing changed

    # Add timestamp to message
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    full_message = f"[BAW] {message} — {ts}"

    # Commit
    result = subprocess.run(
        ["git", "commit", "-m", full_message, "--author", author],
        cwd=str(p),
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return None

    # Extract commit hash
    for line in result.stdout.split("\n"):
        if line.startswith("["):
            _last_commit = line.split("]")[0].lstrip("[")
            break
    _last_commit_time = 0

    return _last_commit


def auto_push(repo_dir: Path | str) -> bool:
    """Try to push to remote. Returns True if successful or not needed."""
    p = Path(repo_dir).expanduser().resolve()

    # Check if remote exists
    result = subprocess.run(
        ["git", "remote"],
        cwd=str(p), capture_output=True, text=True, timeout=5,
    )
    if not result.stdout.strip():
        return True  # No remote configured, skip

    # Push
    result = subprocess.run(
        ["git", "push"],
        cwd=str(p), capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def get_last_commit() -> Optional[str]:
    """Get the last auto-commit hash."""
    return _last_commit


def get_commit_log(repo_dir: Path | str, limit: int = 10) -> list[dict]:
    """Get recent commit history."""
    p = Path(repo_dir).expanduser().resolve()
    result = subprocess.run(
        ["git", "log", f"-{limit}", "--format=%H|%ai|%s"],
        cwd=str(p), capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return []

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({
                "hash": parts[0][:8],
                "timestamp": parts[1],
                "message": parts[2],
            })
    return commits
