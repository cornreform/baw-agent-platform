"""BAW built-in: MiniMax CLI (mmx) — image, video, speech, music, vision, search.

Wraps the official `mmx` CLI: https://github.com/MiniMax-AI/cli
Requires: npm install -g mmx-cli && mmx auth login --api-key <key>
All commands return JSON output for structured parsing.
"""

import json
import os
import subprocess
import shutil
from pathlib import Path


def _run_mmx(args: list[str], timeout: int = 120) -> str:
    """Run mmx command with --output json and return parsed result."""
    cmd = ["mmx"] + args + ["--output", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            output = r.stdout.strip()
            if output:
                # Pretty-print JSON for better readability
                try:
                    parsed = json.loads(output)
                    return json.dumps(parsed, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    return output
            return "(empty response)"
        else:
            stderr = r.stderr.strip()[:500]
            return f"❌ mmx error (exit {r.returncode}): {stderr}"
    except subprocess.TimeoutExpired:
        return f"❌ mmx timed out after {timeout}s"
    except FileNotFoundError:
        return "❌ mmx CLI not found. Run: npm install -g mmx-cli"
    except Exception as e:
        return f"❌ mmx error: {e}"


def mmx_text(messages: str, system: str = "", model: str = "") -> str:
    """Chat with MiniMax model via CLI.

    Args:
        messages: Messages separated by newlines, or JSON array.
        system: Optional system prompt.
        model: Model name (default: MiniMax-M2.7-highspeed).

    Returns:
        Model response text.
    """
    args = ["text", "chat"]
    if system:
        args += ["--system", system]
    if model:
        args += ["--model", model]

    # Try JSON array first, then plain text
    try:
        msgs = json.loads(messages)
        if isinstance(msgs, list):
            args += ["--messages-file", "-"]
            input_data = json.dumps(msgs)
            r = subprocess.run(
                ["mmx"] + args + ["--output", "json"],
                capture_output=True, text=True, timeout=120,
                input=input_data,
            )
            if r.returncode == 0:
                try:
                    parsed = json.loads(r.stdout)
                    return json.dumps(parsed, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    return r.stdout[:2000]
            return f"❌ mmx error: {r.stderr[:300]}"
    except (json.JSONDecodeError, TypeError):
        pass

    # Plain text messages
    for line in messages.strip().split("\n"):
        line = line.strip()
        if line:
            args += ["--message", line]
    return _run_mmx(args)


def mmx_image(prompt: str, n: int = 1, aspect_ratio: str = "1:1",
              out_dir: str = "") -> str:
    """Generate an image via MiniMax.

    Args:
        prompt: Image description.
        n: Number of images (1-4).
        aspect_ratio: e.g. '1:1', '16:9', '9:16', '4:3', '3:4'.
        out_dir: Output directory (default: current).

    Returns:
        JSON with image URLs/paths.
    """
    args = ["image", "generate", "--prompt", prompt,
            "--n", str(n), "--aspect-ratio", aspect_ratio, "--output", "json"]
    if out_dir:
        args += ["--out-dir", out_dir]

    try:
        r = subprocess.run(["mmx"] + args, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            output = r.stdout.strip()
            try:
                parsed = json.loads(output)
                return json.dumps(parsed, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                # Fall back: check for URL in output
                if output:
                    return output[:2000]
                return f"✅ Image generated (check {out_dir or 'current dir'})"
        else:
            return f"❌ mmx image error: {r.stderr[:500]}"
    except subprocess.TimeoutExpired:
        return "❌ mmx image timed out (120s)"
    except Exception as e:
        return f"❌ mmx image error: {e}"


def mmx_speech(text: str, voice: str = "", speed: float = 1.0,
               out_path: str = "") -> str:
    """Generate speech via MiniMax TTS.

    Args:
        text: Text to synthesize.
        voice: Voice name (default: auto-detect Cantonese).
        speed: Speed multiplier (0.5-2.0).
        out_path: Output file path.

    Returns:
        Path to generated audio file.
    """
    args = ["speech", "synthesize", "--text", text]
    if voice:
        args += ["--voice", voice]
    if speed != 1.0:
        args += ["--speed", str(speed)]

    if out_path:
        args += ["--out", out_path]
    else:
        # Auto-generate a filename
        out_path = f"/tmp/mmx_tts_{abs(hash(text)) % 10000}.mp3"
        args += ["--out", out_path]

    try:
        r = subprocess.run(["mmx"] + args, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            if Path(out_path).exists():
                return f"✅ Speech generated\nMEDIA:{out_path}"
            return f"✅ Speech generated (check {out_path})"
        else:
            return f"❌ mmx TTS error: {r.stderr[:500]}"
    except Exception as e:
        return f"❌ mmx TTS error: {e}"


def mmx_video(prompt: str, download: bool = True, out_path: str = "") -> str:
    """Generate a video via MiniMax.

    Args:
        prompt: Video description.
        download: If True, download the video (may take time).
        out_path: Output file path.

    Returns:
        JSON with video task info, or path to downloaded file.
    """
    args = ["video", "generate", "--prompt", prompt]
    if download:
        if out_path:
            args += ["--download", out_path]
        else:
            out_path = f"/tmp/mmx_video_{abs(hash(prompt)) % 10000}.mp4"
            args += ["--download", out_path]
    else:
        args += ["--async"]

    try:
        r = subprocess.run(
            ["mmx"] + args, capture_output=True, text=True, timeout=300  # 5 min for video
        )
        if r.returncode == 0:
            output = r.stdout.strip()
            if download and Path(out_path).exists():
                return f"✅ Video generated\nMEDIA:{out_path}"
            try:
                parsed = json.loads(output)
                return json.dumps(parsed, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                return output[:2000]
        else:
            return f"❌ mmx video error: {r.stderr[:500]}"
    except subprocess.TimeoutExpired:
        return "❌ mmx video timed out (300s). Try with download=False for async mode."
    except Exception as e:
        return f"❌ mmx video error: {e}"


def mmx_music(prompt: str, lyrics: str = "", instrumental: bool = False,
              lyrics_optimizer: bool = False, out_path: str = "") -> str:
    """Generate music via MiniMax.

    Args:
        prompt: Music description (e.g. 'Upbeat pop', 'Cinematic orchestral').
        lyrics: Optional lyrics text.
        instrumental: If True, no vocals.
        lyrics_optimizer: If True, auto-generate lyrics from prompt.
        out_path: Output file path.

    Returns:
        Path to generated audio file.
    """
    args = ["music", "generate", "--prompt", prompt]
    if lyrics:
        args += ["--lyrics", lyrics]
    if instrumental:
        args += ["--instrumental"]
    if lyrics_optimizer:
        args += ["--lyrics-optimizer"]

    if not out_path:
        out_path = f"/tmp/mmx_music_{abs(hash(prompt)) % 10000}.mp3"
    args += ["--out", out_path]

    try:
        r = subprocess.run(["mmx"] + args, capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and Path(out_path).exists():
            return f"✅ Music generated\nMEDIA:{out_path}"
        else:
            return f"❌ mmx music error: {r.stderr[:500]}"
    except Exception as e:
        return f"❌ mmx music error: {e}"


def mmx_vision(image_path: str, prompt: str = "") -> str:
    """Analyze an image via MiniMax Vision.

    Args:
        image_path: Path or URL to image.
        prompt: Optional question about the image.

    Returns:
        Description/analysis text.
    """
    args = ["vision", "describe", "--image", image_path]
    if prompt:
        args += ["--prompt", prompt]

    return _run_mmx(args, timeout=60)


def mmx_search(query: str) -> str:
    """Search the web via MiniMax Search.

    Args:
        query: Search query.

    Returns:
        Search results.
    """
    return _run_mmx(["search", "query", "--q", query], timeout=30)


def mmx_voices() -> str:
    """List available TTS voices."""
    try:
        r = subprocess.run(
            ["mmx", "speech", "voices", "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            try:
                parsed = json.loads(r.stdout)
                return json.dumps(parsed, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                return r.stdout.strip()[:2000]
        return f"❌ mmx voices error: {r.stderr[:300]}"
    except Exception as e:
        return f"❌ mmx voices error: {e}"


def mmx_quota() -> str:
    """Check current token quota."""
    return _run_mmx(["quota"], timeout=15)


# ── Dispatcher ──

def _dispatcher(command: str, prompt: str = "", text: str = "",
                voice: str = "", speed: float = 1.0,
                image_path: str = "", query: str = "",
                n: int = 1, aspect_ratio: str = "1:1",
                lyrics: str = "", instrumental: bool = False,
                lyrics_optimizer: bool = False,
                out_path: str = "", download: bool = True,
                messages: str = "", system: str = "",
                model: str = "") -> str:
    """Dispatch mmx commands."""
    handlers = {
        "text": lambda: mmx_text(messages=messages, system=system, model=model),
        "image": lambda: mmx_image(prompt=prompt, n=n, aspect_ratio=aspect_ratio, out_dir=out_path),  # noqa: E501
        "speech": lambda: mmx_speech(text=text, voice=voice, speed=speed, out_path=out_path),
        "video": lambda: mmx_video(prompt=prompt, download=download, out_path=out_path),
        "music": lambda: mmx_music(prompt=prompt, lyrics=lyrics, instrumental=instrumental,
                                    lyrics_optimizer=lyrics_optimizer, out_path=out_path),
        "vision": lambda: mmx_vision(image_path=image_path, prompt=prompt),
        "search": lambda: mmx_search(query=query),
        "voices": lambda: mmx_voices(),
        "quota": lambda: mmx_quota(),
    }

    fn = handlers.get(command)
    if fn is None:
        avail = ", ".join(handlers.keys())
        return f"Error: unknown command '{command}'. Available: {avail}"
    return fn()


TOOL_DEF = {
    "name": "mmx",
    "description": (
        "MiniMax CLI — generate images, video, speech, music, "
        "vision analysis, web search. "
        "Commands: text, image, speech, video, music, vision, search, "
        "voices, quota. "
        "All return JSON output. "
        "Image/video/music files are delivered via MEDIA: tag. "
        "Requires: npm install -g mmx-cli + mmx auth login."
    ),
    "handler": _dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["text", "image", "speech", "video",
                         "music", "vision", "search", "voices", "quota"],
                "description": "What to generate.",
            },
            "prompt": {
                "type": "string",
                "description": "Required for: image, video, music, vision. "
                               "Description of what to generate.",
            },
            "text": {
                "type": "string",
                "description": "For 'speech': text to synthesize.",
            },
            "voice": {
                "type": "string",
                "description": "For 'speech': voice name (use 'voices' to list). "
                               "Default: auto (Cantonese on MiniMax).",
            },
            "speed": {
                "type": "number",
                "description": "For 'speech': speed 0.5-2.0 (default: 1.0).",
                "default": 1.0,
            },
            "image_path": {
                "type": "string",
                "description": "For 'vision': path or URL to image.",
            },
            "query": {
                "type": "string",
                "description": "For 'search': web search query.",
            },
            "n": {
                "type": "integer",
                "description": "For 'image': number of images (1-4).",
                "default": 1,
            },
            "aspect_ratio": {
                "type": "string",
                "description": "For 'image': 1:1, 16:9, 9:16, 4:3, 3:4.",
                "default": "1:1",
            },
            "lyrics": {
                "type": "string",
                "description": "For 'music': optional lyrics text.",
            },
            "instrumental": {
                "type": "boolean",
                "description": "For 'music': instrumental only (no vocals).",
                "default": False,
            },
            "lyrics_optimizer": {
                "type": "boolean",
                "description": "For 'music': auto-generate lyrics.",
                "default": False,
            },
            "out_path": {
                "type": "string",
                "description": "Output file path (auto-generated if empty).",
            },
            "download": {
                "type": "boolean",
                "description": "For 'video': download video (True) or async (False).",
                "default": True,
            },
            "messages": {
                "type": "string",
                "description": "For 'text': messages, one per line or JSON array.",
            },
            "system": {
                "type": "string",
                "description": "For 'text': system prompt.",
            },
            "model": {
                "type": "string",
                "description": "For 'text': model name.",
            },
        },
        "required": ["command"],
    },
    "risk_level": "low",
}
