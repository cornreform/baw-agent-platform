"""baw rebuild — fast Docker rebuild with caching."""
import subprocess
import sys
from pathlib import Path
from cli import console

BAW_ROOT = Path("/app")  # Inside container


def cmd_rebuild(no_cache: bool = False, up: bool = True):
    """Rebuild Docker image + restart. Layers cached by default."""
    import os
    os.chdir(str(BAW_ROOT))

    console.print("[baw.gold]🔨 BAW rebuild...[/baw.gold]")

    cmd = ["docker", "compose", "build"]
    if no_cache:
        cmd.append("--no-cache")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            console.print(f"[baw.error]✗ Build failed:[/baw.error] {result.stderr[-500:]}")
            sys.exit(1)

        last_line = [l for l in result.stderr.splitlines() if l.strip()][-3:] if result.stderr else ["done"]
        console.print("[baw.success]✓ Image built[/baw.success]")
        for line in last_line:
            if line.strip():
                console.print(f"  [baw.dim]{line.strip()}[/baw.dim]")

    except FileNotFoundError:
        console.print("[baw.error]✗ Docker not found[/baw.error]")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        console.print("[baw.error]✗ Build timed out[/baw.error]")
        sys.exit(1)

    if up:
        console.print("[baw.warning]🔄 Restarting container...[/baw.warning]")
        try:
            subprocess.run(["docker", "compose", "up", "-d"], capture_output=True, check=True, timeout=30)
            console.print("[baw.success]✓ BAW live[/baw.success]")
        except subprocess.CalledProcessError as e:
            console.print(f"[baw.error]✗ up failed:[/baw.error] {e.stderr.decode()[-300:] if e.stderr else e}")
            sys.exit(1)
