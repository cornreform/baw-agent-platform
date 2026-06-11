"""
BAW — Backup & Restore
"""
from __future__ import annotations
import os, sys, subprocess, shutil, tarfile, json
from pathlib import Path
from datetime import datetime

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"


def _backup_dir(data_dir: Path) -> Path:
    """Create ~/.baw/backups/ dir."""
    b = data_dir / "backups"
    b.mkdir(parents=True, exist_ok=True)
    return b


def cmd_backup(data_dir: Path):
    """Backup config, .env, sessions, memory, SOUL.md to tar.gz."""
    backup_dir = _backup_dir(data_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"baw_backup_{ts}.tar.gz"

    items = ["config.yaml", ".env", "SOUL.md", "ORCHESTRATOR.md"]
    # Add dirs if they exist
    for d in ["memory", "sessions", "tasks", "skills", "backups"]:
        if (data_dir / d).exists():
            items.append(d)

    print(f"  {C_YELLOW}⟳{C_RESET} Backing up {len(items)} items...")

    with tarfile.open(dest, "w:gz") as tar:
        for item in items:
            path = data_dir / item
            if path.exists():
                tar.add(path, arcname=item)
                sz = path.stat().st_size if path.is_file() else 0
                label = f" ({sz:,} bytes)" if sz else " (dir)"
                print(f"    {C_DIM}{item}{label}{C_RESET}")

    print(f"\n  {C_GREEN}✓{C_RESET} Backup saved: {dest}")
    print(f"  {C_DIM}Size: {dest.stat().st_size / 1024:.0f} KB{C_RESET}")
    return dest


def cmd_restore(data_dir: Path, backup_path: str = ""):
    """Restore from a backup archive."""
    backup_dir = _backup_dir(data_dir)

    if backup_path:
        src = Path(backup_path)
    else:
        # Find latest backup
        backups = sorted(backup_dir.glob("baw_backup_*.tar.gz"), reverse=True)
        if not backups:
            print(f"  {C_RED}✗{C_RESET} No backups found in {backup_dir}")
            return
        src = backups[0]

    if not src.exists():
        print(f"  {C_RED}✗{C_RESET} Backup not found: {src}")
        return

    print(f"  {C_YELLOW}⚠{C_RESET} Restoring from: {src}")
    print(f"  {C_YELLOW}⚠{C_RESET} Current files will be overwritten")
    confirm = input(f"  Are you sure? (y/N): ").strip().lower()
    if confirm != "y":
        print(f"  {C_DIM}Restore cancelled{C_RESET}")
        return

    # Create backup of current state first
    print(f"  {C_YELLOW}⟳{C_RESET} Auto-backup current state before restore...")
    cmd_backup(data_dir)

    with tarfile.open(src, "r:gz") as tar:
        print(f"  {C_YELLOW}⟳{C_RESET} Restoring files...")
        for member in tar.getmembers():
            tar.extract(member, path=data_dir)
            print(f"    restored: {member.name}")

    print(f"\n  {C_GREEN}✓{C_RESET} Restore complete. Restart BAW to apply.")


def cmd_backup_list(data_dir: Path):
    """List available backups."""
    backup_dir = _backup_dir(data_dir)
    backups = sorted(backup_dir.glob("baw_backup_*.tar.gz"), reverse=True)

    if not backups:
        print(f"  {C_DIM}No backups found{C_RESET}")
        return

    print(f"  {C_BOLD}Available backups:{C_RESET}")
    for i, b in enumerate(backups, 1):
        size = b.stat().st_size / 1024
        ts = b.stem.replace("baw_backup_", "").replace("_", " ")[:15]
        print(f"  [{i}] {C_CYAN}{b.name}{C_RESET} ({size:.0f} KB, {ts})")
