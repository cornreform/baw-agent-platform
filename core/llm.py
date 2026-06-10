"""
BAW — LLM Provider Abstraction Layer
Multi-protocol support: OpenAI, Anthropic, Google, and custom.
Adding a new provider = add a handler function, no core changes.
"""

import os
import json
import time
import signal
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable
from pathlib import Path
import httpx

# ── Shared httpx client (connection pooling — grok-4.1 A3) ──
_HTTPX_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()

def _get_http_client() -> httpx.Client:
    """Lazy-init a shared httpx.Client with connection pooling."""
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is None:
        with _CLIENT_LOCK:
            if _HTTPX_CLIENT is None:  # double-check
                _HTTPX_CLIENT = httpx.Client(
                    timeout=120,
                    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
                )
    return _HTTPX_CLIENT

# ── Circuit breaker (grok-4.1 A2) ──
_CIRCUIT_STATE: dict[str, dict] = {}  # provider -> {failures, last_fail, open_until}
_CIRCUIT_LOCK = threading.Lock()
CIRCUIT_THRESHOLD = 5        # consecutive failures to open
CIRCUIT_COOLDOWN_SEC = 30    # pause before retry

def _check_circuit(provider: str) -> None:
    """Raise RuntimeError if circuit is open for this provider."""
    with _CIRCUIT_LOCK:
        state = _CIRCUIT_STATE.get(provider)
        if state and state.get("open_until", 0) > time.time():
            remain = int(state["open_until"] - time.time())
            raise RuntimeError(
                f"Circuit OPEN for {provider} - {state['failures']} consecutive failures, "
                f"retry in {remain}s"
            )

def _record_circuit_failure(provider: str) -> None:
    """Record a failure. Opens circuit if threshold reached."""
    with _CIRCUIT_LOCK:
        state = _CIRCUIT_STATE.setdefault(provider, {"failures": 0, "last_fail": 0, "open_until": 0})
        state["failures"] += 1
        state["last_fail"] = time.time()
        if state["failures"] >= CIRCUIT_THRESHOLD:
            state["open_until"] = time.time() + CIRCUIT_COOLDOWN_SEC

def _record_circuit_success(provider: str) -> None:
    """Reset circuit on success."""
    with _CIRCUIT_LOCK:
        _CIRCUIT_STATE.pop(provider, None)

def get_circuit_stats() -> dict:
    """Return circuit breaker stats for monitoring."""
    with _CIRCUIT_LOCK:
        return {p: dict(s) for p, s in _CIRCUIT_STATE.items()}

# ── Fallback tracker (deepseek-v4-pro C5) ──
_FALLBACK_LOG: list[dict] = []
_FALLBACK_LOCK = threading.Lock()
MAX_FALLBACK_LOG = 100

def _record_fallback(primary: str, fallback: str, reason: str) -> None:
    with _FALLBACK_LOCK:
        _FALLBACK_LOG.append({
            "ts": time.time(),
            "primary": primary,
            "fallback": fallback,
            "reason": reason[:200],
        })
        if len(_FALLBACK_LOG) > MAX_FALLBACK_LOG:
            _FALLBACK_LOG.pop(0)

def get_fallback_stats() -> list[dict]:
    with _FALLBACK_LOCK:
        return list(_FALLBACK_LOG)

# ── Graceful shutdown handler (deepseek-v4-pro C2) ──
_shutdown_requested = False

def _on_shutdown(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True

def is_shutdown_requested() -> bool:
    return _shutdown_requested

signal.signal(signal.SIGTERM, _on_shutdown)
signal.signal(signal.SIGINT, _on_shutdown)


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
    reasoning_content: str | None = None


def load_config(config_path: Optional[Path] = None) -> dict:
    """Load BAW config from yaml or dict, and auto-load .env for API keys."""
    import yaml

    # Auto-load .env (try Hermes profile first, then BAW's own)
    for env_path in [
        Path.home() / ".baw" / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if v:
                        os.environ.setdefault(k.strip(), v)

    if config_path and (p := Path(config_path)).exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    baw_config = Path.home() / ".baw" / "config.yaml"
    if baw_config.exists():
        return yaml.safe_load(baw_config.read_text(encoding="utf-8")) or {}
    return {}


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
    reasoning = msg.get("reasoning_content")
    if not content and reasoning:
        content = reasoning
        reasoning = None

    return LLMResponse(
        content=content,
        tool_calls=msg.get("tool_calls", []),
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        model=data.get("model", model.id),
        provider=model.provider,
        protocol="openai-chat",
        reasoning_content=reasoning,
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
        "system": system.strip(),
        "messages": anthropic_messages,
        "max_tokens": max_tokens or 4096,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools

    url = f"{model.base_url.rstrip('/')}/v1/messages"
    data = _post(url, headers, body)

    content_blocks = data.get("content", [{}])
    text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")

    return LLMResponse(
        content=text,
        tool_calls=[],
        input_tokens=data.get("usage", {}).get("input_tokens", 0),
        output_tokens=data.get("usage", {}).get("output_tokens", 0),
        model=model.id,
        provider=model.provider,
        protocol="anthropic",
    )


# ── Protocol: Google Gemini ───────────────────────────────────

def _call_google(
    model: ModelDef,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """Google Gemini /v1beta/models/*:generateContent."""
    headers = {"Content-Type": "application/json"}
    url = (
        f"{model.base_url.rstrip('/')}/v1beta/models/{model.id}:generateContent"
        f"?key={model.api_key}"
    )

    contents = []
    for msg in messages:
        role = "user" if msg["role"] in ("user", "tool") else "model"
        entry = {
            "role": role,
            "parts": [{"text": msg["content"]}],
        }
        contents.append(entry)

    body = {
        "contents": contents,
        "generationConfig": {"temperature": temperature},
    }

    data = _post(url, headers, body)

    candidates = data.get("candidates", [{}])
    parts = candidates[0].get("content", {}).get("parts", [{}])
    text = "".join(p.get("text", "") for p in parts)

    return LLMResponse(
        content=text,
        tool_calls=[],
        input_tokens=data.get("usageMetadata", {}).get("promptTokenCount", 0),
        output_tokens=data.get("usageMetadata", {}).get("candidatesTokenCount", 0),
        model=model.id,
        provider=model.provider,
        protocol="google",
    )


# ── HTTP Helper with pooling (grok-4.1 A3) ────────────────────

def _post(url: str, headers: dict, body: dict) -> dict:
    """HTTP POST with error handling + circuit breaker integration."""
    if _shutdown_requested:
        raise RuntimeError("Shutdown in progress - request aborted")

    client = _get_http_client()
    try:
        resp = client.post(url, headers=headers, json=body)
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
        raise RuntimeError(f"LLM API non-JSON response: {e}")


# ── Register built-in protocols ────────────────────────────────

register_protocol("openai-chat", _call_openai_chat)
register_protocol("anthropic", _call_anthropic)
register_protocol("google", _call_google)


# ── Call with fallback (with circuit breaker) ──────────────────

@dataclass
class FallbackResult:
    response: LLMResponse
    model_used: str  # "primary" or "fallback"
    primary_model: str


def call_llm_with_fallback(
    config: dict,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    primary_id: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> FallbackResult:
    """Call LLM with auto-fallback + exponential backoff retry + circuit breaker.

    Tries primary model up to 3 times with exponential backoff (1s→2s→4s)
    on transient errors (429, 503, timeout, connection). Non-transient
    errors (401, 403, 400) skip retry and go straight to fallback.

    Circuit breaker: if N consecutive failures, pauses all requests
    for 30s to prevent burning quota on a dead provider.

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

    # ── Circuit breaker check ──
    try:
        primary_model = get_model(config, primary_id)
        _check_circuit(primary_model.provider)
        _check_circuit(primary_id)  # also check model-level circuit
    except RuntimeError as ce:
        # Circuit is open — skip straight to fallback
        if fallback_id:
            try:
                fallback_model = get_model(config, fallback_id)
                _check_circuit(fallback_model.provider)
            except RuntimeError:
                raise RuntimeError(f"All circuits open. Primary ({primary_id}) + fallback unavailable.")
            _record_fallback(primary_id, fallback_id, f"Circuit open: {ce}")
            fb = call_llm_with_fallback(config, messages, tools, fallback_id, temperature, max_tokens)
            return FallbackResult(response=fb.response, model_used="fallback", primary_model=primary_id)
        raise

    RETRYABLE_STATUS = {429, 503, 502, 504}
    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # seconds: 1s → 2s → 4s

    # ── Try primary with exponential backoff ──
    error_msg = ""
    last_provider = ""
    for attempt in range(MAX_RETRIES + 1):
        if _shutdown_requested:
            raise RuntimeError("Shutdown in progress")
        try:
            model = get_model(config, primary_id)
            last_provider = model.provider
            fb_temp = model.temperature
            resp = _call_with_timeout(model, messages, tools, fb_temp, max_tokens)
            _record_circuit_success(model.provider)
            _record_circuit_success(primary_id)
            return FallbackResult(
                response=resp,
                model_used="primary",
                primary_model=primary_id,
            )
        except ValueError as e:
            # Model not found in config — skip retry, go straight to fallback
            error_msg = str(e)[:300]
            _record_circuit_failure(last_provider or primary_id)
            break
        except (httpx.HTTPStatusError) as e:
            status = e.response.status_code
            error_msg = str(e)[:300]
            if status in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)
                continue
            _record_circuit_failure(last_provider or primary_id)
            break  # non-retryable or exhausted
        except (httpx.TimeoutException, httpx.ConnectError,
                ConnectionError, TimeoutError) as e:
            error_msg = str(e)[:300]
            _record_circuit_failure(last_provider or primary_id)
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)
                continue
            break  # exhausted retries
        except RuntimeError as e:
            error_msg = str(e)[:300]
            _record_circuit_failure(last_provider or primary_id)
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

    # ── Try fallback ──
    _record_fallback(primary_id, fallback_id, error_msg)
    try:
        fallback_model = get_model(config, fallback_id)
        fb_temp = fallback_model.temperature
        resp = _call_with_timeout(fallback_model, messages, tools, fb_temp, max_tokens)
        _record_circuit_success(fallback_model.provider)
        return FallbackResult(
            response=resp,
            model_used="fallback",
            primary_model=primary_id,
        )
    except Exception as e:
        _record_circuit_failure(fallback_id)
        raise RuntimeError(
            f"Both primary ({primary_id}) and fallback ({fallback_id}) failed.\n"
            f"Primary error: {error_msg}\nFallback error: {e}"
        )


def _call_with_timeout(model, messages, tools, temperature, max_tokens):
    """Single LLM call with configurable timeout."""
    return call_llm(model, messages, tools, temperature, max_tokens)


# ── Model router (based on message size) ───────────────────────

def _route_model(config, messages, primary_id):
    """Auto-route based on message size to long-context model."""
    route_cfg = config.get("model", {}).get("route", {})
    if not route_cfg.get("enabled", False):
        return primary_id

    threshold = route_cfg.get("threshold_tokens", 8000)
    long_model = route_cfg.get("long_model")

    # Estimate total tokens roughly
    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    est_tokens = total_chars // 2  # rough: 2 chars ~ 1 token

    if est_tokens > threshold and long_model:
        return long_model
    return primary_id


# ── Config validation (kimi-k2.6 B1) ────────────────────────────

def validate_config(config: dict) -> list[str]:
    """Validate config.yaml structure. Returns list of warnings/errors."""
    issues = []

    providers = config.get("providers", {})
    if not providers:
        issues.append("ERROR: No providers configured")

    model_default = config.get("model", {}).get("default")
    if not model_default:
        issues.append("ERROR: model.default not set")

    # Check model.default exists in some provider
    found = False
    for pname, pinfo in providers.items():
        for m in pinfo.get("models", []):
            if m.get("id") == model_default:
                found = True
                # Check API key
                env_key = pinfo.get("api_key_env", "")
                if env_key and not os.environ.get(env_key):
                    issues.append(f"WARNING: {env_key} not set for provider {pname}")
                break
        if found:
            break
    if not found and model_default:
        issues.append(f"ERROR: Default model '{model_default}' not found in any provider")

    # Check fallback exists
    fallback = config.get("model", {}).get("fallback")
    if fallback:
        fbfound = False
        for pname, pinfo in providers.items():
            for m in pinfo.get("models", []):
                if m.get("id") == fallback:
                    fbfound = True
                    break
        if not fbfound:
            issues.append(f"WARNING: Fallback model '{fallback}' not found")

    # Check adversarial models
    adv = config.get("adversarial", {})
    if adv.get("enabled"):
        devil = adv.get("devil_model")
        if devil:
            devil_found = False
            for pname, pinfo in providers.items():
                for m in pinfo.get("models", []):
                    if m.get("id") == devil:
                        devil_found = True
                        break
            if not devil_found:
                issues.append(f"WARNING: Devil model '{devil}' not found in providers")

    return issues
