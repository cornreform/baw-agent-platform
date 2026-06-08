"""BAW — Capability Router
Route functions (chat, stt, tts) to the right model/provider.
"""
from __future__ import annotations
from typing import Optional


def resolve_capability(config: dict, function: str) -> Optional[dict]:
    """Find the model or method for a given function.
    
    Returns dict with either:
      {'type': 'model', 'id': ..., 'provider': ..., 'base_url': ..., 'api_key_env': ...}
      {'type': 'method', 'method': 'faster-whisper', 'config': {...}}
    
    Resolution:
      1. capabilities.<func>.model → explicit model assignment
      2. capabilities.<func>.method → non-model method (e.g. faster-whisper)
      3. Scan all providers/models for <func> in their capabilities list
      4. For 'chat': model.default as fallback
    """
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
    return None


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
