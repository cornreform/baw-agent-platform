"""baw restart — restart the BAW Docker container."""
import subprocess
from cli import console


def cmd_restart():
    console.print("[baw.warning]⏳ Restarting BAW bot container...[/baw.warning]")
    try:
        subprocess.run(["docker", "restart", "baw-telegram"], check=True,
                       capture_output=True, timeout=30)
        console.print("[baw.success]✓ BAW bot restarted successfully.[/baw.success]")
    except subprocess.CalledProcessError as e:
        console.print(f"[baw.error]✗ Failed to restart:[/baw.error] {e.stderr.decode()}")
    except FileNotFoundError:
        console.print("[baw.error]✗ Docker not found. Is Docker installed?[/baw.error]")
