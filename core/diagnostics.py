"""
BAW — Diagnostics (system info for debugging)
"""
from __future__ import annotations
import os, sys, subprocess, json, platform, shutil
from pathlib import Path
from datetime import datetime

BAW_ROOT = Path(__file__).parent.parent.resolve()
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_CYAN = "\033[96m"


def cmd_diagnostics(data_dir: Path):
    """Collect system info for debugging."""
    w, _ = shutil.get_terminal_size()
    print(f"{C_CYAN}{'=' * w}{C_RESET}")
    print(f"{C_BOLD}  BAW Diagnostics{C_RESET}")
    print(f"  {C_DIM}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{C_RESET}")
    print(f"{C_CYAN}{'=' * w}{C_RESET}")

    # OS
    print(f"\n{C_BOLD}OS:{C_RESET}")
    print(f"  Platform: {sys.platform}")
    print(f"  Release: {platform.release()}")

    # Python
    print(f"\n{C_BOLD}Python:{C_RESET}")
    print(f"  Version: {sys.version.split()[0]}")
    print(f"  Executable: {sys.executable}")

    # Environment
    print(f"\n{C_BOLD}Environment:{C_RESET}")
    print(f"  HOME: {os.environ.get('HOME', '?')}")
    print(f"  PWD: {os.environ.get('PWD', '?')}")
    print(f"  PATH (first): {os.environ.get('PATH', '').split(':')[0]}")
    print(f"  Memory: {data_dir}")

    # Docker
    print(f"\n{C_BOLD}Docker:{C_RESET}")
    try:
        r = subprocess.run(["docker", "--version"], capture_output=True, text=True, timeout=5)
        print(f"  Version: {r.stdout.strip()}")
        r = subprocess.run(["docker", "ps", "--filter", "name=baw", "--format", "{{.Names}} {{.Image}} {{.Status}}"],
                          capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            print(f"  BAW: {r.stdout.strip()}")
        else:
            print(f"  BAW: not running")
        r = subprocess.run(["docker", "inspect", "baw-telegram", "--format", "{{.State.StartedAt}}"],
                          capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            print(f"  Started: {r.stdout.strip()}")
    except Exception as e:
        print(f"  Error: {e}")

    # Git
    print(f"\n{C_BOLD}Git:{C_RESET}")
    try:
        r = subprocess.run(["git", "-C", str(BAW_ROOT), "log", "--oneline", "-5"],
                          capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            for line in r.stdout.strip().splitlines():
                print(f"  {line}")
    except Exception as e:
        print(f"  Error: {e}")

    # Config
    print(f"\n{C_BOLD}Config size:{C_RESET}")
    for f in ["config.yaml", ".env", "SOUL.md"]:
        p = data_dir / f
        if p.exists():
            print(f"  {f}: {p.stat().st_size:,} bytes")

    # Memory
    print(f"\n{C_BOLD}Memory:{C_RESET}")
    mem_dir = data_dir / "memory"
    if mem_dir.exists():
        total = sum(1 for _ in (mem_dir / "store.jsonl").open() if (mem_dir / "store.jsonl").exists()) if (mem_dir / "store.jsonl").exists() else 0
        print(f"  Entries: ? (check with --memory-stats)")

    # Sessions
    sess_dir = data_dir / "sessions"
    if sess_dir.exists():
        count = len(list(sess_dir.glob("*.json")))
        print(f"  Session files: {count}")

    print(f"\n{C_DIM}To save diagnostics: baw --diagnostics --verbose > debug_report.txt{C_RESET}")
