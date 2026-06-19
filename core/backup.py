"""P4: Backup System — daily auto-backup + restore command.

Backs up critical BAW data (~/.baw/ config, memory, sessions, skills).
Excludes large/reproducible dirs (py-user, pylib, downloads, fonts).
Keeps last 7 daily backups.
"""
from __future__ import annotations

import os
import tarfile
import shutil
import time
from pathlib import Path
from datetime import datetime, timezone


BACKUP_DIR = Path.home() / ".baw" / "backups"
MAX_BACKUPS = 7

# ── Directories/files to EXCLUDE from backup (large, reproducible, or temp) ──
_EXCLUDE_NAMES = {
    "backups",         # don't backup backups
    "py-user",         # Python user site-packages (~5GB, reproducible)
    "pylib",           # Python lib cache (~5GB, reproducible)
    "downloads",       # downloaded files (~53MB, reproducible)
    "fonts",           # font files (~5MB, downloadable)
    "skills-quarantine",  # quarantined skills
    "tasks",           # task runtime dirs (transient)
    "logs",            # old log files
    "audio_cache",     # TTS audio cache
    "tts_cache",       # TTS cache
    "backups",         # redundant safety
}

# ── Files we MUST back up (even if in root of .baw/) ──
_CRITICAL_FILES = {
    "config.yaml",
    ".env",
    "SOUL.md",
    "schedule.yaml",
    "MEMORY.md",
    "behavior.jsonl",
    "store.jsonl",
}


def create_backup() -> dict:
    """Create a timestamped backup of critical ~/.baw/ data.
    Excludes large reproducible dirs (py-user, pylib, downloads, fonts).
    Returns {path, size_bytes, size_mb, timestamp}.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    backup_name = f"baw-backup-{ts}.tar.gz"
    backup_path = BACKUP_DIR / backup_name

    baw_dir = Path.home() / ".baw"
    file_count = 0

    # Use gzip level 1 for speed (level 9 is 5-10x slower, only ~20% smaller)
    with tarfile.open(backup_path, "w:gz", compresslevel=1) as tar:
        for item in sorted(baw_dir.iterdir()):
            name = item.name
            # Skip excluded dirs/files
            if name in _EXCLUDE_NAMES:
                continue
            # Skip hidden temp files
            if name.startswith(".") and name not in _CRITICAL_FILES:
                continue
            # Skip large behavior log files (>10MB)
            if item.is_file() and item.stat().st_size > 10_000_000:
                continue
            tar.add(item, arcname=name)
            # Count files (approximate)
            if item.is_file():
                file_count += 1
            elif item.is_dir():
                file_count += sum(1 for _ in item.rglob("*") if _.is_file())

    size_bytes = backup_path.stat().st_size

    # Cleanup old backups (keep last MAX_BACKUPS)
    _cleanup_old_backups()

    return {
        "path": str(backup_path),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "files": file_count,
        "timestamp": ts,
    }


def restore_backup(date_str: str) -> dict:
    """Restore from a dated backup. date_str format: '2026-06-15' or 'latest'.
    Returns {status, files_restored, details}."""
    if not BACKUP_DIR.exists():
        return {"status": "error", "detail": "No backup directory"}

    backups = sorted(BACKUP_DIR.glob("baw-backup-*.tar.gz"))
    if not backups:
        return {"status": "error", "detail": "No backups found"}

    if date_str == "latest":
        target = backups[-1]
    else:
        target = None
        for b in backups:
            if date_str in b.name:
                target = b
                break
        if not target:
            return {
                "status": "error",
                "detail": f"No backup for {date_str}. Available: {[b.name for b in backups[-5:]]}",
            }

    baw_dir = Path.home() / ".baw"
    restored = []

    with tarfile.open(target, "r:gz") as tar:
        for member in tar.getmembers():
            dest = baw_dir / member.name
            if dest.parent == BACKUP_DIR:
                continue
            tar.extract(member, baw_dir)
            restored.append(member.name)

    return {
        "status": "ok",
        "backup_file": str(target),
        "files_restored": len(restored),
        "details": restored[:20],
    }


def list_backups() -> list[dict]:
    """List all available backups."""
    if not BACKUP_DIR.exists():
        return []
    backups = sorted(BACKUP_DIR.glob("baw-backup-*.tar.gz"), reverse=True)
    result = []
    for b in backups:
        stat = b.stat()
        result.append({
            "name": b.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return result


def pre_upgrade_snapshot() -> dict:
    """Create a special pre-upgrade backup."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    backup_name = f"baw-pre-upgrade-{ts}.tar.gz"
    backup_path = BACKUP_DIR / backup_name

    baw_dir = Path.home() / ".baw"
    with tarfile.open(backup_path, "w:gz", compresslevel=1) as tar:
        for item in sorted(baw_dir.iterdir()):
            name = item.name
            if name in _EXCLUDE_NAMES:
                continue
            if name.startswith(".") and name not in _CRITICAL_FILES:
                continue
            tar.add(item, arcname=name)

    return {
        "path": str(backup_path),
        "size_mb": round(backup_path.stat().st_size / (1024 * 1024), 2),
        "timestamp": ts,
    }


def _cleanup_old_backups():
    """Keep only the last MAX_BACKUPS daily backups."""
    backups = sorted(BACKUP_DIR.glob("baw-backup-*.tar.gz"))
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        oldest.unlink()
