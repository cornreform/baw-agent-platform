"""BAW built-in: Install packages and CLI tools (npm, pip, apt, etc.)."""

import subprocess as sp
import shutil
import os
from pathlib import Path


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
        if shutil.which("npm"):
            method = "npm"
        elif shutil.which("pip") or shutil.which("pip3"):
            method = "pip"
        elif shutil.which("apt-get"):
            method = "apt"
        else:
            return "Error: Cannot auto-detect install method. Please specify: npm, pip, apt, gem, go, cargo"

    cmds = []
    env = os.environ.copy()

    if method == "npm":
        # Try global first, fallback to local user install
        cmd = ["npm", "install", "-g", package]
        cmds.append(cmd)
        # Fallback: local install to ~/npm
        local_base = Path.home() / "npm"
        local_base.mkdir(parents=True, exist_ok=True)
        cmds.append(["npm", "install", "--prefix", str(local_base), package])
        # Add local bin to PATH for verification
        local_bin = local_base / "bin"
        if str(local_bin) not in os.environ.get("PATH", ""):
            env["PATH"] = f"{local_bin}{os.pathsep}{env.get('PATH', '')}"

    elif method == "pip":
        pip_cmd = shutil.which("pip3") or shutil.which("pip") or "pip"
        cmd = [pip_cmd, "install"]
        if not global_install:
            cmd.append("--user")
        cmd.append(package)
        cmds.append(cmd)

    elif method == "apt":
        cmds.append(["apt-get", "update", "-qq"])
        cmds.append(["apt-get", "install", "-y", "-qq", package])

    elif method == "gem":
        cmd = ["gem", "install"]
        if not global_install:
            cmd.append("--user-install")
        cmd.append(package)
        cmds.append(cmd)

    elif method == "go":
        env["GOPATH"] = env.get("GOPATH", str(Path.home() / "go"))
        cmds.append(["go", "install", package])

    elif method == "cargo":
        cmds.append(["cargo", "install", package])

    else:
        return f"Error: Unsupported install method '{method}'. Supported: npm, pip, apt, gem, go, cargo"

    # Check if command exists
    exe = cmds[0][0]
    if not shutil.which(exe):
        return f"Error: '{exe}' command not found. Cannot install via {method}."

    # Run install commands
    outputs = []
    any_success = False
    for cmd in cmds:
        try:
            r = sp.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            stdout = r.stdout.strip()
            stderr = r.stderr.strip()
            if r.returncode == 0:
                outputs.append(f"✅ {' '.join(cmd[:3])}... succeeded")
                if stdout:
                    outputs.append(stdout[:500])
                any_success = True
                break  # Stop after first success
            else:
                outputs.append(f"❌ {' '.join(cmd[:3])}... failed (exit {r.returncode})")
                if stderr:
                    outputs.append(f"stderr: {stderr[:300]}")
                # Continue to next fallback
        except sp.TimeoutExpired:
            outputs.append(f"⚠️ {' '.join(cmd[:3])}... timed out")
        except Exception as e:
            outputs.append(f"⚠️ {' '.join(cmd[:3])}... error: {e}")

    if not any_success:
        return "\n".join(outputs)

    # Verify: extract binary name from package (e.g. "mmx-cli" -> "mmx")
    binary_name = package.split("/")[-1].split("@")[0].replace("-cli", "").replace("cli-", "")
    # Check PATH first
    if shutil.which(binary_name):
        outputs.append(f"✅ Verified: '{binary_name}' is now available in PATH")
    else:
        # Check npm local bin
        local_bin = Path.home() / "npm" / "node_modules" / ".bin"
        if (local_bin / binary_name).exists():
            outputs.append(f"✅ Verified: '{binary_name}' installed at {local_bin / binary_name}")
        elif (local_bin / package.split("/")[-1].split("@")[0]).exists():
            outputs.append(f"✅ Verified: '{package.split('/')[-1].split('@')[0]}' installed at {local_bin / package.split('/')[-1].split('@')[0]}")
        else:
            outputs.append(f"⚠️ '{binary_name}' may be installed but not in PATH. Try: export PATH=\"{local_bin}:\$PATH\"")

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
