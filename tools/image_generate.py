"""BAW built-in: image_generate — AI image generation via OpenAI DALL-E.

Configure via capabilities.image_generation in config.yaml:
  capabilities:
    image_generation:
      model: dall-e-3
      provider: openai
"""

import os
import json
import httpx
from pathlib import Path
from datetime import datetime


def _resolve_size(aspect_ratio: str) -> str:
    """Map aspect ratio to DALL-E size string."""
    size_map = {
        "landscape": "1792x1024",
        "square": "1024x1024",
        "portrait": "1024x1792",
    }
    return size_map.get(aspect_ratio, "1024x1024")


def _load_image_gen_config(baw_home: Path) -> tuple:
    """Load image generation config from config.yaml.

    Returns (model, provider_name, api_key, api_key_env, base_url).
    """
    import yaml
    cfg_path = baw_home / "config.yaml"
    cfg = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    caps = cfg.get("capabilities", {}).get("image_generation", {})
    model = caps.get("model", "dall-e-3")
    provider_name = caps.get("provider", "openai")
    providers = cfg.get("providers", {})
    provider_cfg = providers.get(provider_name, {})
    api_key_env = provider_cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    base_url = provider_cfg.get("base_url", "https://api.openai.com/v1")
    return model, provider_name, api_key, api_key_env, base_url


def _resolve_api_key(baw_home: Path, api_key_env: str, api_key: str) -> str:
    """Try loading API key from .env as fallback."""
    if api_key:
        return api_key
    env_file = baw_home / ".env"
    if env_file.exists():
        for line in env_file.read_text().strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == api_key_env:
                    return v.strip()
    return api_key


def _download_and_save_image(client: httpx.Client, image_url: str,
                              baw_home: Path) -> str:
    """Download image and save to generated/ directory. Returns local path."""
    out_dir = baw_home / "generated"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"img_{ts}.png"
    img_r = client.get(image_url, timeout=30)
    out_path.write_bytes(img_r.content)
    return str(out_path)


def image_generate(prompt: str, aspect_ratio: str = "landscape") -> str:
    """Generate an image from a text prompt using OpenAI DALL-E."""
    baw_home = Path.home() / ".baw"
    size = _resolve_size(aspect_ratio)
    model, provider_name, api_key, api_key_env, base_url = _load_image_gen_config(baw_home)
    api_key = _resolve_api_key(baw_home, api_key_env, api_key)

    if not api_key:
        return (
            f"❌ Image generation: No API key for {provider_name}.\n"
            f"Set {api_key_env} in ~/.baw/.env"
        )

    try:
        client = httpx.Client(timeout=60)
        r = client.post(
            f"{base_url}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "prompt": prompt, "n": 1, "size": size},
        )
        if r.status_code == 200:
            data = r.json()
            image_url = data["data"][0].get("url", "")
            if image_url:
                local_path = _download_and_save_image(client, image_url, baw_home)
                return json.dumps({
                    "url": image_url,
                    "local_path": local_path,
                    "model": model,
                    "size": size,
                    "prompt": prompt[:200],
                }, ensure_ascii=False)
            return json.dumps({"error": "No image URL in response"})
        else:
            return json.dumps({"error": f"API error {r.status_code}: {r.text[:300]}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


TOOL_DEF = {
    "name": "image_generate",
    "description": (
        "Generate images from text prompts using DALL-E. "
        "Returns the URL and local path of the generated image."
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
