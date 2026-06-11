"""
BAW — Profile Management
Run multiple independent BAW instances with isolated configs.
"""
from __future__ import annotations
import os, sys, shutil
from pathlib import Path

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"


def _profiles_dir(data_dir: Path) -> Path:
    p = data_dir / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _active_symlink(data_dir: Path) -> Path:
    return data_dir / "profile"


def cmd_profile_list(data_dir: Path):
    """List all profiles."""
    profile_dir = _profiles_dir(data_dir)
    profiles = sorted(d.name for d in profile_dir.iterdir() if d.is_dir())

    active = None
    sym = _active_symlink(data_dir)
    if sym.exists() and sym.is_symlink():
        active = sym.resolve().name

    if not profiles:
        print(f"  {C_DIM}No profiles found. Create one with: baw --profile-create <name>{C_RESET}")
        print(f"  {C_DIM}Profiles store isolated config.yaml, .env, memory, sessions{C_RESET}")
        return

    print(f"\n  {C_BOLD}Profiles:{C_RESET}")
    for p in profiles:
        marker = f" {C_GREEN}← active{C_RESET}" if p == active else ""
        print(f"    {C_CYAN}{p}{C_RESET}{marker}")


def cmd_profile_create(data_dir: Path, name: str):
    """Create a new profile."""
    if not name:
        print(f"  {C_RED}✗{C_RESET} Profile name required")
        return
    profile_dir = _profiles_dir(data_dir) / name
    if profile_dir.exists():
        print(f"  {C_YELLOW}⚠{C_RESET} Profile '{name}' already exists")
        return
    profile_dir.mkdir(parents=True, exist_ok=True)
    # Copy current config
    for f in ["config.yaml", ".env", "SOUL.md"]:
        src = data_dir / f
        if src.exists():
            shutil.copy2(src, profile_dir / f)
    print(f"  {C_GREEN}✓{C_RESET} Profile '{name}' created at {profile_dir}")


def cmd_profile_use(data_dir: Path, name: str):
    """Switch to a profile. Creates config/env symlinks."""
    profile_dir = _profiles_dir(data_dir) / name
    if not profile_dir.exists():
        print(f"  {C_RED}✗{C_RESET} Profile '{name}' not found")
        return

    sym = _active_symlink(data_dir)
    if sym.exists() or sym.is_symlink():
        sym.unlink()
    sym.symlink_to(profile_dir)
    print(f"  {C_GREEN}✓{C_RESET} Switched to profile '{name}'")
    print(f"  {C_DIM}  Restart BAW for changes to take effect{C_RESET}")


def cmd_profile_delete(data_dir: Path, name: str):
    """Delete a profile."""
    profile_dir = _profiles_dir(data_dir) / name
    if not profile_dir.exists():
        print(f"  {C_RED}✗{C_RESET} Profile '{name}' not found")
        return

    sym = _active_symlink(data_dir)
    if sym.exists() and sym.is_symlink() and sym.resolve() == profile_dir:
        print(f"  {C_RED}✗{C_RESET} Cannot delete active profile. Switch first.")
        return

    print(f"  {C_YELLOW}⚠{C_RESET} Delete profile '{name}'?")
    confirm = input(f"  This will remove {profile_dir}. Are you sure? (y/N): ").strip().lower()
    if confirm == "y":
        shutil.rmtree(profile_dir)
        print(f"  {C_GREEN}✓{C_RESET} Profile '{name}' deleted")
    else:
        print(f"  {C_DIM}Cancelled{C_RESET}")
