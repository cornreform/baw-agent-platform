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
    # ── Major AI labs ──
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
        "model_patterns": [r"^gemini-", r"^google/", r"^gemma"],
    },
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^gpt-", r"^o[0-9]", r"^openai/", r"^dall-e", r"^whisper"],
    },
    "xai": {
        "env_var": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^grok-", r"^xai/"],
    },
    "mistral": {
        "env_var": "MISTRAL_API_KEY",
        "base_url": "https://api.mistral.ai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^mistral-", r"^codestral-", r"^pixtral", r"^ministral"],
    },
    "cohere": {
        "env_var": "COHERE_API_KEY",
        "base_url": "https://api.cohere.ai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^cohere/", r"^command-r", r"^c4ai-"],
    },

    # ── Chinese AI ──
    "deepseek": {
        "env_var": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^deepseek-", r"^deepseek/"],
    },
    "minimax": {
        "env_var": "MINIMAX_API_KEY",
        "base_url": "https://api.minimax.io/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^minimax-", r"^MiniMax-", r"^abab"],
    },
    "moonshot": {
        "env_var": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.ai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^kimi-", r"^moonshot-", r"^moonshot/"],
    },
    "stepfun": {
        "env_var": "STEPFUN_API_KEY",
        "base_url": "https://api.stepfun.ai/step_plan/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^step-", r"^stepfun"],
    },
    "zhipu": {
        "env_var": "ZHIPU_API_KEY",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "protocol": "openai-chat",
        "model_patterns": [r"^glm-", r"^zhipu", r"^chatglm", r"^cogview"],
    },
    "qwen": {
        "env_var": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^qwen-", r"^dashscope"],
    },
    "baichuan": {
        "env_var": "BAICHUAN_API_KEY",
        "base_url": "https://api.baichuan-ai.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^baichuan", r"^Baichuan"],
    },

    # ── Inference platforms ──
    "groq": {
        "env_var": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^groq/", r"^llama-", r"^mixtral-", r"^gemma"],
    },
    "together": {
        "env_var": "TOGETHER_API_KEY",
        "base_url": "https://api.together.xyz/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^together/", r"^meta-llama/", r"^mistralai/", r"^deepseek-ai/"],
    },
    "openrouter": {
        "env_var": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^openrouter/", r"^nousresearch/", r"^meta-llama/", r"^mistralai/"],
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
    "deepinfra": {
        "env_var": "DEEPINFRA_API_KEY",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "protocol": "openai-chat",
        "model_patterns": [r"^deepinfra/", r"^Phind/", r"^cognitivecomputations/"],
    },
    "fireworks": {
        "env_var": "FIREWORKS_API_KEY",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^fireworks/", r"^accounts/fireworks/"],
    },
    "replicate": {
        "env_var": "REPLICATE_API_TOKEN",
        "base_url": "https://api.replicate.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^replicate/", r"^meta/"],
    },
    "novita": {
        "env_var": "NOVITA_API_KEY",
        "base_url": "https://api.novita.ai/v3/openai",
        "protocol": "openai-chat",
        "model_patterns": [r"^novita/", r"^sao10k/"],
    },
    "sambanova": {
        "env_var": "SAMBANOVA_API_KEY",
        "base_url": "https://api.sambanova.ai/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^sambanova/", r"^Meta-Llama"],
    },
    "hyperbolic": {
        "env_var": "HYPERBOLIC_API_KEY",
        "base_url": "https://api.hyperbolic.xyz/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^hyperbolic/", r"^deepseek-ai/"],
    },
    "lambda": {
        "env_var": "LAMBDA_API_KEY",
        "base_url": "https://api.lambdalabs.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^lambda/", r"^hermes"],
    },

    # ── Cloud / Enterprise ──
    "azure": {
        "env_var": "AZURE_OPENAI_API_KEY",
        "base_url": "https://{resource}.openai.azure.com/openai/deployments/{deployment}",
        "protocol": "openai-chat",
        "model_patterns": [r"^azure/"],
    },
    "cloudflare": {
        "env_var": "CLOUDFLARE_API_TOKEN",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run",
        "protocol": "openai-chat",
        "model_patterns": [r"^@cf/", r"^cloudflare/"],
    },
    "voyage": {
        "env_var": "VOYAGE_API_KEY",
        "base_url": "https://api.voyageai.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^voyage-"],
    },
    "ai21": {
        "env_var": "AI21_API_KEY",
        "base_url": "https://api.ai21.com/studio/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^jamba", r"^ai21/"],
    },

    # ── Free / Community ──
    "agnes": {
        "env_var": "AGNES_API_KEY",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "protocol": "openai-chat",
        "model_patterns": [r"^agnes-", r"^agnes/"],
    },
}

# ── Model ID → provider hints (common models) ──
MODEL_HINTS: dict[str, str] = {
    # Anthropic
    "claude-sonnet-4-20250514": "anthropic",
    "claude-3-opus": "anthropic",
    "claude-3-5-sonnet": "anthropic",
    "claude-3-5-haiku": "anthropic",
    "claude-3-haiku": "anthropic",
    # Google
    "gemini-2.5-pro": "google",
    "gemini-2.5-flash": "google",
    "gemini-2.0-flash": "google",
    "gemini-1.5-pro": "google",
    # OpenAI
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "gpt-4-turbo": "openai",
    "o1": "openai",
    "o1-mini": "openai",
    "o3-mini": "openai",
    "o4-mini": "openai",
    # xAI
    "grok-3": "xai",
    "grok-2": "xai",
    # Mistral
    "mistral-large": "mistral",
    "mistral-medium": "mistral",
    "mistral-small": "mistral",
    "codestral": "mistral",
    "pixtral-large": "mistral",
    # Cohere
    "command-r-plus": "cohere",
    "command-r": "cohere",
    # DeepSeek
    "deepseek-v4-flash": "deepseek",
    "deepseek-v4-pro": "deepseek",
    "deepseek-reasoner": "deepseek",
    "deepseek-chat": "deepseek",
    # MiniMax
    "MiniMax-M3": "minimax",
    "MiniMax-M2.5": "minimax",
    "MiniMax-M1": "minimax",
    "image-01": "minimax",       # MiniMax official image-gen model
    "video-01": "minimax",       # MiniMax official video-gen model
    # Kimi/Moonshot
    "kimi-k2.6": "moonshot",
    "kimi-k2": "moonshot",
    # StepFun
    "step-2": "stepfun",
    "step-1.5v": "stepfun",
    "step-3.7-flash": "stepfun",
    # Groq
    "llama-3.3-70b": "groq",
    "llama-3.1-8b": "groq",
    "mixtral-8x7b": "groq",
    "gemma-2-9b": "groq",
    # Zhipu
    "glm-4": "zhipu",
    "glm-4v": "zhipu",
    "chatglm3": "zhipu",
    # Qwen
    "qwen-max": "qwen",
    "qwen-plus": "qwen",
    "qwen-turbo": "qwen",
    "qwen-vl-max": "qwen",
    # Agnes AI (free)
    "agnes-2.0-flash": "agnes",
    "agnes-image-2.0": "agnes",
    "agnes-image-2.1-flash": "agnes",
    "agnes-video-v2.0": "agnes",
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

    Priority:
      1. Already-configured providers (have API key in .env) — search their known model patterns
      2. Free/community alternatives (Agnes AI) — only if no configured provider matches
      3. Other known providers — only if API key exists

    Returns the model info dict if successful, None otherwise.
    """
    # Already exists?
    for pname, pcfg in config.get("providers", {}).items():
        for m in pcfg.get("models", []):
            if m["id"] == model_id:
                return {"id": m["id"], "provider": pname,
                        "base_url": pcfg.get("base_url", ""),
                        "api_key_env": pcfg.get("api_key_env", ""), **m}

    # Step 1: Find which providers are already configured (have API keys)
    configured = _get_configured_providers(config)

    # Step 2: Does this model match any configured provider's patterns?
    provider_name = guess_provider(model_id)
    if provider_name and provider_name in configured:
        pinfo = KNOWN_PROVIDERS.get(provider_name)
        if pinfo:
            return _add_model_to_provider(config, provider_name, model_id, pinfo, data_dir)

    # Step 3: If no configured provider matches, try free alternatives
    # Agnes AI — free tier for chat, image, video
    if hasattr(logger, "info"):
        logger.info(f"[Discover] No configured provider for '{model_id}' — checking free alternatives")
    if _find_env_key("AGNES_API_KEY") or True:  # Agnes is free, try anyway
        if "agnes" in model_id.lower():
            return _add_model_to_provider(config, "agnes", model_id,
                                          KNOWN_PROVIDERS.get("agnes", {}), data_dir)

    # Step 4: Try other known providers (only if API key exists)
    if provider_name:
        pinfo = KNOWN_PROVIDERS.get(provider_name)
        if pinfo:
            env_var = pinfo["env_var"]
            if _find_env_key(env_var):
                return _add_model_to_provider(config, provider_name, model_id, pinfo, data_dir)
            else:
                logger.info(f"[Discover] Provider '{provider_name}' detected for '{model_id}' but {env_var} not found")

    return None


def _guess_capabilities(model_id: str) -> list[str]:
    """Guess capabilities from model ID patterns.

    Used by auto_discover_all_models to tag models discovered via /v1/models.
    """
    mid = model_id.lower()
    caps = ["chat"]

    # STT / ASR
    if any(kw in mid for kw in ("asr", "whisper", "stt", "audio-input", "transcri", "speech-to-text", "audio_input")):
        caps.append("stt")
    # TTS / Voice
    if any(kw in mid for kw in ("tts", "voice", "speech", "audio-output", "sound", "speaker", "text-to-speech", "audio_output")):
        caps.append("tts")
    # Vision
    if any(kw in mid for kw in ("vision", "vl", "vlm", "multimodal", "image-input", "image_input")):
        caps.append("vision")
    # Image generation
    if any(kw in mid for kw in ("dall-e", "cogview", "image-gen", "image-generation", "imagegen", "draw")):
        caps.append("image_generation")
    # Video generation
    if any(kw in mid for kw in ("video-gen", "video-generation", "videogen", "cogvideo")):
        caps.append("video_generation")
    # Embedding
    if any(kw in mid for kw in ("embed", "embedding")):
        caps.append("embedding")

    return caps


def auto_discover_all_models(config: dict) -> int:
    """Query /v1/models for every configured provider and add any missing models.
    
    Returns number of new models added.
    """
    import urllib.request
    import json
    
    added = 0
    for pname, pcfg in config.get("providers", {}).items():
        base_url = pcfg.get("base_url", "").rstrip("/")
        env_var = pcfg.get("api_key_env", "")
        api_key = ""
        if env_var:
            api_key = os.environ.get(env_var, "")
            if not api_key:
                env_path = Path.home() / ".baw" / ".env"
                if env_path.exists():
                    for line in env_path.read_text().splitlines():
                        if line.startswith(f"{env_var}=") and "=" in line:
                            api_key = line.split("=", 1)[1].strip().strip("\"'")
                            break
        if not base_url or not api_key:
            continue
        
        urls_to_try = [f"{base_url}/models"]
        # If base_url has extra path segments (e.g. step_plan/v1),
        # also try parent paths by stripping segments from the LEFT.
        # e.g. step_plan/v1 → v1/models may have more models than step_plan/v1/models
        from urllib.parse import urlparse
        _parsed = urlparse(base_url)
        _path_parts = _parsed.path.strip("/").split("/")
        if len(_path_parts) > 1:
            # Strip segments from the left to find broader models endpoints
            for _strip_left in range(1, len(_path_parts)):
                _remaining = _path_parts[_strip_left:]
                _parent_path = "/".join(_remaining)
                _candidate = f"{_parsed.scheme}://{_parsed.netloc}/{_parent_path}/models"
                if _candidate not in urls_to_try:
                    urls_to_try.append(_candidate)

        all_raw = []
        for models_url in urls_to_try:
            try:
                req = urllib.request.Request(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                raw = data.get("data", [])
                if raw and isinstance(raw, list):
                    all_raw.extend(raw)
            except Exception:
                pass  # Try next URL

        # Deduplicate by model id
        seen_ids = set()
        deduped_raw = []
        for m in all_raw:
            mid = m.get("id", "")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                deduped_raw.append(m)

        existing_ids = {m["id"] for p in config.get("providers", {}).values()
                       for m in p.get("models", [])}
        for m in deduped_raw:
            mid = m.get("id", "")
            if mid and mid not in existing_ids:
                config.setdefault("providers", {}).setdefault(pname, {}).setdefault("models", []).append({
                    "id": mid,
                    "capabilities": _guess_capabilities(mid),
                    "context_window": _guess_context_window(mid),
                })
                added += 1
        if added:
                _save_config(config, None)
                logger.info(f"[Discover] Auto-added {added} new models from {pname} /v1/models")
    return added


def _get_configured_providers(config: dict) -> set[str]:
    """Return set of provider names that have API keys configured in .env."""
    configured = set()
    for pname, pinfo in config.get("providers", {}).items():
        env_var = pinfo.get("api_key_env", "")
        if env_var and _find_env_key(env_var):
            configured.add(pname)
    return configured


def _add_model_to_provider(config: dict, provider_name: str, model_id: str,
                            pinfo: dict, data_dir: Path | None) -> dict:
    """Add a model to a provider and save config. Returns model info dict."""
    providers = config.setdefault("providers", {})
    if provider_name in providers:
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
        providers[provider_name] = {
            "api_key_env": pinfo.get("env_var", ""),
            "base_url": pinfo.get("base_url", ""),
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

    return {
        "id": model_id,
        "provider": provider_name,
        "base_url": pinfo.get("base_url", ""),
        "api_key_env": pinfo.get("env_var", ""),
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
    if "agnes" in model_lower:
        return 131_072
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
