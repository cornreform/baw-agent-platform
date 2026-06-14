"""BAW built-in: bash shell execution"""

import subprocess
import shlex
from pathlib import Path


SENSITIVE_PATHS = [
    "/etc/passwd", "/etc/shadow", "/etc/master.passwd",
    "/etc/hosts", "/etc/hostname", "/proc/",
    "/root/", "/home/*/.ssh/", "/home/*/.bash_history",
    "id_rsa", "id_ed25519", ".aws/credentials", ".env",
]

def _is_sensitive(cmd: str) -> tuple[bool, str]:
    """Check if command tries to read sensitive files or execute dangerous operations."""
    cmd_lower = cmd.lower().strip()

    # Dangerous command prefixes (destructive operations)
    DANGEROUS_PREFIXES = [
        "rm -rf", "rm -fr", "rm --recursive --force",
        "sudo ", "su -", "su root",
        "dd if=", "mkfs", "mkfs.ext", "mkfs.ntfs",
        "> /dev/sda", "> /dev/nvme", "> /dev/hd",
        "curl .*\|.*sh", "wget .*\|.*sh",  # pipe-to-shell
        "eval\b", "exec\b",
        "chmod 777 /", "chmod -R 777 /",
        "chown -R root", "chown -R 0:0",
        "systemctl stop", "systemctl disable",
        "kill -9 1", "kill -9 init",
        "reboot", "shutdown", "poweroff", "halt",
        "del /f /s /q", "rd /s /q",  # Windows destructive
    ]
    for dp in DANGEROUS_PREFIXES:
        if dp in cmd_lower:
            return True, f"❌ Blocked — dangerous command detected: '{dp.strip()}'"

    # Block cat/less/more/head/tail of sensitive files
    for sp in SENSITIVE_PATHS:
        if sp in cmd_lower:
            return True, f"❌ Blocked — command references sensitive path: {sp}"
    # Block any command that outputs password/shadow content
    if any(x in cmd_lower for x in ["passwd", "shadow", "master.passwd"]):
        if any(x in cmd_lower for x in ["cat ", "less ", "more ", "head ", "tail ", "grep ", "awk ", "cut ", "sort ", "xxd ", "strings ", "od ", "hexdump "]):
            return True, "❌ Blocked — reading system credential files is not allowed"
    return False, ""

def bash(command: str, workdir: str | None = None, timeout: int = 60) -> str:
    """Execute a shell command. Cross-platform (Linux + macOS)."""
    blocked, reason = _is_sensitive(command)
    if blocked:
        return reason
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir or str(Path.cwd()),
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[Timeout after {timeout}s]"
    except Exception as e:
        return f"[Error] {e}"


TOOL_DEF = {
    "name": "bash",
    "description": "Execute a shell command. Returns stdout, stderr, and exit code. Cross-platform.",
    "handler": bash,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute"
            },
            "workdir": {
                "type": "string",
                "description": "Working directory (optional, defaults to current)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 60)",
            },
        },
        "required": ["command"],
    },
    "risk_level": "medium",
}
