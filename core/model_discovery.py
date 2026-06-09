"""BAW — Model Auto-Discovery

When the user mentions a model not in config.yaml, BAW auto-detects
the provider from known patterns + checks if the API key exists in .env.
If found, auto-adds the provider + model to config.yaml.
"""
from __future__ import annotations
import os
import re
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("baw.discovery")

# ── Known Provider Database ──
# Maps provider name → {env_var, base_url, protocol, known_models}
KNOWN_PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com/v1",
        "protocol": "anthropic",
        "model_patterns": [r"^claude-", r"^anthropic/"],
    },
    "google": {
        "env_var": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "protocol": "google",
        "model_patterns": [r"^gemini-", r"^google/"],
    },
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^gpt-", r"^o[0-9]", r"^openai/", r"^dall-e"],
    },
    "xai": {
        "env_var": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^grok-", r"^xai/"],
    },
    "groq": {
        "env_var": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^llama-", r"^mixtral-", r"^groq/", r"^gemma"],
    },
    "together": {
        "env_var": "TOGETHER_API_KEY",
        "base_url": "https://api.together.xyz/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^together/", r"^meta-llama/", r"^mistralai/"],
    },
    "openrouter": {
        "env_var": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^openrouter/", r"^nousresearch/"],
    },
    "cerebras": {
        "env_var": "CEREBRAS_API_KEY",
        "base_url": "https://api.cerebras.ai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^cerebras/", r"^llama3"],
    },
    "perplexity": {
        "env_var": "PERPLEXITY_API_KEY",
        "base_url": "https://api.perplexity.ai",
        "protocol": "openai-chat",
        "model_patterns": [r"^perplexity/", r"^pplx-", r"^sonar"],
    },
}

# ── Model ID → provider hints (common models) ──
MODEL_HINTS: dict[str, str] = {
    "claude-sonnet-4-20250514": "anthropic",
    "claude-3-opus": "anthropic",
    "claude-3-5-sonnet": "anthropic",
    "claude-3-5-haiku": "anthropic",
    "gemini-2.5-pro": "google",
    "gemini-2.5-flash": "google",
    "gemini-2.0-flash": "google",
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "gpt-4-turbo": "openai",
    "o1": "openai",
    "o1-mini": "openai",
    "o3-mini": "openai",
    "grok-3": "xai",
    "grok-2": "xai",
    "llama-3.3-70b": "groq",
    "llama-3.1-8b": "groq",
    "mixtral-8x7b": "groq",
    "gemma-2-9b": "groq",
}


def _find_env_key(env_var: str) -> bool:
    """Check if an API key exists in .env or environment."""
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


def guess_provider(model_id: str) -> Optional[str]:
    """Guess which provider a model belongs to.

    Returns provider name (e.g. 'anthropic') or None.
    """
    # 1. Exact hint match
    if model_id in MODEL_HINTS:
        return MODEL_HINTS[model_id]

    # 2. Pattern match against known providers
    for pname, pinfo in KNOWN_PROVIDERS.items():
        for pattern in pinfo.get("model_patterns", []):
            if re.search(pattern, model_id, re.IGNORECASE):
                return pname

    # 3. Prefix-based: provider/model-id format
    if "/" in model_id:
        prefix = model_id.split("/")[0].lower()
        if prefix in KNOWN_PROVIDERS:
            return prefix

    return None


def auto_discover_model(config: dict, model_id: str, data_dir: Path | None = None) -> Optional[dict]:
    """Try to auto-discover and add a model to config.

    If the model isn't in config but we can detect its provider AND
    the API key exists, auto-add the provider + model to config.yaml.

    Returns the model info dict if successful, None otherwise.
    """
    # Already exists?
    for pname, pcfg in config.get("providers", {}).items():
        for m in pcfg.get("models", []):
            if m["id"] == model_id:
                return {"id": m["id"], "provider": pname,
                        "base_url": pcfg.get("base_url", ""),
                        "api_key_env": pcfg.get("api_key_env", ""), **m}

    # Guess provider
    provider_name = guess_provider(model_id)
    if not provider_name:
        return None

    pinfo = KNOWN_PROVIDERS.get(provider_name)
    if not pinfo:
        return None

    # Check API key exists
    env_var = pinfo["env_var"]
    if not _find_env_key(env_var):
        logger.info(f"[Discover] Provider '{provider_name}' detected for '{model_id}' but {env_var} not found")
        return None

    # Does this provider already exist in config?
    providers = config.setdefault("providers", {})
    if provider_name in providers:
        # Provider exists — just add the model
        existing_models = providers[provider_name].setdefault("models", [])
        existing_ids = {m["id"] for m in existing_models}
        if model_id not in existing_ids:
            existing_models.append({
                "id": model_id,
                "capabilities": ["chat"],
                "context_window": _guess_context_window(model_id),
            })
            _save_config(config, data_dir)
            logger.info(f"[Discover] Auto-added model '{model_id}' to existing provider '{provider_name}'")
    else:
        # New provider — add full config
        providers[provider_name] = {
            "api_key_env": env_var,
            "base_url": pinfo["base_url"],
            "models": [{
                "id": model_id,
                "capabilities": ["chat"],
                "context_window": _guess_context_window(model_id),
            }],
        }
        if pinfo.get("protocol") and pinfo["protocol"] != "openai-chat":
            providers[provider_name]["protocol"] = pinfo["protocol"]
        _save_config(config, data_dir)
        logger.info(f"[Discover] Auto-added provider '{provider_name}' + model '{model_id}'")

    # Return model info
    return {
        "id": model_id,
        "provider": provider_name,
        "base_url": pinfo["base_url"],
        "api_key_env": env_var,
        "capabilities": ["chat"],
        "context_window": _guess_context_window(model_id),
    }


def _guess_context_window(model_id: str) -> int:
    """Guess context window from model name."""
    model_lower = model_id.lower()
    if "1m" in model_lower or "million" in model_lower:
        return 1_000_000
    if "128k" in model_lower:
        return 128_000
    if "32k" in model_lower:
        return 32_000
    # Known defaults
    if "claude-3" in model_lower or "claude-sonnet-4" in model_lower:
        return 200_000
    if "gemini-2" in model_lower or "gemini-1.5" in model_lower:
        return 1_048_576
    if "gpt-4" in model_lower or "o1" in model_lower or "o3" in model_lower:
        return 128_000
    if "grok" in model_lower:
        return 131_072
    if "llama-3" in model_lower:
        return 128_000
    return 131_072  # safe default


def _save_config(config: dict, data_dir: Path | None = None):
    """Save config back to disk."""
    import yaml
    data_dir = data_dir or Path.home() / ".baw"
    config_path = data_dir / "config.yaml"
    try:
        config_path.write_text(
            yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[Discover] Failed to save config: {e}")
