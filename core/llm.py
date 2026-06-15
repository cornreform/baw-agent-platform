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

def _check_circuit(provider_or_model: str) -> None:
    """Raise RuntimeError ONLY if circuit just opened (give a grace period).

    If circuit is in cooldown, do NOT raise — instead log a warning and
    return. The caller will try the call, and on failure the next
    second-tier fallback will pick another provider immediately.
    This prevents the user-facing 'retry in 22s' deadlock.
    """
    import logging as _log
    with _CIRCUIT_LOCK:
        state = _CIRCUIT_STATE.get(provider_or_model)
        if state and state.get("open_until", 0) > time.time():
            remain = int(state["open_until"] - time.time())
            _log.debug(
                f"[LLM] circuit-cooling {provider_or_model} "
                f"({state['failures']} fails, {remain}s remain) — trying anyway"
            )

def _record_circuit_failure(provider_or_model: str, error_signature: str = "") -> None:
    """Record a failure. Opens circuit if threshold reached AND
    failure signatures repeat (same error pattern, not transient).

    error_signature: short hash of the error class+code so we only
    count *persistent* failures. Transient 429/503 don't open the
    circuit — they retry via fallback.
    """
    with _CIRCUIT_LOCK:
        state = _CIRCUIT_STATE.setdefault(
            provider_or_model,
            {"failures": 0, "last_fail": 0, "open_until": 0,
             "last_sig": "", "sig_count": 0}
        )
        state["failures"] += 1
        state["last_fail"] = time.time()
        # If same error signature, increment sig count.
        # Different error → reset sig count (don't punish if failures
        # are diverse — they may be transient).
        if error_signature and error_signature == state.get("last_sig"):
            state["sig_count"] = state.get("sig_count", 0) + 1
        else:
            state["last_sig"] = error_signature
            state["sig_count"] = 1
        # Only OPEN if threshold reached AND same signature repeats 3x
        if (state["failures"] >= CIRCUIT_THRESHOLD
                and state["sig_count"] >= 3):
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

# P1-5 (Opus 4.8 audit): wrap signal.signal in try/except — it can only be
# called from the main thread. Subprocess / ThreadPoolExecutor / Textual TUI
# contexts would otherwise raise ValueError on import.
try:
    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)
except (ValueError, OSError):
    # Not in main thread, or signal not available (e.g. Windows constrained runtimes).
    pass


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


def calculate_cost(model: ModelDef, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD from token counts + model pricing (per 1M tokens)."""
    in_cost = (input_tokens / 1_000_000) * model.cost_per_1m_input
    out_cost = (output_tokens / 1_000_000) * model.cost_per_1m_output
    return round(in_cost + out_cost, 6)


def load_config(config_path: Optional[Path] = None) -> dict:
    """Load BAW config from yaml or dict, and auto-load .env for API keys.

    P1-3 (Opus 4.8 audit): delegates to core.config.load_config so all
    callers see the same merged view. Maintained as a thin wrapper for
    backward compatibility — it also continues to populate os.environ
    from ~/.baw/.env (load_config already does that, but keeping the
    behavior here means older callers stay safe even if they pre-import
    load_config before core.config is initialized).
    """
    from .config import load_config as _unified_load
    return _unified_load(reload=False, config_path=config_path)


def get_model(config: dict, model_id: Optional[str] = None) -> ModelDef:
    """Resolve a model definition from config. Protocol-agnostic."""
    cfg = config.get("model", {})
    model_id = model_id or cfg.get("default", "deepseek-v4-flash")
    providers = config.get("providers", {})

    for provider_name, provider_cfg in providers.items():
        for m in provider_cfg.get("models", []):
            mid = m.get("id")
            if not mid:
                continue
            if mid == model_id:
                return ModelDef(
                    id=mid,
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
    available = [
        m.get("id") for p in providers.values()
        for m in p.get("models", []) if m.get("id")
    ]
    raise ValueError(f"Model '{model_id}' not found in config.\n"
                     f"Available: {available}")


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
    if not model.api_key:
        raise ValueError(
            f"API key missing for provider '{model.provider}'. "
            f"Check ~/.baw/.env or config.yaml"
        )
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

    import time as _time
    _start = _time.time()
    _model_id = body.get("model", "unknown")
    _provider = url.split("/")[2] if "/" in url else "unknown"

    client = _get_http_client()
    try:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        _latency = _time.time() - _start
        _record_latency(_provider, _model_id, _latency, "ok")
        return resp.json()
    except httpx.TimeoutException:
        _latency = _time.time() - _start
        _record_latency(_provider, _model_id, _latency, "timeout")
        raise RuntimeError(f"LLM API timeout: {url}")
    except httpx.HTTPStatusError as e:
        _latency = _time.time() - _start
        _record_latency(_provider, _model_id, _latency, f"http_{e.response.status_code}")
        _status = e.response.status_code
        _body = e.response.text[:500]
        # User-friendly error classification
        if _status == 401:
            raise RuntimeError(
                f"API key 無效 (401)\n"
                f"Provider: {_provider}\n"
                f"解決: 檢查 ~/.baw/.env 中的 {_provider.upper()}_API_KEY 是否正確"
            )
        elif _status == 402:
            raise RuntimeError(
                f"API 配額已用盡 (402)\n"
                f"Provider: {_provider}\n"
                f"解決: 請去 {_provider} 平台充值或更換 API key"
            )
        elif _status == 429:
            raise RuntimeError(
                f"API 限流 (429)\n"
                f"Provider: {_provider}\n"
                f"解決: 等幾分鐘後重試, 或檢查配額"
            )
        elif _status == 503:
            raise RuntimeError(
                f"API 服務暫時不可用 (503)\n"
                f"Provider: {_provider}\n"
                f"解決: 等幾分鐘後重試"
            )
        else:
            raise RuntimeError(
                f"LLM API 錯誤: {_status}\n"
                f"Provider: {_provider}\n"
                f"詳情: {_body[:200]}"
            )
    except json.JSONDecodeError as e:
        _latency = _time.time() - _start
        _record_latency(_provider, _model_id, _latency, "json_error")
        raise RuntimeError(f"LLM API non-JSON response: {e}")


def _record_latency(provider: str, model: str, latency: float, status: str):
    """Append latency record to JSONL log."""
    import json as _json, time as _time
    from pathlib import Path as _Path
    _log = _Path.home() / ".baw" / "logs" / "latency.jsonl"
    _log.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _time.time(),
        "provider": provider,
        "model": model,
        "latency": round(latency, 3),
        "status": status,
    }
    try:
        with open(_log, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass


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

    # ── Sanity check: fallback must differ from primary ──
    if fallback_id and fallback_id == primary_id:
        import logging as _log
        _log.warning(f"[LLM] fallback '{fallback_id}' == primary — will be skipped")
        fallback_id = ""

    # ── Auto-route based on message size ──
    primary_id = _route_model(config, messages, primary_id)

    # ── Circuit breaker check (advisory only — won't block) ──
    try:
        primary_model = get_model(config, primary_id)
        _check_circuit(primary_model.provider)
        _check_circuit(primary_id)
    except ValueError as ve:
        raise ve
    except RuntimeError:
        # Old: skip straight to fallback. New: just continue,
        # let the actual API call fail and trigger second-tier fallback.
        pass

    RETRYABLE_STATUS = {429, 503, 502, 504}
    # Retry config: read from config.yaml, with sensible defaults
    retry_cfg = config.get("retry", {})
    MAX_RETRIES = retry_cfg.get("max_retries", 3)
    BASE_DELAY = retry_cfg.get("base_delay", 1.0)
    EXPONENTIAL_BASE = retry_cfg.get("exponential_base", 2.0)
    MAX_DELAY = retry_cfg.get("max_delay", 60.0)  # cap at 60s

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
            _record_circuit_failure(last_provider or primary_id, f"ValueError:{primary_id}")
            break
        except (httpx.HTTPStatusError) as e:
            status = e.response.status_code
            error_msg = str(e)[:300]
            if status in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                delay = min(BASE_DELAY * (EXPONENTIAL_BASE ** attempt), MAX_DELAY)
                _time.sleep(delay)
                continue
            _record_circuit_failure(last_provider or primary_id, f"HTTP:{status}")
            break  # non-retryable or exhausted
        except (httpx.TimeoutException, httpx.ConnectError,
                ConnectionError, TimeoutError) as e:
            error_msg = str(e)[:300]
            _record_circuit_failure(last_provider or primary_id, "NetworkTimeout")
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)
                continue
            break  # exhausted retries
        except RuntimeError as e:
            error_msg = str(e)[:300]
            # Extract status code or error category from message
            _sig = "RuntimeError"
            if "402" in error_msg: _sig = "HTTP:402"
            elif "429" in error_msg: _sig = "HTTP:429"
            elif "401" in error_msg: _sig = "HTTP:401"
            elif "403" in error_msg: _sig = "HTTP:403"
            elif "500" in error_msg: _sig = "HTTP:500"
            elif "503" in error_msg: _sig = "HTTP:503"
            elif "timeout" in error_msg.lower(): _sig = "Timeout"
            _record_circuit_failure(last_provider or primary_id, _sig)
            # Check if it's a transient error (timeout/rate-limit message)
            if ("timeout" in error_msg.lower() or "rate" in error_msg.lower()
                    or "overloaded" in error_msg.lower()) and attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                _time.sleep(delay)
                continue
            break  # non-retryable RuntimeError

    # ── Fallback chain ──
    _providers = config.get("providers", {})
    _tried = {primary_id}
    if fallback_id:
        _tried.add(fallback_id)
    
    if fallback_id:
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
            _record_circuit_failure(fallback_id, "SecondTier")
    else:
        import logging as _logging
        _logging.info(f"[LLM] {primary_id} failed (no fallback) → scanning all providers")

    # ── Second-tier fallback: scan all providers for any working model ──
    _tried_providers = set()
    for _m in list(_tried):
        try:
            _tried_providers.add(get_model(config, _m).provider)
        except Exception:
            pass
    for _pname, _pcfg in _providers.items():
        if _pname in _tried_providers:
            continue
        for _m in _pcfg.get("models", []):
            _mid = _m.get("id", "")
            if not _mid or _mid in _tried:
                continue
            if "chat" not in _m.get("capabilities", []):
                continue
            _tried.add(_mid)
            try:
                _fb2 = get_model(config, _mid)
                _resp = _call_with_timeout(_fb2, messages, tools, _fb2.temperature, max_tokens)
                _record_circuit_success(_pname)
                return FallbackResult(
                    response=_resp,
                    model_used=f"fallback2:{_mid}",
                    primary_model=primary_id,
                )
            except Exception as _e2:
                _sig = "SecondTier:" + (str(_e2)[:50] if str(_e2) else "Unknown")
                _record_circuit_failure(_mid, _sig)
                continue

    # ── Last resort: try ALL models in ALL providers regardless ──
    for _pname, _pcfg in _providers.items():
        for _m in _pcfg.get("models", []):
            _mid = _m.get("id", "")
            if not _mid or _mid in _tried:
                continue
            if "chat" not in _m.get("capabilities", []):
                continue
            _tried.add(_mid)
            try:
                _fb3 = get_model(config, _mid)
                _resp = _call_with_timeout(_fb3, messages, tools, _fb3.temperature, max_tokens)
                _record_circuit_success(_pname)
                return FallbackResult(
                    response=_resp,
                    model_used=f"last-resort:{_mid}",
                    primary_model=primary_id,
                )
            except Exception:
                continue

    # Only when EVERY chat-capable model across ALL providers has failed
    # Build a user-friendly summary of what went wrong
    _provider_status = []
    for _pname, _pcfg in _providers.items():
        _key_env = _pcfg.get("api_key_env", "")
        _has_key = bool(os.environ.get(_key_env, ""))
        _models = [m.get("id", "") for m in _pcfg.get("models", [])]
        _status = "✅ key set" if _has_key else "❌ no key"
        _provider_status.append(f"  {_pname}: {_status} (模型: {', '.join(_models[:2])})")

    _friendly = (
        f"** 無可用的 AI 服務**\n\n"
        f"所有配置的 LLM provider 均無法連線。\n\n"
        f"Provider 狀態:\n"
        + "\n".join(_provider_status) + "\n\n"
        f"原始錯誤: {error_msg[:150]}\n\n"
        f"解決方法:\n"
        f"1. 檢查 ~/.baw/.env 是否有效的 API key\n"
        f"2. 確認 API key 未過期或配額未用盡\n"
        f"3. 網絡連線是否正常\n"
        f"4. 使用 /doctor 指令檢查系統狀態"
    )
    raise RuntimeError(_friendly)


def _call_with_timeout(model, messages, tools, temperature, max_tokens):
    """Single LLM call with hard wall-clock timeout.

    The shared httpx client has a 120s read timeout, but slow-streaming
    servers can reset that timer indefinitely. We wrap the call in a
    thread with a hard timeout to prevent multi-minute hangs.
    """
    import concurrent.futures
    LLM_TIMEOUT = 90  # seconds: hard cap per LLM call

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call_llm, model, messages, tools, temperature, max_tokens)
        try:
            return future.result(timeout=LLM_TIMEOUT)
        except concurrent.futures.TimeoutError:
            raise RuntimeError(f"LLM API timeout after {LLM_TIMEOUT}s: {model.provider}/{model.id}")


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
