"""BAW built-in: text_to_speech — TTS audio generation (stub).

Requires a TTS API (MiniMax, OpenAI, Edge TTS, etc.).
Configure via capabilities.tts in config.yaml.
"""


def text_to_speech(text: str) -> str:
    """Convert text to speech audio."""
    _BAW_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
    import sys
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)

    from pathlib import Path
    import yaml

    data_dir = Path.home() / ".baw"
    if (data_dir / "config.yaml").exists():
        cfg = yaml.safe_load((data_dir / "config.yaml").read_text("utf-8"))
        caps = cfg.get("capabilities", {}).get("tts", {})
        model = caps.get("model", "")
        if model:
            return (
                f"[tts] TTS is configured to use '{model}', "
                f"but generation backend is not yet wired.\n"
                f"Text: {text[:200]}"
            )

    return (
        f"[tts] Not configured. Set up in ~/.baw/config.yaml:\n"
        f"  capabilities:\n"
        f"    tts:\n"
        f"      model: MiniMax-M3\n"
        f"Text: {text[:200]}"
    )


TOOL_DEF = {
    "name": "text_to_speech",
    "description": (
        "Convert text to speech audio. "
        "Currently NOT configured — returns setup instructions."
    ),
    "handler": text_to_speech,
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to convert to speech",
            },
        },
        "required": ["text"],
    },
    "risk_level": "low",
}
