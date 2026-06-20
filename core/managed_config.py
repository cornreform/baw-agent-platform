"""Managed config layer — system-defined overrides that always win.

Applied AFTER user config, so managed values can't be overwritten by
BAW's autonomous changes or unintentional user config edits.

Three layers of protection:
  1. **Write guard** — managed keys raise PermissionError even with
     allow_config_write() active. Only direct file editing can change them.
  2. **Load-time override** — apply_managed_layer() re-asserts managed
     values on every load_config() call, so silent corruption is healed.
  3. **Save scrub** — strip_managed_keys() removes managed keys from
     dicts that are about to be written back to config.yaml, preventing
     accidental overwrite via _save_config().

Sunny 2026-06-20: System-defined config layer that always wins over user config.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("baw.managed_config")


# ── Managed key declarations ─────────────────────────────────────
# Dot-separated paths into the config dict.
# These values are defined at install/setup time and should never change
# without explicit user file editing.

MANAGED_KEYS: frozenset[str] = frozenset({
    # ── Provider base URLs (prevents silent drift to Chinese defaults) ──
    "providers.minimax.base_url",
    "providers.stepfun.base_url",
    "providers.deepseek.base_url",
    "providers.moonshot.base_url",
    "providers.xai.base_url",
    "providers.openrouter.base_url",
    "providers.openai.base_url",
    "providers.agnes.base_url",
    "providers.groq.base_url",

    # ── Provider API key env names (prevents key routing corruption) ──
    "providers.minimax.api_key_env",
    "providers.stepfun.api_key_env",
    "providers.deepseek.api_key_env",
    "providers.moonshot.api_key_env",
    "providers.xai.api_key_env",
    "providers.openrouter.api_key_env",
    "providers.openai.api_key_env",
    "providers.agnes.api_key_env",
    "providers.groq.api_key_env",

    # ── System identity (admin routing) ──
    "telegram.admin_chat_id",

    # ── Capability method assignments (prevents AI changing transport) ──
    "capabilities.stt.method",
    "capabilities.tts.method",
    "capabilities.vision.method",
    "capabilities.image_generation.method",
    "capabilities.browser.method",

    # ── System clock / timezone (cron accuracy) ──
    "cron.timezone",

    # ── Router strategy (system-level routing decision) ──
    "router.strategy",
})


# ── Helper: resolve dotted key in nested dict ────────────────────

def _resolve_key(config: dict, path: str) -> tuple[dict | None, str]:
    """Walk a dotted path into a nested dict.

    Returns (parent_dict, leaf_key).  If an intermediate key is missing
    or not a dict, returns (None, '').
    """
    parts = path.split(".")
    current = config
    for i, part in enumerate(parts[:-1]):
        if not isinstance(current, dict) or part not in current:
            return None, ""
        current = current[part]
    if not isinstance(current, dict):
        return None, ""
    return current, parts[-1]


def _set_key(config: dict, path: str, value) -> bool:
    """Set a dotted key path in a nested dict. Creates intermediate dicts.

    Returns True if the value was actually different from existing, False if same.
    """
    parts = path.split(".")
    current = config
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        elif not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    leaf = parts[-1]
    existing = current.get(leaf)
    current[leaf] = value
    return existing != value


def _delete_key(config: dict, path: str) -> bool:
    """Delete a dotted key path from a nested dict. Returns True if removed."""
    parent, leaf = _resolve_key(config, path)
    if parent is None or leaf not in parent:
        return False
    del parent[leaf]
    return True


# ── Public API ────────────────────────────────────────────────────

def is_managed(path: str) -> bool:
    """Check if a config path is managed (blocked from agent writes)."""
    if path in MANAGED_KEYS:
        return True
    # Also match wildcard prefixes like "providers.*.base_url"
    # by checking if any managed key starts with this path
    for managed in MANAGED_KEYS:
        if managed == path:
            continue
        # If path is a prefix of a managed key, it's managed
        if managed.startswith(path + ".") or managed.startswith(path):
            return True
    return False


def refuse_write(path: str) -> None:
    """Check permission for a managed key write.

    Default: raise PermissionError (ask user).
    If user has granted session/permanent permission, allow.
    If user has blocked, raise with BLOCKED message.
    """
    if not is_managed(path):
        return

    # Check user-configured permission
    try:
        from .permissions import check
        level = check(f"config:{path}")
        if level == "granted":
            return  # User approved exact path — allow write
        # Also check broader scopes (e.g. /permit config:providers. → matches all providers)
        parts = path.split(".")
        for i in range(len(parts) - 1, 0, -1):
            broad = f"config:{'.'.join(parts[:i])}."
            if check(broad) == "granted":
                return
        if level == "blocked":
            raise PermissionError(
                f"[BLOCKED] User has blocked writes to managed key '{path}'. "
                f"Use /permit {path} to unblock."
            )
    except ImportError:
        pass

    # Default: ask user
    raise PermissionError(
        f"[MANAGED SCOPE] '{path}' is a system-managed setting. "
        f"To approve this change, use: /permit config:{path}\n"
        f"Or for permanent approval: /permit config:{path} permanent"
    )


def apply_managed_layer(config: dict) -> dict:
    """Re-assert all managed key values onto *config* (in-place + return).

    This is the load-time override: whatever the user config says,
    managed values win.  Call this as the **last** merge step in
    load_config().
    """
    managed_src = _load_managed_source()
    if not managed_src:
        return config  # no managed source → no overrides

    changed = 0
    for key in MANAGED_KEYS:
        value = _resolve_key(managed_src, key)
        if value[0] is None:
            continue  # key not in managed source
        if _set_key(config, key, value[0][value[1]]):
            changed += 1

    if changed:
        logger.debug(f"[ManagedConfig] Overrode {changed} managed key(s) from source")
    return config


def strip_managed_keys(config: dict) -> dict:
    """Remove all managed keys from *config* (deep copy, returns new dict).

    Use this before writing config back to disk — prevents the save
    from persisting unintended managed values.
    """
    result = copy.deepcopy(config)
    for key in MANAGED_KEYS:
        _delete_key(result, key)
    return result


# ── Managed source file ──────────────────────────────────────────
# The managed values live in a separate file so they're version-controlled
# and survive config.yaml rewrites.

_MANAGED_SOURCE_PATH = Path.home() / ".baw" / "managed_config.yaml"

# Sentinel for "not loaded" — None means "never loaded"
_LOAD_SENTINEL: Any = None  # None=not loaded, {} or dict=loaded result


def _load_managed_source() -> dict:
    """Read the managed config source file (cached after first load)."""
    global _LOAD_SENTINEL
    if _LOAD_SENTINEL is not None:
        _cache: dict = _LOAD_SENTINEL
        return _cache

    path = _MANAGED_SOURCE_PATH
    if not path.exists():
        _LOAD_SENTINEL = {}
        return _LOAD_SENTINEL

    try:
        import yaml
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _LOAD_SENTINEL = data or {}
    except Exception as exc:
        logger.warning(f"[ManagedConfig] Failed to load {path}: {exc}")
        _LOAD_SENTINEL = {}
    return _LOAD_SENTINEL


def invalidate_cache() -> None:
    """Clear the managed config cache (call after file change)."""
    global _LOAD_SENTINEL
    _LOAD_SENTINEL = None


def initialize_managed_source(repo_root: Path | None = None) -> None:
    """Create ~/.baw/managed_config.yaml from repo config.yaml if missing.

    Extracts the current managed keys from the live config and writes
    them to the managed source file.  Safe to call on every startup.
    """
    from .config import load_config

    target = _MANAGED_SOURCE_PATH
    if target.exists():
        return  # already exists — don't overwrite

    config = load_config()
    managed_data: dict = {}

    for key in MANAGED_KEYS:
        parent, leaf = _resolve_key(config, key)
        if parent is not None and leaf in parent:
            _set_key(managed_data, key, parent[leaf])

    if managed_data:
        import yaml
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            yaml.dump(managed_data, f, allow_unicode=True, default_flow_style=False)
        logger.info(f"[ManagedConfig] Created {target} with {len(managed_data)} keys")
