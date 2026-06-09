"""BAW built-in: MiniMax vision (image understanding via mmx CLI)"""

import subprocess as sp
import os
import yaml
from pathlib import Path

# Default model
DEFAULT_MODEL = "MiniMax-M3"

def _load_config_model():
    """Load vision model from config.yaml capabilities section."""
    config_paths = [
        Path(__file__).parent / "config.yaml",
        Path.home / "baw" / "config.yaml",
    ]
    for p in config_paths:
        if p.exists():
            try:
                with open(p) as f:
                    cfg = yaml.safe_load(f)
                caps = cfg.get("capabilities", {})
                vision_cfg = caps.get("vision", {})
                model = vision_cfg.get("model")
                if model:
                    return model
            except Exception:
                pass
    return None


def vision_describe(path: str, question: str = "") -> str:
    """Describe an image using MiniMax vision via mmx CLI.

    Args:
        path: Path to the image file
        question: Optional question about the image

    Returns:
        Vision model: MiniMax-M3 (via config or fallback)
    """
    # Load model from config with fallback
    config_model = _load_config_model()
    model = config_model or os.environ.get("VISION_MODEL") or DEFAULT_MODEL
    
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