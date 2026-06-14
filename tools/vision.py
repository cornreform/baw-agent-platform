"""BAW built-in: Vision (image understanding) — MiniMax API + Stepfun fallback + OCR."""

import subprocess as sp
import os
import yaml
import base64
import json
import urllib.request
from pathlib import Path

# Default models
DEFAULT_MINIMAX_MODEL = "MiniMax-M3"
DEFAULT_STEPFUN_MODEL = "step-3.7-flash"


def _load_config():
    """Load vision config from config.yaml."""
    config_paths = [
        Path(__file__).parent / "config.yaml",
        Path.home() / "baw" / "config.yaml",
        Path.home() / ".baw" / "config.yaml",
    ]
    for p in config_paths:
        if p.exists():
            try:
                with open(p) as f:
                    return yaml.safe_load(f)
            except Exception:
                pass
    return {}


def _encode_image(path: str) -> str:
    """Encode image to base64 data URI."""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = Path(path).suffix.lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "webp": "image/webp"}.get(ext.lstrip("."), "image/jpeg")
    return f"data:{mime};base64,{data}"


def _vision_minimax(path: str, question: str = "") -> str:
    """Direct MiniMax vision API call (OpenAI-compatible)."""
    cfg = _load_config()
    providers = cfg.get("providers", {})
    mmx = providers.get("minimax", {})

    api_key = mmx.get("api_key") or os.environ.get("MINIMAX_API_KEY")
    base_url = mmx.get("base_url", "https://api.minimax.io/v1")
    vision_cfg = cfg.get("capabilities", {}).get("vision", {})
    model = vision_cfg.get("model") or DEFAULT_MINIMAX_MODEL

    if not api_key:
        return "Error: MiniMax API key not configured"

    url = f"{base_url.rstrip('/')}/chat/completions"
    user_question = question or "Describe this image in detail. What objects, text, or scenes do you see?"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful vision assistant. Describe images accurately and concisely."},
            {"role": "user", "content": [
                {"type": "text", "text": user_question},
                {"type": "image_url", "image_url": {"url": _encode_image(path)}},
            ]},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "(empty response)")
            return "(no response from vision model)"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return f"MiniMax vision error: HTTP {e.code} — {body[:200]}"
    except Exception as e:
        return f"MiniMax vision error: {e}"


def _vision_stepfun(path: str, question: str = "") -> str:
    """Use Stepfun multimodal API as fallback."""
    cfg = _load_config()
    providers = cfg.get("providers", {})
    step = providers.get("stepfun", {})

    api_key = step.get("api_key") or os.environ.get("STEPFUN_API_KEY") or os.environ.get("STEP_API_KEY")
    base_url = step.get("base_url", "https://api.stepfun.ai/v1")
    vision_cfg = cfg.get("capabilities", {}).get("vision", {})
    model = vision_cfg.get("model") or DEFAULT_STEPFUN_MODEL

    if not api_key:
        return "Error: Stepfun API key not configured"

    url = f"{base_url.rstrip('/')}/chat/completions"
    user_question = question or "Describe this image in detail. What objects, text, or scenes do you see?"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful vision assistant. Describe images accurately and concisely."},
            {"role": "user", "content": [
                {"type": "text", "text": user_question},
                {"type": "image_url", "image_url": {"url": _encode_image(path)}},
            ]},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "(empty response)")
            return "(no response from vision model)"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return f"Stepfun vision error: HTTP {e.code} — {body[:200]}"
    except Exception as e:
        return f"Stepfun vision error: {e}"


def _vision_mmx_cli(path: str, question: str = "") -> str:
    """Legacy mmx CLI call (if available)."""
    import shutil
    if not shutil.which("mmx"):
        return (
            "mmx CLI not installed. "
            "To install: use the 'install' tool with package='mmx-cli' and method='npm'. "
            "Example: install(package='mmx-cli', method='npm', global_install=True)"
        )

    cmd = ["mmx", "vision", "describe", path]
    if question:
        cmd.extend(["--question", question])

    try:
        r = sp.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return r.stdout.strip()
        return r.stderr.strip() or r.stdout.strip() or "(mmx returned nothing)"
    except sp.TimeoutExpired:
        return "mmx CLI timed out"
    except Exception as e:
        return f"mmx CLI error: {e}"


def vision_describe(path: str, question: str = "") -> str:
    """Describe an image using vision AI (MiniMax API primary, Stepfun fallback).

    Args:
        path: Path to the image file
        question: Optional question about the image

    Returns:
        Description from vision model
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {p}"

    # ── Primary: Direct MiniMax API (no CLI needed) ──
    mmx_result = _vision_minimax(str(p), question)
    if not mmx_result.startswith("Error:") and not mmx_result.startswith("MiniMax vision error:"):
        return f"[MiniMax {DEFAULT_MINIMAX_MODEL}]\n{mmx_result}"

    # ── Fallback 1: mmx CLI (if installed) ──
    cli_result = _vision_mmx_cli(str(p), question)
    if not cli_result.startswith("mmx") and not cli_result.startswith("Error"):
        return f"[MiniMax via mmx CLI]\n{cli_result}"

    # ── Fallback 2: Stepfun multimodal ──
    step_result = _vision_stepfun(str(p), question)
    if not step_result.startswith("Error:") and not step_result.startswith("Stepfun vision error:"):
        return f"[Stepfun {DEFAULT_STEPFUN_MODEL} (fallback)]\n{step_result}"

    # ── Final: combined error ──
    return (
        f"Vision analysis failed.\n"
        f"- MiniMax API: {mmx_result}\n"
        f"- mmx CLI: {cli_result}\n"
        f"- Stepfun: {step_result}"
    )


TOOL_DEF = {
    "name": "vision",
    "description": (
        "Analyze an image using vision AI (MiniMax primary, Stepfun fallback). "
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
