"""
BAW — Self-Update from Git + Version Info
"""
from __future__ import annotations
import os, sys, subprocess
from pathlib import Path

BAW_ROOT = Path(__file__).parent.parent.resolve()

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"


def _get_version() -> str:
    """Extract version from baw script."""
    baw_script = BAW_ROOT / "baw"
    if baw_script.exists():
        for line in baw_script.read_text().splitlines():
            if "BAW_VERSION" in line:
                parts = line.split('"')
                return parts[1] if len(parts) > 1 else "?"
    return "?"


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def cmd_version(data_dir: Path):
    """Show version info."""
    version = _get_version()
    print(f"{C_BOLD}BAW (Black And White) Agent Platform{C_RESET}")
    print(f"  Version: {version}")

    r = _run(["git", "-C", str(BAW_ROOT), "log", "--oneline", "-1"])
    if r.returncode == 0 and r.stdout.strip():
        print(f"  Commit: {r.stdout.strip()}")

    r = _run(["git", "-C", str(BAW_ROOT), "branch", "--show-current"])
    if r.returncode == 0 and r.stdout.strip():
        print(f"  Branch: {r.stdout.strip()}")

    r = _run(["docker", "inspect", "baw-telegram", "--format", "{{.Image}}"])
    if r.returncode == 0 and r.stdout.strip():
        print(f"  Docker image: {r.stdout.strip()[:20]}…")
    r2 = _run(["docker", "ps", "--filter", "name=baw", "--format", "{{.Status}}"])
    if r2.returncode == 0 and r2.stdout.strip():
        print(f"  Docker status: {r2.stdout.strip()}")

    r = _run(["python3", "--version"])
    print(f"  Python: {r.stdout.strip()}")


def cmd_update(data_dir: Path):
    """Pull latest code, rebuild Docker, restart."""
    if not (BAW_ROOT / ".git").exists():
        print(f"  {C_RED}✗{C_RESET} Not a git repo at {BAW_ROOT}")
        return False

    print(f"  {C_YELLOW}⟳{C_RESET} Pulling latest code...")
    r = _run(["git", "-C", str(BAW_ROOT), "pull", "origin", "main"], timeout=30)
    if r.returncode != 0:
        print(f"  {C_RED}✗{C_RESET} Git pull failed: {r.stderr[:200]}")
        return False
    if "Already up to date" in r.stdout:
        print(f"  {C_GREEN}✓{C_RESET} Already up to date")
    else:
        print(f"  {C_GREEN}✓{C_RESET} Pulled latest code")

    print(f"  {C_YELLOW}⟳{C_RESET} Building Docker image...")
    r = _run(["docker", "compose", "build", "baw-telegram"], timeout=300)
    if r.returncode != 0:
        print(f"  {C_RED}✗{C_RESET} Docker build failed: {r.stderr[:200]}")
        return False
    print(f"  {C_GREEN}✓{C_RESET} Docker image built")

    print(f"  {C_YELLOW}⟳{C_RESET} Restarting container...")
    r = _run(["docker", "compose", "up", "-d", "baw-telegram"], timeout=30)
    if r.returncode != 0:
        print(f"  {C_RED}✗{C_RESET} Docker restart failed: {r.stderr[:200]}")
        return False

    # Verify
    import time
    time.sleep(3)
    r = _run(["docker", "ps", "--filter", "name=baw", "--format", "{{.Names}} {{.Status}}"])
    if "healthy" in r.stdout:
        print(f"  {C_GREEN}✓{C_RESET} BAW updated and running")
        return True
    else:
        print(f"  {C_YELLOW}⚠{C_RESET} Container restarted, waiting for health: {r.stdout.strip()}")
        return True
