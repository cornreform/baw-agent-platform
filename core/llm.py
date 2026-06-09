"""
BAW — LLM Provider Abstraction Layer
Multi-protocol support: OpenAI, Anthropic, Google, and custom.
Adding a new provider = add a handler function, no core changes.
"""

import os
import json
import time
from dataclasses import dataclass
from typing import Optional, Callable
from pathlib import Path
import httpx


@dataclass
class ModelDef:
    id: str
    provider: str
    base_url: str
    api_key: str
    protocol: str = "openai-chat"
    context_window: int = 4096
    vision: bool = False
    cost_per_1m_input: float = 0
    cost_per_1m_output: float = 0
    temperature: float = 0.7  # Default, overridden by model config
    custom_handler: Optional[str] = None
    model_kwargs: dict = None  # Extra body params (e.g. thinking, top_p)


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict]
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    protocol: str = "openai-chat"


def load_config(config_path: Optional[Path] = None) -> dict:
    """Load BAW config from yaml or dict, and auto-load .env for API keys."""
    import yaml
    
    # Auto-load .env (try Hermes profile first, then BAW's own)
    for env_path in [
        Path.home() / ".hermes" / "profiles" / "sticky" / ".env",
        Path.home() / ".baw" / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text().strip().split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    if key.strip() not in os.environ:
                        os.environ[key.strip()] = val.strip()
    
    if config_path and config_path.exists():
        return yaml.safe_load(config_path.read_text())
    for p in [
        Path.home() / ".baw" / "config.yaml",
        (Path.cwd() / "baw" / "config.yaml").resolve(),
    ]:
        if p.exists():
            return yaml.safe_load(p.read_text())
    raise FileNotFoundError("BAW config.yaml not found")


def get_model(config: dict, model_id: Optional[str] = None) -> ModelDef:
    """Resolve a model definition from config. Protocol-agnostic."""
    cfg = config.get("model", {})
    model_id = model_id or cfg.get("default", "deepseek-v4-flash")
    providers = config.get("providers", {})

    for provider_name, provider_cfg in providers.items():
        for m in provider_cfg.get("models", []):
            if m["id"] == model_id:
                return ModelDef(
                    id=m["id"],
                    provider=provider_name,
                    base_url=provider_cfg.get("base_url", ""),
                    api_key=os.environ.get(provider_cfg.get("api_key_env", ""), ""),
                    protocol=m.get("protocol", provider_cfg.get("protocol", "openai-chat")),
                    context_window=m.get("context_window", 4096),
                    vision=m.get("vision", False),
                    cost_per_1m_input=m.get("cost_per_1m_input", 0),
                    cost_per_1m_output=m.get("cost_per_1m_output", 0),
                    temperature=m.get("temperature", 0.7),
                    model_kwargs=m.get("model_kwargs"),
                    custom_handler=m.get("custom_handler"),
                )
    raise ValueError(f"Model '{model_id}' not found in config.\n"
                     f"Available: {[m['id'] for p in providers.values() for m in p.get('models', [])]}")


# ── Protocol Handlers ──────────────────────────────────────────

_HANDLERS: dict[str, Callable] = {}


def register_protocol(name: str, handler: Callable):
    """Register a new protocol handler. Call this to add support for any API format."""
    _HANDLERS[name] = handler


def call_llm(
    model: ModelDef,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """Call LLM via configured protocol. Falls back to openai-chat if protocol unknown."""
    handler = _HANDLERS.get(model.protocol)
    if handler:
        return handler(model, messages, tools, temperature, max_tokens)
    # Default: OpenAI chat completions
    return _call_openai_chat(model, messages, tools, temperature, max_tokens)


# ── Protocol: OpenAI Chat Completions ──────────────────────────

def _call_openai_chat(
    model: ModelDef,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """OpenAI-compatible /v1/chat/completions. Works with: DeepSeek, MiniMax, Groq, Together, etc."""
    headers = {
        "Authorization": f"Bearer {model.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model.id,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    if model.model_kwargs:
        body.update(model.model_kwargs)

    url = f"{model.base_url.rstrip('/')}/chat/completions"
    data = _post(url, headers, body)
    
    choice = data["choices"][0]
    msg = choice.get("message", {})
    usage = data.get("usage", {})

    # Handle models that return content in reasoning_content (e.g. Kimi thinking mode)
    content = msg.get("content", "") or ""
    if not content and msg.get("reasoning_content"):
        content = msg["reasoning_content"]

    return LLMResponse(
        content=content,
        tool_calls=msg.get("tool_calls", []),
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        model=data.get("model", model.id),
        provider=model.provider,
        protocol="openai-chat",
    )


# ── Protocol: Anthropic Messages ──────────────────────────────

def _call_anthropic(
    model: ModelDef,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """Anthropic Claude /v1/messages format."""
    headers = {
        "x-api-key": model.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    # Convert OpenAI-style messages to Anthropic format
    system = ""
    anthropic_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system += msg["content"] + "\n"
        elif msg["role"] == "user":
            anthropic_messages.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "assistant":
            anthropic_messages.append({"role": "assistant", "content": msg["content"]})
        elif msg["role"] == "tool":
            anthropic_messages.append({
                "role": "user",
                "content": f"[Tool result: {msg.get('content', '')}]"
            })

    body = {
        "model": model.id,
        "messages": anthropic_messages,
        "max_tokens": max_tokens or 4096,
        "temperature": temperature,
    }
    if system.strip():
        body["system"] = system.strip()

    url = f"{model.base_url.rstrip('/')}/messages"
    data = _post(url, headers, body)

    content = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")

    usage = data.get("usage", {})
    return LLMResponse(
        content=content,
        tool_calls=[],
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        model=model.id,
        provider=model.provider,
        protocol="anthropic",
    )


# ── Protocol: Google Generative AI ────────────────────────────

def _call_google(
    model: ModelDef,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """Google Gemini /v1beta/models/{model}:generateContent format."""
    headers = {
        "Content-Type": "application/json",
    }
    # Google uses API key as query param, not header
    api_key = model.api_key
    
    # Convert OpenAI messages to Google format
    google_contents = []
    for msg in messages:
        role = "user" if msg["role"] in ("user", "tool") else "model"
        google_contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}]
        })

    body = {
        "contents": google_contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens or 4096,
        },
    }

    url = f"{model.base_url.rstrip('/')}/models/{model.id}:generateContent?key={api_key}"
    data = _post(url, headers, body)

    content = ""
    candidates = data.get("candidates", [])
    if candidates:
        for part in candidates[0].get("content", {}).get("parts", []):
            if "text" in part:
                content += part["text"]

    usage = data.get("usageMetadata", {})
    return LLMResponse(
        content=content,
        tool_calls=[],
        input_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0),
        model=model.id,
        provider=model.provider,
        protocol="google",
    )


# ── HTTP Helper ────────────────────────────────────────────────

def _post(url: str, headers: dict, body: dict) -> dict:
    """HTTP POST with error handling. No provider-specific logic here."""
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"LLM API error: {e.response.status_code}\n"
            f"URL: {url}\n"
            f"Response: {e.response.text[:500]}"
        )
    except httpx.TimeoutException:
        raise RuntimeError(f"LLM API timeout: {url}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM API non-JSON response: {resp.text[:200] if 'resp' in dir() else str(e)}")


# ── Register built-in protocols ────────────────────────────────

register_protocol("openai-chat", _call_openai_chat)
register_protocol("anthropic", _call_anthropic)
register_protocol("google", _call_google)


# ── Cost helper ────────────────────────────────────────────────

def calculate_cost(model: ModelDef, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a call."""
    return (
        (input_tokens / 1_000_000) * model.cost_per_1m_input
        + (output_tokens / 1_000_000) * model.cost_per_1m_output
    )


# ── Auto-fallback ──────────────────────────────────────────────


def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate: ~4 chars per token for mixed CN/EN text."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += len(content) // 3  # conservative: 3 chars ≈ 1 token for CN
    return total


def _route_model(config: dict, messages: list[dict], primary_id: str) -> str:
    """Auto-route to long-context model if message size exceeds threshold."""
    route_cfg = config.get("model", {}).get("route", {})
    if not route_cfg.get("enabled"):
        return primary_id

    threshold = route_cfg.get("threshold_tokens", 8000)
    estimated = _estimate_tokens(messages)
    if estimated > threshold:
        long_model = route_cfg.get("long_model", "")
        if long_model:
            return long_model
    return route_cfg.get("short_model", primary_id)


@dataclass
class FallbackResult:
    response: LLMResponse
    model_used: str  # "primary" | "fallback"
    primary_model: str
    fallback_model: str | None = None
    error: str | None = None


def call_llm_with_fallback(
    config: dict,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    primary_id: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> FallbackResult:
    """Call LLM with auto-fallback + exponential backoff retry.

    Tries primary model up to 3 times with exponential backoff (1s→2s→4s)
    on transient errors (429, 503, timeout, connection). Non-transient
    errors (401, 403, 400) skip retry and go straight to fallback.

    Config:
      model.default  -> primary model ID
      model.fallback -> fallback model ID

    Returns FallbackResult with which model was used.
    """
    import time as _time

    model_cfg = config.get("model", {})
    primary_id = primary_id or model_cfg.get("default", "deepseek-v4-flash")
    fallback_id = model_cfg.get("fallback", "")

    # ── Auto-route based on message size ──
    primary_id = _route_model(config, messages, primary_id)

    RETRYABLE_STATUS = {429, 503, 502, 504}
    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # seconds: 1s → 2s → 4s

    # ── Try primary with exponential backoff ──
    error_msg = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            model = get_model(config, primary_id)
            fb_temp = model.temperature
            resp = _call_with_timeout(model, messages, tools, fb_temp, max_tokens)
            return FallbackResult(
                response=resp,
                model_used="primary",
                primary_model=primary_id,
            )
        except ValueError as e:
            # Model not found in config — skip retry, go straight to fallback
            error_msg = str(e)[:300]
            break
        except (httpx.HTTPStatusError) as e:
            status = e.response.status_code
            error_msg = str(e)[:300]
            if status in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)
                continue
            break  # non-retryable or exhausted
        except (httpx.TimeoutException, httpx.ConnectError,
                ConnectionError, TimeoutError) as e:
            error_msg = str(e)[:300]
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)
                continue
            break  # exhausted retries
        except RuntimeError as e:
            error_msg = str(e)[:300]
            # Check if it's a transient error (timeout/rate-limit message)
            if ("timeout" in error_msg.lower() or "rate" in error_msg.lower()
                    or "overloaded" in error_msg.lower()) and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)
                continue
            break  # non-retryable RuntimeError

    if not fallback_id:
        if error_msg:
            raise RuntimeError(error_msg)
        raise RuntimeError(f"Primary ({primary_id}) failed after {MAX_RETRIES + 1} attempts")

    # ── Fallback ──
    import logging
    _flog = logging.getLogger("baw.llm")
    _flog.warning(
        f"[LLM] Primary ({primary_id}) failed: {error_msg[:120]} → "
        f"falling back to {fallback_id}"
    )
    try:
        model = get_model(config, fallback_id)
        fb_temp = model.temperature
        resp = _call_with_timeout(model, messages, tools, fb_temp, max_tokens)
        return FallbackResult(
            response=resp,
            model_used="fallback",
            primary_model=primary_id,
            fallback_model=fallback_id,
            error=error_msg,
        )
    except Exception as e2:
        raise RuntimeError(
            f"Primary ({primary_id}) failed: {error_msg}\n"
            f"Fallback ({fallback_id}) failed: {e2}"
        )


def _call_with_timeout(
    model: ModelDef,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """Wrapper for call_llm with model-aware temperature."""
    temp = temperature if temperature is not None else model.temperature
    return call_llm(model, messages, tools, temp, max_tokens)
