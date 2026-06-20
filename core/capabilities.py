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
            if m.get("id") == model_id:
                return {"id": m.get("id", model_id), "provider": pname,
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
            lines.append(f"  <b>{func}</b> → ❌ 未設定")
        elif r["type"] == "model":
            lines.append(f"  <b>{func}</b> → `{r['id']}` ({r['provider']})")
        else:
            lines.append(f"  <b>{func}</b> → method: `{r['method']}`")
    return "\n".join(lines)


def validate_capability_health(config: dict) -> list[dict]:
    """Detect and auto-heal capability config drift.

    Returns list of {capability, issue, fix_applied} dicts.
    Auto-heals: contradictory fields, missing env vars, method drift.
    <b>Code-enforced</b> — runs before EVERY LLM call in run_agent().
    """
    import os
    from pathlib import Path

    fixes = []
    caps = config.get("capabilities", {})

    # ── Drift patterns ──
    LOCAL_METHODS = {"faster-whisper", "whisper-local", "openai-whisper-local"}
    REMOTE_METHODS = {"auto-asr", "whisper-api", "openai-asr"}

    for cap_name, cap_cfg in caps.items():
        if not isinstance(cap_cfg, dict):
            continue

        method = cap_cfg.get("method", "")
        model_id = cap_cfg.get("model", "")
        base_url = cap_cfg.get("base_url", "")
        api_key_env = cap_cfg.get("api_key_env", "")

        # Pattern 1: Local method + remote base_url = drift
        if method in LOCAL_METHODS and base_url:
            cap_cfg.pop("base_url", None)
            cap_cfg.pop("api_key_env", None)
            fixes.append({
                "capability": cap_name,
                "issue": f"local method '{method}' with remote base_url '{base_url}'",
                "fix_applied": "removed base_url + api_key_env (local method doesn't need them)",
            })

        # Pattern 2: Remote method + no base_url — auto-fallback to local
        if method in REMOTE_METHODS and not base_url:
            local_fallback = _pick_local_fallback(cap_name)
            cap_cfg["method"] = local_fallback
            cap_cfg.pop("base_url", None)
            cap_cfg.pop("api_key_env", None)
            cap_cfg.pop("model", None)
            fixes.append({
                "capability": cap_name,
                "issue": f"remote method '{method}' without base_url — broken config",
                "fix_applied": f"auto-fallback to '{local_fallback}' (local, free, always works)",
            })

        # Pattern 3: api_key_env set but env var doesn't exist — auto-fallback to local
        if api_key_env and not _env_var_exists(api_key_env):
            local_fallback = _pick_local_fallback(cap_name)
            old_method = method or model_id or api_key_env
            cap_cfg["method"] = local_fallback
            cap_cfg.pop("base_url", None)
            cap_cfg.pop("api_key_env", None)
            cap_cfg.pop("model", None)
            fixes.append({
                "capability": cap_name,
                "issue": f"api_key_env '{api_key_env}' not found — '{old_method}' will fail",
                "fix_applied": f"auto-fallback to '{local_fallback}' (local, no API key needed)",
            })

        # Pattern 4: base_url + api_key_env both set but model ID doesn't match any provider
        if base_url and api_key_env and model_id:
            provider = _find_provider_for_base_url(config, base_url)
            if provider:
                model_exists = any(
                    m.get("id") == model_id
                    for m in config.get("providers", {}).get(provider, {}).get("models", [])
                )
                if not model_exists:
                    fixes.append({
                        "capability": cap_name,
                        "issue": f"model '{model_id}' not listed in provider '{provider}' models",
                        "fix_applied": "none — model may still work if provider accepts unknown model IDs",
                    })

    return fixes


def _pick_local_fallback(cap_name: str) -> str:
    """Pick the best local fallback method for a capability."""
    local_map = {
        "stt": "faster-whisper",
        "tts": "edge-tts",
    }
    return local_map.get(cap_name, "local")


def _find_provider_for_base_url(config: dict, base_url: str) -> Optional[str]:
    """Find which provider in config matches a given base_url."""
    for pname, pdata in config.get("providers", {}).items():
        if pdata.get("base_url", "").rstrip("/") == base_url.rstrip("/"):
            return pname
    return None


def _env_var_exists(env_var: str) -> bool:
    """Check if an env var exists in os.environ or ~/.baw/.env."""
    import os
    from pathlib import Path

    if os.environ.get(env_var):
        return True
    env_path = Path.home() / ".baw" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{env_var}=") and "=" in line:
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and val != "***":
                    return True
    return False
