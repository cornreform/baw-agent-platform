"""BAW built-in: image_generate — AI image generation (stub).

Requires an image generation API (DALL-E, Stable Diffusion, etc.).
Configure via capabilities.image_generation in config.yaml.
"""


def image_generate(prompt: str, aspect_ratio: str = "landscape") -> str:
    """Generate an image from a text prompt."""
    _BAW_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
    import sys
    if _BAW_ROOT not in sys.path:
        sys.path.insert(0, _BAW_ROOT)

    from pathlib import Path
    import yaml

    data_dir = Path.home() / ".baw"
    if (data_dir / "config.yaml").exists():
        cfg = yaml.safe_load((data_dir / "config.yaml").read_text("utf-8"))
        caps = cfg.get("capabilities", {}).get("image_generation", {})
        model = caps.get("model", "")
        if model:
            return (
                f"[image_generate] Image generation is configured to use '{model}', "
                f"but generation backend is not yet wired.\n"
                f"Prompt: {prompt[:200]}\n"
                f"Aspect ratio: {aspect_ratio}"
            )

    return (
        f"[image_generate] Not configured. Set up in ~/.baw/config.yaml:\n"
        f"  capabilities:\n"
        f"    image_generation:\n"
        f"      model: dall-e-3\n"
        f"  providers:\n"
        f"    openai:\n"
        f"      api_key_env: OPENAI_API_KEY\n"
        f"Prompt: {prompt[:200]}"
    )


TOOL_DEF = {
    "name": "image_generate",
    "description": (
        "Generate images from text prompts. "
        "Currently NOT configured — returns setup instructions."
    ),
    "handler": image_generate,
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text prompt describing the desired image",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["landscape", "square", "portrait"],
                "description": "Aspect ratio (default: landscape)",
                "default": "landscape",
            },
        },
        "required": ["prompt"],
    },
    "risk_level": "low",
}
