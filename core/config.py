"""Unified BAW config loader (P1-3, Opus 4.8 audit).

Single source of truth for loading and merging BAW config files.
Before this module existed, three different loaders existed:
  - core/llm.py:load_config()     — read ~/.baw/config.yaml only
  - tools/delegate_task.py:_get_minimax_config() — read ~/.baw/config.yaml only
  - cli/commands/chat.py:_cfg()    — merge BAW_ROOT/config.yaml + ~/.baw/config.yaml

The three call sites disagreed on which file(s) to read and what merge order
to apply, so a config change in repo/config.yaml might be picked up by CLI
chat but ignored by delegate_task. P0-1 (model_override passthrough) was
partially neutralized by this drift: if the user-tuned model_id was in repo
config but not in ~/.baw/providers, delegate_task's existence check would
reject it and silently fall back.

This module:
  1. Reads BOTH BAW_ROOT/config.yaml (repo) and ~/.baw/config.yaml (user).
  2. Merges with user-overrides-repo semantics: ~/.baw wins on conflict
     at the leaf level, recursing into nested dicts.
  3. Loads ~/.baw/.env into os.environ once.
  4. Caches the merged result so repeated calls don't re-parse YAML.
  5. Provides model_id validation so P0-1's override can fail loud, not silent.

HARD GATE (thread-local write guard):
  BAW's LLM is physically prevented from changing model/provider/endpoint
  settings without explicit user approval.  The guard uses a thread-local
  flag that defaults to False.  Only user-initiated code paths (slash
  commands, explicit user commands) bypass the gate by wrapping the write
  in the `allow_config_write()` context manager.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from .managed_config import apply_managed_layer, refuse_write, invalidate_cache as _invalidate_managed
import copy
from pathlib import Path
from typing import Optional

# Project root = the directory that contains core/, tools/, cli/, etc.
# We resolve it from this file's location so it works whether the package
# is imported as `baw` (with `baw.core.config`) or run as a flat script
# (with `core.config`).
_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT_CANDIDATES = [
    _THIS_FILE.parent.parent,        # baw/core/config.py → baw/
    _THIS_FILE.parent.parent.parent, # if module is one level deeper
]
BAW_ROOT: Path = next(
    (p for p in _PROJECT_ROOT_CANDIDATES if (p / "core").is_dir()),
    _PROJECT_ROOT_CANDIDATES[0],
)
BAW_HOME: Path = Path.home() / ".baw"

# Cache (thread-safe; we hold the lock briefly to set/clear).
_LOCK = threading.Lock()
_CACHED: Optional[dict] = None
_ENV_LOADED = False

# ── Thread-Local Write Guard (HARD GATE) ──────────────────────
# Prevents BAW's LLM from autonomously changing model/provider/endpoint
# settings.  Default: BLOCKED.  Only user-initiated code paths (slash
# commands, explicit /set, /model) bypass by wrapping with
# allow_config_write().

_write_guard = threading.local()
_write_guard.allowed = False

# Protected key prefixes — any config path starting with these triggers the gate.
# 'model.' intentionally excluded: model.default is user preference, not system safety.
PROTECTED_PREFIXES = [
    # "providers." removed — BAW can self-configure providers/models.
    # Managed config layer (managed_config.py) still protects critical keys
    # like base_url and api_key_env from being overwritten.
    "capabilities.",
]


@contextmanager
def allow_config_write():
    """Context manager that permits config writes for the current thread.

    Use this to wrap any user-initiated config write (e.g. slash commands,
    /set, /model, /reload).  BAW's agent loop does NOT use this, so LLM-
    triggered writes through execute_tool() are physically blocked.
    """
    old = getattr(_write_guard, 'allowed', False)
    _write_guard.allowed = True
    try:
        yield
    finally:
        _write_guard.allowed = old


def check_config_write_guard(path: str) -> None:
    """Raise PermissionError if writing to a protected path without user approval.

    Checks:
      1. Managed scope — checks user permission, defaults to blocked
      2. Thread-local write-allow flag (user-initiated via slash command)
      3. User-granted permission (via /permit command — even for LLM-initiated)
      4. endpoint/base_url keys anywhere in the dotted path
    """
    # Layer 1: Managed scope — check user permission (defaults to ask)
    refuse_write(path)

    if getattr(_write_guard, 'allowed', False):
        return  # User-initiated write — allowed

    # Check user-granted permission (allows LLM to write when user approved)
    try:
        from .permissions import check as _perm_check
        pfx = f"config:{path}"
        if _perm_check(pfx) == "granted":
            return  # User pre-approved via /permit
        # Also check broader scope like config:providers.
        parts = path.split(".")
        for i in range(len(parts) - 1, 0, -1):
            broad = f"config:{'.'.join(parts[:i])}."
            if _perm_check(broad) == "granted":
                return
    except ImportError:
        pass

    path_lower = path.lower()
    for prefix in PROTECTED_PREFIXES:
        if path_lower.startswith(prefix):
            raise PermissionError(
                f"[HARD GATE] Cannot write to protected path '{path}'. "
                f"BAW is not allowed to autonomously change model/provider/endpoint "
                f"settings.  Use request_config_change() to propose this change to "
                f"the user, or ask them to /permit this change."
            )
    # Catch endpoint/base_url keys anywhere in the dotted path
    if "endpoint" in path_lower or "base_url" in path_lower:
        raise PermissionError(
            f"[HARD GATE] Cannot write to endpoint/base_url path '{path}'. "
            f"BAW is not allowed to autonomously change endpoint settings. "
            f"Use request_config_change() to propose this change to the user."
        )


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on leaf conflict.

    Lists are replaced wholesale (no list-merge semantics) — a user config
    that wants to change one task_rule should rewrite the whole task_rules
    list, not expect us to splice it.
    """
    out = dict(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    """Read a YAML file, returning {} on missing or parse error."""
    if not path.exists():
        return {}
    try:
        import yaml
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data or {}
    except Exception:
        # Bad YAML shouldn't crash the whole app. Caller will see a partial
        # config (or {} if the bad file is the only one). Audit this
        # elsewhere — load_config is on the hot path.
        return {}


def _load_env_once() -> None:
    """Read ~/.baw/.env into os.environ, once per process.

    setdefault semantics: we never overwrite an env var already set in the
    process environment (e.g. by systemd EnvironmentFile). This matches
    the previous behavior in tools/delegate_task.py:_get_minimax_config.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = BAW_HOME / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    os.environ.setdefault(k, v)
        except Exception:
            pass
    _ENV_LOADED = True


def load_config(*, reload: bool = False, config_path: Optional[Path] = None) -> dict:
    """Load and merge BAW config (P1-3 unified loader).

    Merge order (lowest → highest priority):
      1. BAW_ROOT/config.yaml            (repo defaults, shipped with the code)
      2. BAW_HOME/config.yaml            (user overrides, copied by setup wizard)
      3. config_path                     (explicit override; e.g. tests)

    Within each file, leaves override earlier values. Lists are replaced
    wholesale (see _deep_merge).

    The result is cached in-process. Pass reload=True to force a re-parse
    (used by `baw config reload` and tests). Thread-safe.
    """
    global _CACHED
    _load_env_once()

    with _LOCK:
        if _CACHED is not None and not reload and config_path is None:
            return _CACHED

        merged: dict = {}
        # 1) Repo defaults (lowest priority)
        repo_cfg = BAW_ROOT / "config.yaml"
        merged = _deep_merge(merged, _load_yaml(repo_cfg))

        # 2) User overrides
        user_cfg = BAW_HOME / "config.yaml"
        merged = _deep_merge(merged, _load_yaml(user_cfg))

        # 3) Explicit override (e.g. test fixture)
        if config_path is not None:
            merged = _deep_merge(merged, _load_yaml(Path(config_path)))

        # 4) Managed scope — system-defined overrides, highest priority
        merged = apply_managed_layer(merged)

        if config_path is None and not reload:
            _CACHED = merged
        return merged


def model_exists(config: dict, model_id: str) -> bool:
    """True if model_id is declared in any provider's models list.

    P0-1 depends on this: when the router hands a model_id down, delegate_task
    uses this to confirm it can be resolved before falling back silently.
    """
    if not model_id:
        return False
    for _pname, pcfg in (config.get("providers") or {}).items():
        for m in pcfg.get("models", []) or []:
            if m.get("id") == model_id:
                return True
    return False


def get_api_key_for_model(config: dict, model_id: str) -> str:
    """Resolve the API key env-var name for a model and return its value.

    Returns "" if the model is unknown or the env var is unset. Does NOT
    raise — callers handle the empty-key case.
    """
    for _pname, pcfg in (config.get("providers") or {}).items():
        for m in pcfg.get("models", []) or []:
            if m.get("id") == model_id:
                env_name = pcfg.get("api_key_env", "")
                if env_name:
                    return os.environ.get(env_name, "")
                # api_key embedded directly in provider cfg
                return pcfg.get("api_key", "") or ""
    return ""


def invalidate_cache() -> None:
    """Clear the config and managed config caches. Call after config writes."""
    global _CACHED
    with _LOCK:
        _CACHED = None
    _invalidate_managed()
