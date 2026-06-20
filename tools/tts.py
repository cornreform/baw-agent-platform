"""BAW built-in: Multi-Provider TTS (MiniMax / Stepfun / Edge TTS)

Auto-detects configured TTS provider from config.yaml:
  - capabilities.tts.model = "MiniMax-M3" → MiniMax API
  - capabilities.tts.model = "stepaudio-2.5-tts" → Stepfun API  
  - No config / edge-tts found → Edge TTS (local, free)

Usage:
  tts(text, voice, output_path) → generates mp3 file
  tts_list_voices() → list available voices for current provider
"""
from __future__ import annotations
import os, json, subprocess, shutil
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# ── Config loader ──

def _load_config() -> dict:
    paths = [Path.home() / ".baw" / "config.yaml",
             Path("/home/baw/.baw/config.yaml")]
    for p in paths:
        if p.exists():
            try:
                import yaml
                return yaml.safe_load(p.read_text()) or {}
            except Exception:
                pass
    return {}


def _get_env(key: str) -> str:
    paths = [Path.home() / ".baw" / ".env",
             Path("/home/baw/.baw/.env"),
             Path.home() / ".baw" / "telegram.env"]
    for p in paths:
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        if k.strip() == key:
                            return v.strip().strip('"').strip("'")
            except Exception:
                pass
    return os.environ.get(key, "")


# ── Provider detection ──

def _detect_provider() -> str:
    """Return 'minimax', 'stepfun', 'edge', or 'auto'.

    Priority: MiniMax (more Cantonese voices) → Stepfun → Edge (free fallback).
    """
    cfg = _load_config()
    tts_cfg = cfg.get("capabilities", {}).get("tts", {})
    model = tts_cfg.get("model", "")
    method = tts_cfg.get("method", "")
    # Explicit override via method field
    if method in ("minimax", "stepfun", "edge"):
        return method
    # MiniMax first (more Cantonese female voices: Cantonese_GentleLady, etc.)
    if _get_env("MINIMAX_API_KEY"):
        return "minimax"
    # Stepfun second (has lively-girl, gentle-woman, cute-girl)
    if _get_env("STEPFUN_API_KEY") and ("stepaudio" in model or "step-tts" in model):
        return "stepfun"
    # Edge as last resort (local, free, 2 Cantonese voices)
    return "edge"


# ── Voices per provider ──

MINIMAX_VOICES = {
    "female": ["Cantonese_GentleLady", "Cantonese_CuteGirl", "Cantonese_KindWoman",
               "Arrogant_Miss", "English_ConfidentWoman", "English_CalmWoman",
               "English_Soft-spokenGirl"],
}
STEPFUN_VOICES = {
    "female": ["lively-girl", "gentle-woman", "cute-girl"],
    "male": ["male-tone-1", "serious-man"],
}
EDGE_CANTONESE_VOICES = {
    "female": ["zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural"],
    "male": ["zh-HK-WanLungNeural"],
}


def tts_list_voices(provider: str = "") -> str:
    """List voices for the current/default provider."""
    if not provider:
        provider = _detect_provider()
    labels = {"minimax": "MiniMax TTS", "stepfun": "Stepfun TTS", "edge": "Edge TTS"}
    lines = [f"{labels.get(provider, provider)} voices:", ""]
    voice_map = {"minimax": MINIMAX_VOICES, "stepfun": STEPFUN_VOICES, "edge": EDGE_CANTONESE_VOICES}
    voices = voice_map.get(provider, {})
    for gender, vlist in voices.items():
        lines.append(f"  {gender.title()}:")
        for v in vlist:
            lines.append(f"    - {v}")
    return "\n".join(lines)


# ── Provider-specific generators ──

def _tts_minimax(text: str, voice: str, output_path: str, speed: float = 1.0) -> str:
    """MiniMax TTS — reads base_url from config.yaml so international endpoints work."""
    api_key = _get_env("MINIMAX_API_KEY")
    if not api_key:
        return "ERROR: MINIMAX_API_KEY not found"
    # Read base_url from config (respects user's region choice)
    _cfg = _load_config()
    _mmx = _cfg.get("providers", {}).get("minimax", {})
    _base = _mmx.get("base_url", "https://api.minimax.io/v1")
    _tts_url = _base.rstrip("/") + "/t2a_v2"
    model = "speech-2.8-hd"
    payload = {
        "model": model,
        "text": text[:500],
        "voice_setting": {"voice_id": voice, "speed": speed, "vol": 1.0, "pitch": 0},
        "audio_setting": {"format": "mp3", "sample_rate": 32000},
        "output_format": "url",
    }
    req = Request(
        _tts_url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        resp = urlopen(req, timeout=30)
        body = json.loads(resp.read().decode())
        audio_url = body.get("data", {}).get("audio", "")
        if not audio_url:
            return f"ERROR: MiniMax: {json.dumps(body, ensure_ascii=False)[:200]}"
        dl = urlopen(audio_url, timeout=30)
        audio = dl.read()
        if not audio:
            return "ERROR: MiniMax empty audio download"
        Path(output_path).write_bytes(audio)
        return f"OK {output_path} ({len(audio)/1024:.1f}KB, MiniMax {model} / {voice})"
    except HTTPError as e:
        return f"ERROR: MiniMax HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
    except Exception as e:
        return f"ERROR: MiniMax: {e}"


def _tts_stepfun(text: str, voice: str, output_path: str, speed: float = 1.0) -> str:
    """Stepfun TTS via OpenAI-compatible /v1/audio/speech (step_plan for monthly)"""
    api_key = _get_env("STEPFUN_API_KEY")
    if not api_key:
        return "ERROR: STEPFUN_API_KEY not found"
    cfg = _load_config()
    tts_cfg = cfg.get("capabilities", {}).get("tts", {})
    config_section = tts_cfg.get("config", {})
    # Read from stepfun provider config (respects user's base_url choice)
    _sf = _cfg.get("providers", {}).get("stepfun", {})
    base_url = config_section.get("base_url") or _sf.get("base_url", "https://api.stepfun.ai/v1")
    model = tts_cfg.get("model", "stepaudio-2.5-tts")
    url = f"{base_url.rstrip('/')}/audio/speech"
    payload = {"model": model, "input": text[:1000], "voice": voice, "response_format": "mp3"}
    payload_json = json.dumps(payload).encode()
    req = Request(
        url, data=payload_json,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        resp = urlopen(req, timeout=30)
        audio = resp.read()
        if not audio or len(audio) < 100:
            text_resp = audio.decode("utf-8", errors="replace") if audio else "empty"
            return f"ERROR: Stepfun empty/small response ({len(audio)}b): {text_resp[:200]}"
        Path(output_path).write_bytes(audio)
        return f"OK {output_path} ({len(audio)/1024:.1f}KB, Stepfun {model} / {voice})"
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        if "voice" in body.lower() or "id" in body.lower():
            return f"ERROR: Stepfun voice '{voice}' invalid. Use: lively-girl, gentle-woman, cute-girl"
        return f"ERROR: Stepfun HTTP {e.code}: {body}"
    except Exception as e:
        return f"ERROR: Stepfun: {e}"


def _tts_edge(text: str, voice: str, output_path: str, speed: float = 1.0) -> str:
    """Edge TTS (local, free) — requires pip install edge-tts"""
    if not shutil.which("edge-tts"):
        # Try pip install
        r = subprocess.run(
            ["pip3", "install", "edge-tts", "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return f"ERROR: edge-tts not installed. pip install edge-tts"
    rate = f"+{int((speed-1)*100)}%" if speed >= 1 else f"-{int((1-speed)*100)}%"
    r = subprocess.run(
        ["edge-tts", "--voice", voice, "--text", text[:1000],
         "--rate", rate, "--write-media", output_path],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return f"ERROR: Edge TTS: {r.stderr[:200]}"
    if Path(output_path).stat().st_size < 100:
        return "ERROR: Edge TTS generated empty/small file"
    sz = Path(output_path).stat().st_size
    return f"OK {output_path} ({sz/1024:.1f}KB, Edge TTS {voice})"


# ── Main entry point ──

def tts_generate(text: str, voice: str = "", output_path: str = "",
                 speed: float = 1.0, provider: str = "") -> str:
    """Generate TTS audio using the configured or detected provider.

    Args:
        text: Text to convert to speech
        voice: Voice ID. Override to use a specific voice.
        output_path: Output mp3 path. Default: /tmp/baw_tts_<voice>.mp3
        speed: Speech speed (0.5-2.0)
        provider: Override provider. Auto-detect if empty.

    Returns:
        Status string with file path and size, or error message.
    """
    if not text:
        return "ERROR: No text provided"
    if not provider:
        provider = _detect_provider()
    if not voice:
        # Pick default female voice for provider
        voice_map = {"minimax": "Cantonese_GentleLady", "stepfun": "lively-girl", "edge": "zh-HK-HiuGaaiNeural"}
        voice = voice_map.get(provider, "Cantonese_GentleLady")
    if not output_path:
        safe_voice = voice.replace(" ", "_").replace("(", "").replace(")", "").replace("-", "_")
        # Default to a path inside BAW's persistent dir so the file
        # is visible to the host (Docker volume mount) and survives
        # container restarts. Falls back to /tmp if .baw/media doesn't exist.
        from pathlib import Path as _TtsPath
        _shared_dir = _TtsPath("/home/baw/.baw/media/tts")
        try:
            _shared_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(_shared_dir / f"baw_tts_{provider}_{safe_voice}.mp3")
        except Exception:
            output_path = f"/tmp/baw_tts_{provider}_{safe_voice}.mp3"
    gen = {"minimax": _tts_minimax, "stepfun": _tts_stepfun, "edge": _tts_edge}
    fn = gen.get(provider)
    if not fn:
        return f"ERROR: Unknown provider '{provider}'. Use: minimax, stepfun, or edge"
    result = fn(text, voice, output_path, speed)
    return result


# ── Tool definition ──

TOOL_DEF = {
    "name": "tts",
    "description": (
        "🔊 </b>DIRECTLY GENERATES AUDIO MP3 FILES</b> from text via TTS. "
        "When the user asks for TTS, voice, audio, or 'read out loud', "
        "you MUST call this tool — do NOT say you cannot generate audio. "
        "Supports Cantonese voices. Auto-detects provider (MiniMax / Stepfun / Edge). "
        "After calling, the tool returns an MP3 file path. "
        "Include MEDIA:/path/to/file.mp3 in your final response to send the audio to the user."
    ),
    "handler": tts_generate,
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Cantonese text to convert to speech (max 500 chars)."},
            "voice": {"type": "string", "description": "Voice ID. Leave empty for default. Use tts_list_voices() to list."},
            "output_path": {"type": "string", "description": "Output mp3 path. Default: /tmp/baw_tts_<provider>_<voice>.mp3 — DO NOT invent other paths. Use the exact path the tool returns."},
            "speed": {"type": "number", "description": "Speech speed (0.5-2.0)", "default": 1.0},
            "provider": {"type": "string", "description": "Override provider: minimax, stepfun, or edge. Auto-detected if empty."},
        },
        "required": ["text"],
    },
    "risk_level": "low",
}
