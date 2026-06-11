"""BAW — Capability Router
Route functions (chat, stt, tts, vision, image_generation) to the right model/provider.
Also handles in-task model overrides via [key: value] tags in user prompts.
"""
from __future__ import annotations
import re
import copy
from typing import Optional


# ── Tag → capability name mapping ──
_TAG_MAP = {
    "model": "chat",
    "chat": "chat",
    "stt": "stt",
    "tts": "tts",
    "vision": "vision",
    "img": "image_generation",
    "image": "image_generation",
}


def parse_model_overrides(prompt: str) -> tuple[str, dict[str, str]]:
    """Extract [key: value] model override tags from a user prompt.

    Returns (cleaned_prompt, overrides_dict).
    cleaned_prompt has all tags removed.
    overrides_dict maps capability name → model_id, e.g. {"chat": "MiniMax-M3"}.

    Supported tags:
        [model: X]           → override capabilities.chat.model
        [chat: X]            → override capabilities.chat.model
        [stt: X]             → override capabilities.stt.model
        [tts: X]             → override capabilities.tts.model
        [vision: X]          → override capabilities.vision.model
        [img: X]             → override capabilities.image_generation.model
        [image: X]           → override capabilities.image_generation.model
    """
    overrides: dict[str, str] = {}
    pattern = re.compile(r"\[(\w+):\s*(\S+)\]")

    def _replace(m: re.Match) -> str:
        tag = m.group(1).lower()
        model_id = m.group(2)
        cap = _TAG_MAP.get(tag)
        if cap:
            overrides[cap] = model_id
        return ""  # Remove tag from prompt

    cleaned = pattern.sub(_replace, prompt).strip()
    # Collapse multiple spaces left by tag removal
    cleaned = re.sub(r"  +", " ", cleaned)
    return cleaned, overrides


def resolve_capability(
    config: dict, function: str, overrides: dict[str, str] | None = None
) -> Optional[dict]:
    """Find the model or method for a given function.

    In-task overrides take priority over config when provided.

    Returns dict with either:
      {'type': 'model', 'id': ..., 'provider': ..., 'base_url': ..., 'api_key_env': ...}
      {'type': 'method', 'method': 'faster-whisper', 'config': {...}}

    Resolution:
      0. overrides[function] → in-task model override (highest priority)
      1. capabilities.<func>.model → explicit model assignment
      2. capabilities.<func>.method → non-model method (e.g. faster-whisper)
      3. Scan all providers/models for <func> in their capabilities list
      4. For 'chat': model.default as fallback
    """
    # ── 0. In-task override ──
    if overrides and function in overrides:
        override_model = overrides[function]
        m = _find_model(config, override_model)
        if m:
            return {"type": "model", **m}

    caps = config.get("capabilities", {})
    func_cfg = caps.get(function, {})

    # 1. Explicit model
    model_id = func_cfg.get("model", "")
    if model_id:
        m = _find_model(config, model_id)
        if m:
            return {"type": "model", **m}

    # 2. Non-model method (faster-whisper, etc.)
    method = func_cfg.get("method", "")
    if method:
        return {"type": "method", "method": method, "config": func_cfg}

    # 3. Capability-tagged models
    for pname, pcfg in config.get("providers", {}).items():
        for m in pcfg.get("models", []):
            if function in m.get("capabilities", []):
                return {"type": "model", **m, "provider": pname,
                        "base_url": pcfg.get("base_url", ""),
                        "api_key_env": pcfg.get("api_key_env", "")}

    # 4. Chat fallback
    if function == "chat":
        default_id = config.get("model", {}).get("default", "")
        if default_id:
            m = _find_model(config, default_id)
            if m:
                return {"type": "model", **m}

    return None


def _find_model(config: dict, model_id: str) -> Optional[dict]:
    for pname, pcfg in config.get("providers", {}).items():
        for m in pcfg.get("models", []):
            if m["id"] == model_id:
                return {"id": m["id"], "provider": pname,
                        "base_url": pcfg.get("base_url", ""),
                        "api_key_env": pcfg.get("api_key_env", ""),
                        **m}

    # ── Auto-discovery: try to add the model if we can detect its provider ──
    from .model_discovery import auto_discover_model, auto_discover_all_models
    discovered = auto_discover_model(config, model_id)
    if discovered:
        # Also scan all providers for any additional models via /v1/models
        auto_discover_all_models(config)
        return discovered

    return None


def apply_overrides_to_config(config: dict, overrides: dict[str, str]) -> dict:
    """Deep-copy config and inject in-task model overrides into capabilities section.

    Returns a NEW config dict — original is untouched so overrides are per-task only.
    """
    import copy
    cfg = copy.deepcopy(config)
    caps = cfg.setdefault("capabilities", {})
    for func, model_id in overrides.items():
        # If func already has a method (e.g. stt: faster-whisper), pop it and set model
        caps[func] = caps.get(func, {})
        caps[func]["model"] = model_id
        caps[func].pop("method", None)  # Remove method so model takes priority
    return cfg


def capability_help(config: dict) -> str:
    """Return formatted routing table."""
    lines = ["**🔌 功能路由表**"]
    for func in ("chat", "stt", "tts"):
        r = resolve_capability(config, func)
        if r is None:
            lines.append(f"  **{func}** → ❌ 未設定")
        elif r["type"] == "model":
            lines.append(f"  **{func}** → `{r['id']}` ({r['provider']})")
        else:
            lines.append(f"  **{func}** → method: `{r['method']}`")
    return "\n".join(lines)
