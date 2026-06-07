"""BAW built-in: bash shell execution"""

import subprocess
import shlex
from pathlib import Path


def bash(command: str, workdir: str | None = None, timeout: int = 60) -> str:
    """Execute a shell command. Cross-platform (Linux + macOS)."""
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
