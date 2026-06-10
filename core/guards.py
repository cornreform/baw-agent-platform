"""
BAW — System-level guards (kimi-k2.6 + deepseek-v4-pro suggestions)
- B2: Session auto-save + crash recovery
- B3: Consistent error messages (i18n helper)
- B5: Tool execution timeout
- C1: Streaming safety guard
- C4: Memory pressure monitor
"""
import os
import gc
import time
import signal
import threading
from pathlib import Path
from typing import Optional, Callable

BAW_HOME = Path.home() / ".baw"

# ═══════════════════════════════════════════════════════════════
# B3 — Error message helper (consistent format across all modules)
# ═══════════════════════════════════════════════════════════════

_ERR_TEMPLATES = {
    "api_key_missing": "✗ No API key for {provider}. Set {env_var} in ~/.baw/.env",
    "config_not_found": "✗ Config not found at {path}. Run baw setup first.",
    "docker_not_running": "✗ BAW container not running. Start: cd ~/baw && docker compose up -d",
    "docker_not_found": "✗ Docker not found. Is Docker installed?",
    "model_not_found": "✗ Model '{model_id}' not found. Run baw models to see available.",
    "session_not_found": "✗ Session '{session_id}' not found.",
    "tool_timeout": "✗ Tool '{tool}' timed out after {timeout}s",
    "tool_unknown": "✗ Unknown tool: '{tool}'",
    "api_error": "✗ API error ({status}): {message}",
    "network_error": "✗ Network error: {detail}",
    "circuit_open": "⏸ Circuit open for {provider} ({failures} failures, retry in {remain}s)",
    "shutdown": "⏹ Shutting down...",
    "no_session_history": "📭 No saved sessions yet.",
}


def bail(key: str, **kwargs) -> str:
    """Return a consistent error message. All user-facing errors use this."""
    template = _ERR_TEMPLATES.get(key, f"✗ Error: {key}")
    return template.format(**kwargs) if kwargs else template


# ═══════════════════════════════════════════════════════════════
# B2 — Session auto-save + crash recovery
# ═══════════════════════════════════════════════════════════════

class SessionGuard:
    """Periodic auto-save + crash recovery for Telegram sessions."""

    _instance: Optional["SessionGuard"] = None
    _save_callback: Optional[Callable] = None
    _interval: int = 60  # seconds between auto-saves
    _timer: Optional[threading.Timer] = None
    _running: bool = False

    @classmethod
    def init(cls, save_callback: Callable, interval: int = 60):
        """Start periodic auto-save. save_callback is called every `interval`."""
        if cls._instance:
            return
        cls._instance = cls()
        cls._save_callback = save_callback
        cls._interval = interval
        cls._running = True
        cls._schedule_next()

    @classmethod
    def _schedule_next(cls):
        if not cls._running:
            return
        cls._timer = threading.Timer(cls._interval, cls._do_save)
        cls._timer.daemon = True
        cls._timer.start()

    @classmethod
    def _do_save(cls):
        if cls._save_callback and cls._running:
            try:
                cls._save_callback()
            except Exception:
                pass
        cls._schedule_next()

    @classmethod
    def shutdown(cls):
        """Save once more and stop timer."""
        cls._running = False
        if cls._timer:
            cls._timer.cancel()
        if cls._save_callback:
            try:
                cls._save_callback()
            except Exception:
                pass

    @classmethod
    def save_now(cls):
        """Force immediate save."""
        if cls._save_callback:
            try:
                cls._save_callback()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# B5 — Tool execution timeout
# ═══════════════════════════════════════════════════════════════

TOOL_DEFAULT_TIMEOUT = 30  # seconds


def execute_tool_with_timeout(name: str, args: dict, executor: Callable, timeout: int = TOOL_DEFAULT_TIMEOUT) -> str:
    """Execute a tool with a timeout guard. Returns result string."""
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor, **args)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return bail("tool_timeout", tool=name, timeout=timeout)
        except Exception as e:
            return f"✗ Tool '{name}' error: {e}"


# ═══════════════════════════════════════════════════════════════
# C1 — Streaming safety guard
# ═══════════════════════════════════════════════════════════════

def safe_stream_content(text: str, max_length: int = 10000) -> str:
    """Truncate streamed content if it exceeds safe limit."""
    if len(text) > max_length:
        return text[:max_length] + f"\n\n... [truncated {len(text) - max_length} chars]"
    return text


# ═══════════════════════════════════════════════════════════════
# C4 — Memory pressure monitor
# ═══════════════════════════════════════════════════════════════

_MEMORY_WARN_THRESHOLD_MB = 400  # warn at 400MB
_MEMORY_CRIT_THRESHOLD_MB = 480  # critical at 480MB (near Docker 512M limit)


def check_memory_pressure() -> Optional[str]:
    """Check process memory usage. Returns warning string if pressure is high, None otherwise."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # RSS in KB on Linux
        mem_mb = usage.ru_maxrss / 1024  # KB to MB

        if mem_mb > _MEMORY_CRIT_THRESHOLD_MB:
            # Force garbage collection
            gc.collect()
            return (f"⚠ Memory CRITICAL: {mem_mb:.0f}MB (limit {_MEMORY_CRIT_THRESHOLD_MB}MB). "
                    f"GC run. Consider restarting.")
        if mem_mb > _MEMORY_WARN_THRESHOLD_MB:
            gc.collect()
            return f"⚠ Memory HIGH: {mem_mb:.0f}MB. GC run."
    except Exception:
        pass
    return None


def memory_usage_mb() -> float:
    """Get current process memory usage in MB."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        return 0.0
