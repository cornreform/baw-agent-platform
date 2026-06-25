"""baw restart / gateway — restart, start, stop BAW (bare metal + Docker)."""
import sys, subprocess
from pathlib import Path
from cli import console

def _is_docker():
    return Path("/.dockerenv").exists()

def cmd_restart(force: bool = False):
    if not force:
        console.print("[baw.warning]⚠  This will restart BAW.[/baw.warning]")
        try:
            answer = input("[baw.gold]Continue? [y/N]:[/baw.gold] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[baw.muted]Cancelled.[/baw.muted]")
            sys.exit(0)
        if answer not in ("y", "yes"):
            console.print("[baw.muted]Cancelled.[/baw.muted]")
            return

    console.print("[baw.warning]⏳ Restarting BAW...[/baw.warning]")
    if _is_docker():
        r = subprocess.run(["docker", "restart", "baw-telegram"], capture_output=True, timeout=30)
    else:
        r = subprocess.run(["systemctl", "--user", "restart", "baw"], capture_output=True, timeout=15)
    if r.returncode == 0:
        console.print("[baw.success]✓ BAW restarted.[/baw.success]")
    else:
        console.print(f"[baw.error]✗ Failed:[/baw.error] {r.stderr.decode()[:200]}")

def cmd_gateway_start():
    console.print("[baw.warning]⏳ Starting BAW...[/baw.warning]")
    if _is_docker():
        r = subprocess.run(["docker", "start", "baw-telegram"], capture_output=True, timeout=30)
    else:
        r = subprocess.run(["systemctl", "--user", "start", "baw"], capture_output=True, timeout=15)
    if r.returncode == 0:
        console.print("[baw.success]✓ BAW started.[/baw.success]")
    else:
        console.print(f"[baw.error]✗ Failed:[/baw.error] {r.stderr.decode()[:200]}")

def cmd_gateway_stop():
    console.print("[baw.warning]⏳ Stopping BAW...[/baw.warning]")
    if _is_docker():
        r = subprocess.run(["docker", "stop", "baw-telegram"], capture_output=True, timeout=30)
    else:
        r = subprocess.run(["systemctl", "--user", "stop", "baw"], capture_output=True, timeout=15)
    if r.returncode == 0:
        console.print("[baw.success]✓ BAW stopped.[/baw.success]")
    else:
        console.print(f"[baw.error]✗ Failed:[/baw.error] {r.stderr.decode()[:200]}")