"""BAW built-in: execute_code — Python code execution sandbox (stub).

Runs Python code with access to BAW tools (web_search, read_file, etc.).
Requires a sandboxed environment for safety — currently returns a stub.
"""

import sys
from pathlib import Path


def execute_code(code: str) -> str:
    """Execute Python code in a sandbox.

    WARNING: This is a stub. Full sandboxed execution not yet implemented.
    Use bash + python3 for now.
    """
    _BAW_ROOT = str(Path(__file__).resolve().parent.parent)

    # Quick safety check — refuse obviously dangerous patterns
    dangerous = [
        "import os", "import subprocess", "import shutil",
        "__import__", "eval(", "exec(", "compile(",
        "open(", "write(", "delete", "remove",
    ]
    code_lower = code.lower()
    for d in dangerous:
        if d in code_lower:
            return (
                f"[execute_code] Blocked: code contains potentially unsafe pattern '{d}'.\n"
                f"Use the 'bash' tool for system commands instead."
            )

    return (
        f"[execute_code] Sandboxed Python execution not yet configured.\n"
        f"Code received ({len(code)} chars):\n"
        f"```python\n{code[:500]}\n```\n"
        f"Use 'bash' tool with 'python3 -c \"...\"' as a workaround."
    )


TOOL_DEF = {
    "name": "execute_code",
    "description": (
        "Execute Python code in a sandbox with access to BAW tools. "
        "Currently NOT configured — use bash + python3 as workaround. "
        "Use this for multi-step logic that needs tool calls with processing between them."
    ),
    "handler": execute_code,
    "parameters": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
        },
        "required": ["code"],
    },
    "risk_level": "high",
}
