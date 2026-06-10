"""BAW built-in: MiniMax TTS (text-to-speech via MiniMax API)

Direct API wrapper — no curl, no sub-agent, no web_search needed.
Single call: tts(text, voice, output_path) → generates mp3 file.

Voice list reference (Cantonese female voices):
  female-shaonv — 少女 (young girl)
  female-shaofan — 少芬 (mature female)
  female-guangdong — 廣東女聲 (Cantonese female)
  female-tone-1 — 女聲1號
  female-tone-2 — 女聲2號
  female-cantonese-1 — 粵語女聲1
  female-cantonese-2 — 粵語女聲2

API endpoint: POST https://api.minimaxi.com/v1/t2a_v2
Docs: https://platform.minimax.io/docs/faq/system-voice-id
"""

import os
import json
import yaml
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_BASE = "https://api.minimaxi.com"
TTS_ENDPOINT = "/v1/t2a_v2"

# Known Cantonese-compatible female voices (from MiniMax official docs)
CANTONESE_FEMALE_VOICES = [
    "female-shaonv",
    "female-shaofan", 
    "female-guangdong",
    "female-tone-1",
    "female-tone-2",
    "female-cantonese-1",
    "female-cantonese-2",
    "Chinese (Mandarin)_Sweet_Lady",
    "Chinese (Mandarin)_Warm_Girl",
    "Chinese (Mandarin)_Soft_Girl",
    "Chinese (Mandarin)_Crisp_Girl",
    "Chinese (Mandarin)_IntellectualGirl",
    "Chinese (Mandarin)_Cute_Spirit",
    "Chinese (Mandarin)_BashfulGirl",
    "Chinese (Mandarin)_Mature_Woman",
]


def _load_api_key() -> str:
    """Load MiniMax API key from .env file."""
    env_paths = [
        Path.home() / ".baw" / ".env",
        Path.home() / ".baw" / "telegram.env",
    ]
    for p in env_paths:
        if p.exists():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k in ("MINIMAX_API_KEY", "MINIMAX_API_KEY_2"):
                            return v
            except Exception:
                pass
    return os.environ.get("MINIMAX_API_KEY", "")


def tts_generate(text: str, voice: str = "female-shaonv", output_path: str = "",
                 speed: float = 1.0, model: str = "speech-2.8-hd") -> str:
    """Generate TTS audio using MiniMax API.

    Args:
        text: Text to convert to speech (keep under 500 chars)
        voice: Voice ID (see CANTONESE_FEMALE_VOICES)
        output_path: Output mp3 path. Default: /tmp/baw_tts_<voice>.mp3
        speed: Speech speed (0.5-2.0)
        model: MiniMax TTS model ID

    Returns:
        Absolute path to generated mp3 file, or error string.
    """
    api_key = _load_api_key()
    if not api_key:
        return "ERROR: MINIMAX_API_KEY not found in ~/.baw/.env"

    if not output_path:
        safe_voice = voice.replace(" ", "_").replace("(", "").replace(")", "")
        output_path = f"/tmp/baw_tts_{safe_voice}.mp3"

    payload = {
        "model": model,
        "text": text[:500],
        "voice_setting": {
            "voice_id": voice,
            "speed": float(speed),
        },
        "audio_setting": {
            "format": "mp3",
            "sample_rate": 32000,
        },
    }

    url = f"{API_BASE}{TTS_ENDPOINT}"
    data = json.dumps(payload).encode("utf-8")

    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })

    try:
        resp = urlopen(req, timeout=30)
        audio_data = resp.read()
        if not audio_data:
            return f"ERROR: Empty response from MiniMax TTS API (voice={voice})"

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio_data)

        size_kb = len(audio_data) / 1024
        return f"OK {output_path} ({size_kb:.1f}KB, voice={voice})"

    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return f"ERROR: HTTP {e.code} from MiniMax TTS API: {body}"
    except URLError as e:
        return f"ERROR: Network error calling MiniMax TTS API: {e}"
    except Exception as e:
        return f"ERROR: TTS generation failed ({voice}): {e}"


def tts_list_voices() -> str:
    """List all known Cantonese-compatible female voices."""
    lines = ["Cantonese female voices available:", ""]
    for i, v in enumerate(CANTONESE_FEMALE_VOICES, 1):
        lines.append(f"  {i}. {v}")
    return "\n".join(lines)


TOOL_DEF = {
    "name": "tts",
    "description": (
        "Generate Cantonese text-to-speech audio using MiniMax TTS API. "
        "Use this to create audio samples for voice selection. "
        "Returns the path to the generated mp3 file. "
        "After generating, include MEDIA:/path/to/file.mp3 in your response to send it."
    ),
    "handler": tts_generate,
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Cantonese text to convert to speech (max 500 chars).",
            },
            "voice": {
                "type": "string",
                "description": "Voice ID. Use tts_list_voices to see available voices. Default: female-shaonv",
                "default": "female-shaonv",
            },
            "output_path": {
                "type": "string",
                "description": "Output mp3 file path. Default: /tmp/baw_tts_<voice>.mp3",
            },
            "speed": {
                "type": "number",
                "description": "Speech speed (0.5-2.0). Default: 1.0",
                "default": 1.0,
            },
            "model": {
                "type": "string",
                "description": "TTS model ID. Default: speech-2.8-hd",
                "default": "speech-2.8-hd",
            },
        },
        "required": ["text", "voice"],
    },
    "risk_level": "low",
}
