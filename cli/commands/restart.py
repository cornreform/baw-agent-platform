"""baw restart — restart the BAW Docker container with confirmation."""
import sys
import subprocess
from cli import console


def cmd_restart(force: bool = False):
    if not force:
        console.print("[baw.warning]⚠  This will restart the BAW container.[/baw.warning]")
        try:
            answer = input("[baw.gold]Continue? [y/N]:[/baw.gold] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[baw.muted]Cancelled.[/baw.muted]")
            sys.exit(0)
        if answer not in ("y", "yes"):
            console.print("[baw.muted]Cancelled.[/baw.muted]")
            return

    console.print("[baw.warning]⏳ Restarting BAW bot container...[/baw.warning]")
    try:
        subprocess.run(["docker", "restart", "baw-telegram"], check=True,
                       capture_output=True, timeout=30)
        console.print("[baw.success]✓ BAW bot restarted successfully.[/baw.success]")
    except subprocess.CalledProcessError as e:
        console.print(f"[baw.error]✗ Failed to restart:[/baw.error] {e.stderr.decode()}")
        sys.exit(1)
    except FileNotFoundError:
        console.print("[baw.error]✗ Docker not found. Is Docker installed?[/baw.error]")
        sys.exit(1)
