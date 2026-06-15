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


# ═══════════════════════════════════════════════════════════════
# S1 — Tool execution safety gate
# ═══════════════════════════════════════════════════════════════

# Patterns that require audit before execution
_DOWNLOAD_PATTERNS = ["curl ", "wget ", "git clone", "git pull", "pip install",
                       "npm install", "yarn add", "cargo install"]

_EXEC_PATTERNS = ["/tmp/", ".venv/bin/python", "node /tmp/", "bash /tmp/"]

_SYSTEM_PATHS = ["/etc/", "/boot/", "/usr/bin/", "/usr/lib/", "/var/",
                 "~/.ssh/", "~/.gnupg/"]


def check_safety(name: str, arguments: dict) -> tuple:
    """Pre-execution safety check. Returns (blocked: bool, reason: str).

    Blocks dangerous operations that haven't been audited.
    Currently enforces: no silent downloads without audit awareness.
    """
    import os

    # Check 1: bash/terminal executing downloads without audit
    if name in ("bash", "terminal"):
        cmd = str(arguments.get("command", "") or arguments.get("cmd", ""))
        cmd_lower = cmd.lower()

        for pattern in _DOWNLOAD_PATTERNS:
            if pattern in cmd_lower:
                # Check if this looks like it needs audit
                if any(kw in cmd_lower for kw in ("github.com", "gitlab.com", "-g ",
                                                    "clone", "remote", "download")):
                    return True, (
                        f"⛔ SAFETY GATE: '{name}' tool is about to download external code.\\n"
                        f"Command: `{cmd[:200]}`\\n\\n"
                        f"⚠️  Before downloading third-party code, you MUST:\\n"
                        f"  1. Use `code_scan(path='<download dir>')` to audit the source first\\n"
                        f"  2. Report findings to the user\\n"
                        f"  3. Only proceed if deemed safe\\n\\n"
                        f"If you believe this download is safe, use code_scan first, then retry."
                    )

        # Check 2: executing scripts from /tmp without audit
        for pattern in _EXEC_PATTERNS:
            if pattern in cmd_lower:
                return True, (
                    f"⛔ SAFETY GATE: Executing code from /tmp.\\n"
                    f"Command: `{cmd[:200]}`\\n\\n"
                    f"⚠️  Code in /tmp may be untrusted. Use `code_scan(path='/tmp/<dir>')` first."
                )

    # Check 2: http_fetch downloading large amounts
    if name == "http_fetch":
        url = str(arguments.get("url", ""))
        if any(kw in url.lower() for kw in ("github.com", "raw.githubusercontent.com",
                                               "gitlab.com", "bitbucket.org")):
            return True, (
                f"⛔ SAFETY GATE: `http_fetch` is downloading from {url[:100]}.\\n"
                f"⚠️  Before downloading external code, verify the source is trusted.\\n"
                f"If this is a known/safe source, re-run with explicit intent.\\n"
                f"For unknown repos: clone first, then use `code_scan()` to audit."
            )

    # Check 3: write_file to system paths
    if name == "write_file":
        path = str(arguments.get("path", ""))
        expanded = os.path.expanduser(path)
        for sys_path in _SYSTEM_PATHS:
            if expanded.startswith(os.path.expanduser(sys_path)):
                return True, (
                    f"⛔ SAFETY GATE: Writing to system path: {path}\\n"
                    f"⚠️  Modifying system files requires explicit user confirmation.\\n"
                    f"Use `read_file` to inspect the file first, then ask the user."
                )

    return False, ""
