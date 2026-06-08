"""BAW — Text-to-Speech Module
MiniMax T2A API integration for Telegram voice output.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("baw.tts")

MINIMAX_T2A_URL = "https://api.minimax.io/v1/t2a_v2"
DEFAULT_VOICE = "male-tone-1"
DEFAULT_MODEL = "speech-2.8-hd"

# Map language_boost values for MiniMax TTS
LANG_BOOST = {
    "yue": "Chinese,Yue",
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
}


def minimax_tts(text: str, api_key: str, voice: str = DEFAULT_VOICE,
                model: str = DEFAULT_MODEL, language: str = "auto") -> Optional[bytes]:
    """Call MiniMax T2A API to convert text to speech.
    
    Returns raw audio bytes (MP3), or None on failure.
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "text": text[:10000],  # Max 10k chars
        "stream": False,
        "voice_setting": {
            "voice_id": voice,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "format": "mp3",
            "sample_rate": 32000,
            "channel": 1,
        },
        "output_format": "url",  # Get downloadable URL
    }

    if language != "auto":
        boost = LANG_BOOST.get(language, language)
        payload["language_boost"] = boost

    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(MINIMAX_T2A_URL, headers=headers, json=payload)
            if r.status_code != 200:
                logger.error(f"[TTS] API error {r.status_code}: {r.text[:200]}")
                return None

            data = r.json()
            audio_url = data.get("data", {}).get("audio", "")
            if not audio_url:
                logger.error(f"[TTS] No audio URL in response: {data}")
                return None

            # Download the audio file
            dl = client.get(audio_url, timeout=60)
            if dl.status_code != 200:
                logger.error(f"[TTS] Download failed: {dl.status_code}")
                return None

            logger.info(f"[TTS] Success: {len(dl.content)} bytes from {model}")
            return dl.content

    except Exception as e:
        logger.error(f"[TTS] Error: {e}")
        return None


def save_audio_bytes(data: bytes, name: str = "tts_output") -> str:
    """Save audio bytes to temp file and return path."""
    tmp_dir = Path.home() / ".baw" / "tts_cache"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"{name}.mp3"
    # Avoid collisions
    if path.exists():
        base = path.stem
        i = 1
        while path.exists():
            path = tmp_dir / f"{base}_{i}.mp3"
            i += 1
    path.write_bytes(data)
    return str(path)
