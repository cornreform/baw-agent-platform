"""baw rebuild — Docker or bare-metal rebuild + restart. Cached by default."""
import subprocess
import sys
import shutil
from pathlib import Path
from cli import console

BAW_ROOT = Path("/app") if Path("/app").exists() else Path(__file__).resolve().parent.parent.parent
_HAS_DOCKER = shutil.which("docker") is not None
_SERVICE = "baw"
_IS_BARE = Path("/run/systemd/system").exists()


def _docker_rebuild(no_cache: bool = False, up: bool = True):
    import os
    compose_file = BAW_ROOT / "docker-compose.yml"
    if not compose_file.exists():
        console.print(f"[baw.error]✗ docker-compose.yml not found at {BAW_ROOT}[/baw.error]")
        sys.exit(1)

    os.chdir(str(BAW_ROOT))
    console.print("[baw.gold]🔨 BAW Docker rebuild...[/baw.gold]")

    cmd = ["docker", "compose", "build"]
    if no_cache:
        cmd.append("--no-cache")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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


def _bare_rebuild(no_cache: bool = False, up: bool = True):
    import os
    os.chdir(str(BAW_ROOT))

    console.print("[baw.gold]🔨 BAW bare-metal rebuild...[/baw.gold]")

    # Git pull
    if (BAW_ROOT / ".git").exists():
        console.print("[baw.muted]  git pull...[/baw.muted]")
        r = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=60, cwd=str(BAW_ROOT))
        if r.returncode != 0:
            console.print(f"[baw.warning]  ⚠ git pull warning: {r.stderr.strip()[-100:]}[/baw.warning]")
        else:
            console.print("[baw.success]  ✓ git pull[/baw.success]")

    # Pip install
    pip_cmd = ["pip3", "install", "-e", "."]
    if no_cache:
        pip_cmd.insert(2, "--no-cache-dir")
    console.print("[baw.muted]  pip install...[/baw.muted]")
    r = subprocess.run(pip_cmd, capture_output=True, text=True, timeout=120, cwd=str(BAW_ROOT))
    if r.returncode != 0:
        console.print(f"[baw.error]✗ pip install failed:[/baw.error] {r.stderr[-300:]}")
        sys.exit(1)
    console.print("[baw.success]  ✓ pip install[/baw.success]")

    # Restart
    if up:
        console.print("[baw.warning]🔄 Restarting systemd service...[/baw.warning]")
        try:
            subprocess.run(["systemctl", "restart", _SERVICE], capture_output=True, check=True, timeout=15)
            console.print("[baw.success]✓ BAW live (systemd)[/baw.success]")
        except subprocess.CalledProcessError as e:
            console.print(f"[baw.error]✗ systemctl restart failed: {e}[/baw.error]")
            sys.exit(1)


def cmd_rebuild(no_cache: bool = False, up: bool = True):
    if _HAS_DOCKER and (BAW_ROOT / "docker-compose.yml").exists():
        _docker_rebuild(no_cache=no_cache, up=up)
    elif _IS_BARE:
        _bare_rebuild(no_cache=no_cache, up=up)
    else:
        console.print("[baw.error]✗ No Docker or systemd detected — cannot rebuild[/baw.error]")
        sys.exit(1)
