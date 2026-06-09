"""BAW built-in: MiniMax vision (image understanding via mmx CLI)"""

import subprocess as sp
from pathlib import Path


def vision_describe(path: str, question: str = "") -> str:
    """Describe an image using MiniMax vision via mmx CLI.

    Args:
        path: Path to the image file
        question: Optional question about the image
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {p}"

    cmd = ["mmx", "vision", "describe", str(p)]
    if question:
        cmd.extend(["--question", question])

    try:
        r = sp.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return r.stdout.strip()
        return f"Vision error: {r.stderr.strip() or r.stdout.strip()}"
    except sp.TimeoutExpired:
        return "Vision timeout (>60s)"
    except FileNotFoundError:
        return "Error: mmx CLI not found. Install with: npm i -g @minimax/mcp"


TOOL_DEF = {
    "name": "vision",
    "description": (
        "Analyze an image using MiniMax vision. "
        "Use this for: identifying objects, reading text in images, "
        "describing scenes, finding products, or answering questions about images. "
        "ALWAYS prefer this over OCR for image understanding."
    ),
    "handler": vision_describe,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the image file",
            },
            "question": {
                "type": "string",
                "description": "Optional question about the image (e.g. 'What product is this? Where can I buy it?')",
            },
        },
        "required": ["path"],
    },
    "risk_level": "low",
}
