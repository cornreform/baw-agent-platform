"""BAW built-in: Install packages and CLI tools (npm, pip, apt, etc.)."""

import subprocess as sp
import shutil
import os
from pathlib import Path


def _detect_method(package: str) -> str:
    """Auto-detect install method based on available package managers."""
    if shutil.which("npm"):
        return "npm"
    elif shutil.which("pip") or shutil.which("pip3"):
        return "pip"
    elif shutil.which("apt-get"):
        return "apt"
    else:
        return ""


def _build_cmds_npm(package: str, env: dict) -> tuple[list[list[str]], dict]:
    """Build npm install command list with fallback."""
    cmds = []
    cmd = ["npm", "install", "-g", package]
    cmds.append(cmd)
    local_base = Path.home() / "npm"
    local_base.mkdir(parents=True, exist_ok=True)
    cmds.append(["npm", "install", "--prefix", str(local_base), package])
    local_bin = local_base / "bin"
    if str(local_bin) not in os.environ.get("PATH", ""):
        env["PATH"] = f"{local_bin}{os.pathsep}{env.get('PATH', '')}"
    return cmds, env


def _build_cmds_pip(package: str, global_install: bool) -> list[list[str]]:
    """Build pip install command list."""
    pip_cmd = shutil.which("pip3") or shutil.which("pip") or "pip"
    cmd = [pip_cmd, "install"]
    if not global_install:
        cmd.append("--user")
    cmd.append(package)
    return [cmd]


def _build_cmds_apt(package: str) -> list[list[str]]:
    """Build apt install command list."""
    return [
        ["apt-get", "update", "-qq"],
        ["apt-get", "install", "-y", "-qq", package],
    ]


def _build_cmds_gem(package: str, global_install: bool) -> list[list[str]]:
    """Build gem install command list."""
    cmd = ["gem", "install"]
    if not global_install:
        cmd.append("--user-install")
    cmd.append(package)
    return [cmd]


def _build_cmds_go(package: str, env: dict) -> tuple[list[list[str]], dict]:
    """Build go install command list."""
    env["GOPATH"] = env.get("GOPATH", str(Path.home() / "go"))
    return [["go", "install", package]], env


def _build_cmds_cargo(package: str) -> list[list[str]]:
    """Build cargo install command list."""
    return [["cargo", "install", package]]


def _build_install_commands(
    method: str, package: str, global_install: bool, env: dict
) -> tuple[str, list[list[str]], dict]:
    """Route to the right command builder based on method. Returns (exe, cmds, env)."""
    if method == "npm":
        cmds, env = _build_cmds_npm(package, env)
        exe = cmds[0][0]
    elif method == "pip":
        cmds = _build_cmds_pip(package, global_install)
        exe = cmds[0][0]
    elif method == "apt":
        cmds = _build_cmds_apt(package)
        exe = cmds[0][0]
    elif method == "gem":
        cmds = _build_cmds_gem(package, global_install)
        exe = cmds[0][0]
    elif method == "go":
        cmds, env = _build_cmds_go(package, env)
        exe = cmds[0][0]
    elif method == "cargo":
        cmds = _build_cmds_cargo(package)
        exe = cmds[0][0]
    else:
        raise ValueError(f"Unsupported install method '{method}'")
    return exe, cmds, env


def _exec_commands(cmds: list[list[str]], env: dict) -> tuple[list[str], bool]:
    """Execute a list of commands in order. Returns (outputs, any_success)."""
    outputs = []
    any_success = False
    for cmd in cmds:
        try:
            r = sp.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            stdout = r.stdout.strip()
            stderr = r.stderr.strip()
            if r.returncode == 0:
                exe = cmd[0]
                last_arg = cmd[-1] if len(cmd) > 1 else ""
                outputs.append(f"[OK] INSTALLED: {exe} install {last_arg}")
                if stdout:
                    outputs.append(stdout[:500])
                any_success = True
                break
            else:
                exe = cmd[0]
                last_arg = cmd[-1] if len(cmd) > 1 else ""
                outputs.append(f"[FAIL] FAILED: {exe} install {last_arg} (exit code {r.returncode})")
                if stderr:
                    outputs.append(f"    stderr: {stderr[:300]}")
        except sp.TimeoutExpired:
            outputs.append(f"[WARN] {' '.join(cmd[:3])}... timed out")
        except Exception as e:
            outputs.append(f"[WARN] {' '.join(cmd[:3])}... error: {e}")
    return outputs, any_success


def _verify_install(package: str, outputs: list[str]) -> list[str]:
    """Verify that the installed binary is accessible."""
    binary_name = package.split("/")[-1].split("@")[0].replace("-cli", "").replace("cli-", "")
    if shutil.which(binary_name):
        binary_path = shutil.which(binary_name)
        version_output = ""
        for flag in ["--version", "-v", "version"]:
            try:
                r = sp.run([binary_name, flag], capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    v = r.stdout.strip()[:80] or r.stderr.strip()[:80]
                    if v:
                        version_output = version_output or v
                    break
            except Exception:
                continue
        loc = f" at {binary_path}" if binary_path else ""
        ver = f" (v{version_output})" if version_output else ""
        outputs.append(f"[OK] INSTALLED: {binary_name}{ver}{loc}")
    else:
        local_bin = Path.home() / "npm" / "node_modules" / ".bin"
        if (local_bin / binary_name).exists():
            outputs.append(f"[OK] Verified: '{binary_name}' installed at {local_bin / binary_name}")
        elif (local_bin / package.split("/")[-1].split("@")[0]).exists():
            outputs.append(f"[OK] Verified: '{package.split('/')[-1].split('@')[0]}' installed at {local_bin / package.split('/')[-1].split('@')[0]}")
        else:
            outputs.append(f"[WARN] '{binary_name}' may be installed but not in PATH. Try: export PATH=\"{local_bin}:$PATH\"")
    return outputs


def install(package: str, method: str = "auto", global_install: bool = True) -> str:
    """Install a package or CLI tool using the specified method.

    Supports: npm, pip, apt, gem, go, cargo, etc.
    Auto-detects method if not specified.

    Args:
        package: Package name or 'name@version'
        method: 'npm', 'pip', 'apt', 'gem', 'go', 'cargo', or 'auto'
        global_install: Install globally (npm -g, pip --user, etc.)

    Returns:
        Installation result message
    """
    method = method.lower().strip()

    # Auto-detect method
    if method == "auto":
        method = _detect_method(package)
        if not method:
            return "Error: Cannot auto-detect install method. Please specify: npm, pip, apt, gem, go, cargo"

    # Build commands
    env = os.environ.copy()
    try:
        exe, cmds, env = _build_install_commands(method, package, global_install, env)
    except ValueError as e:
        return f"Error: {e}. Supported: npm, pip, apt, gem, go, cargo"

    # Check if command exists
    if not shutil.which(exe):
        return f"Error: '{exe}' command not found. Cannot install via {method}."

    # Run install commands
    outputs, any_success = _exec_commands(cmds, env)
    if not any_success:
        return "\n".join(outputs)

    # Verify
    outputs = _verify_install(package, outputs)
    return "\n".join(outputs)


TOOL_DEF = {
    "name": "install",
    "description": (
        "Install a package or CLI tool using npm, pip, apt, gem, go, or cargo. "
        "Use this when a required command is missing (e.g., 'mmx not found'). "
        "Auto-detects install method if not specified. "
        "Examples: install('mmx-cli', 'npm'), install('requests', 'pip')."
    ),
    "handler": install,
    "parameters": {
        "type": "object",
        "properties": {
            "package": {
                "type": "string",
                "description": "Package name, e.g. 'mmx-cli', 'requests', 'ffmpeg'",
            },
            "method": {
                "type": "string",
                "description": "Install method: npm, pip, apt, gem, go, cargo, or auto (default)",
                "default": "auto",
            },
            "global_install": {
                "type": "boolean",
                "description": "Install globally (npm -g, etc.). Default True.",
                "default": True,
            },
        },
        "required": ["package"],
    },
    "risk_level": "high",  # Can modify system state
}
