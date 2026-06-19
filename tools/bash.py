"""BAW built-in: bash shell execution"""

import subprocess
import shlex
import os
from pathlib import Path


SENSITIVE_PATHS = [
    "/etc/passwd", "/etc/shadow", "/etc/master.passwd", "/etc/group",
    "/etc/hosts", "/etc/hostname", "/etc/resolv.conf",
    "/proc/", "/sys/", "/dev/mem", "/dev/kmem",
    "/root/", "/home/*/.ssh/", "/home/*/.bash_history",
    "/home/*/.aws/", "/home/*/.gcloud/", "/home/*/.config/gh/",
    "id_rsa", "id_ed25519", "id_ecdsa",
    ".aws/credentials", ".env", ".git-credentials",
    "/var/run/docker.sock", "/run/docker.sock",
    "/var/log/auth.log", "/var/log/secure", "/var/log/syslog",
    "/boot/", "/lib/modules/", "/usr/src/",
    "/etc/ssl/private/", "/etc/apt/sources.list",
]

import re as _bash_re

def _extract_paths(cmd: str) -> list[str]:
    """Extract file paths from a shell command."""
    paths = []
    # Match quoted paths
    paths.extend(_bash_re.findall(r"""["']([/\w.-]+)["']""", cmd))
    # Match unquoted absolute paths
    paths.extend(_bash_re.findall(r'(/(?:[\w.-]+/)*[\w.-]+)', cmd))
    # Match ~/ paths
    paths.extend(_bash_re.findall(r'(~/(?:[\w.-]+/)*[\w.-]+)', cmd))
    # Deduplicate
    return list(set(paths))

def _matches_glob(pattern: str, path: str) -> bool:
    """Simple glob matching for sensitive path patterns."""
    import fnmatch
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern + '/*')

def _is_sensitive(cmd: str) -> tuple[bool, str]:
    """Check if command tries to read sensitive files or execute dangerous operations."""
    cmd_lower = cmd.lower().strip()
    cmd_original = cmd.strip()

    # Dangerous command prefixes (destructive operations)
    DANGEROUS_PREFIXES = [
        "rm -rf", "rm -fr", "rm --recursive --force",
        "sudo ", "su -", "su root",
        "dd if=", "mkfs", "mkfs.ext", "mkfs.ntfs",
        "> /dev/sda", "> /dev/nvme", "> /dev/hd",
        "curl .*\\|.*sh", "wget .*\\|.*sh",  # pipe-to-shell
        "eval\\b", "exec\\b",
        "chmod 777 /", "chmod -R 777 /",
        "chown -R root", "chown -R 0:0",
        "systemctl stop", "systemctl disable",
        "kill -9 1", "kill -9 init",
        "reboot", "shutdown", "poweroff", "halt",
        "del /f /s /q", "rd /s /q",  # Windows destructive
        ":(){ :|:& };:",  # fork bomb
    ]
    for dp in DANGEROUS_PREFIXES:
        if dp in cmd_lower:
            return True, f"[FAIL] Blocked — dangerous command detected: '{dp.strip()}'"

    # Block cat/less/more/head/tail of sensitive files
    for sp in SENSITIVE_PATHS:
        if sp in cmd_lower:
            return True, f"[FAIL] Blocked — command references sensitive path: {sp}"

    # Path-based glob matching (handles wildcards in SENSITIVE_PATHS)
    _paths = _extract_paths(cmd_original)
    for sp in SENSITIVE_PATHS:
        if '*' in sp or '?' in sp:
            for p in _paths:
                if _matches_glob(sp, p):
                    return True, f"[FAIL] Blocked — command references sensitive path: {p} (matches {sp})"

    # Block any command that outputs password/shadow content
    if any(x in cmd_lower for x in ["passwd", "shadow", "master.passwd"]):
        if any(x in cmd_lower for x in ["cat ", "less ", "more ", "head ", "tail ", "grep ", "awk ", "cut ", "sort ", "xxd ", "strings ", "od ", "hexdump "]):
            return True, "[FAIL] Blocked — reading system credential files is not allowed"
    return False, ""

def bash(command: str, workdir: str | None = None, timeout: int = 60) -> str:
    """Execute a shell command. Cross-platform (Linux + macOS)."""
    blocked, reason = _is_sensitive(command)
    if blocked:
        return reason
    try:
        # Sanitize env: strip API keys and secrets from subprocess
        _clean_env = {
            k: v for k, v in os.environ.items()
            if not k.endswith("_API_KEY")
            and not k.endswith("_SECRET")
            and not k.endswith("_TOKEN")
            and not k == "token"
        }
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir or str(Path.cwd()),
            env=_clean_env,
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
