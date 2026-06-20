"""BAW Permission System

Three-tier permission model:
  - blocked:   User explicitly denied. BAW must refuse.
  - ask:       Default. BAW asks user, user says yes/no.
  - granted:   User pre-approved (session or permanent).

Persistence: ~/.baw/permissions.json

Permission scopes (dot-path):
  config:modify         — modify config.yaml (including managed keys)
  config:provider       — modify provider endpoints/keys
  config:model          — modify model definitions
  env:modify            — modify .env file
  system:exec           — execute system commands (bash)
  file:write-sensitive  — write to /etc, /usr, etc.
  deploy                — rebuild/restart container
  tools:generate        — create new tools
  tools:install         — install packages
  memory:purge          — delete memory/data
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

PERM_FILE = "permissions.json"
SESSION_APPROVALS: dict = {}  # in-memory, cleared on restart


def _perm_path(data_dir: Optional[Path] = None) -> Path:
    base = data_dir or Path.home() / ".baw"
    return base / PERM_FILE


def _load(data_dir: Optional[Path] = None) -> dict:
    path = _perm_path(data_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(perms: dict, data_dir: Optional[Path] = None):
    path = _perm_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(perms, indent=2, ensure_ascii=False), encoding="utf-8")


def get_level(scope: str, data_dir: Optional[Path] = None) -> str:
    """Return the effective permission level for scope.
    Priority: session > permanent config > default (ask).
    """
    # 1. Session approval (in-memory, highest priority)
    if scope in SESSION_APPROVALS:
        expiry = SESSION_APPROVALS[scope].get("expires_at")
        if expiry is None or time.time() < expiry:
            ses = SESSION_APPROVALS[scope]["level"]
            if ses == "granted":
                return "granted"
            if ses == "blocked":
                return "blocked"
        else:
            # Expired
            del SESSION_APPROVALS[scope]

    # 2. Persistent config
    perms = _load(data_dir)
    entry = perms.get(scope, {})
    level = entry.get("level", "ask")
    return level


def check(scope: str, data_dir: Optional[Path] = None) -> str:
    """Check permission. Returns 'granted', 'blocked', or 'ask'."""
    return get_level(scope, data_dir)


def grant(scope: str, duration: str = "session", data_dir: Optional[Path] = None):
    """Grant permission for a scope.
    duration: 'session' (this restart), 'permanent' (persisted), or '5m'/'1h'/'1d'.
    """
    if duration == "session":
        SESSION_APPROVALS[scope] = {"level": "granted", "expires_at": None}
    elif duration == "permanent":
        perms = _load(data_dir)
        perms[scope] = {"level": "granted", "duration": "permanent", "updated_at": time.time()}
        _save(perms, data_dir)
    else:
        # Parse duration like "5m", "1h", "1d"
        seconds = _parse_duration(duration)
        SESSION_APPROVALS[scope] = {
            "level": "granted",
            "expires_at": time.time() + seconds,
        }


def block(scope: str, duration: str = "session", data_dir: Optional[Path] = None):
    """Block a scope."""
    if duration == "session":
        SESSION_APPROVALS[scope] = {"level": "blocked", "expires_at": None}
    else:
        perms = _load(data_dir)
        perms[scope] = {"level": "blocked", "duration": duration, "updated_at": time.time()}
        _save(perms, data_dir)


def reset(scope: str = None, data_dir: Optional[Path] = None):
    """Reset permission(s) to default (ask). scope=None = reset all."""
    if scope:
        SESSION_APPROVALS.pop(scope, None)
        perms = _load(data_dir)
        perms.pop(scope, None)
        _save(perms, data_dir)
    else:
        SESSION_APPROVALS.clear()
        _save({}, data_dir)


def list_perms(data_dir: Optional[Path] = None) -> dict:
    """List all current permissions (both session and persistent)."""
    perms = _load(data_dir)
    result = {}
    for scope, entry in perms.items():
        result[scope] = {"level": entry.get("level", "ask"), "source": "persistent"}
    for scope, entry in SESSION_APPROVALS.items():
        if scope in result:
            result[scope]["source"] = "session (overrides)"
        else:
            result[scope] = {"level": entry["level"], "source": "session"}
        if entry.get("expires_at"):
            remaining = int(entry["expires_at"] - time.time())
            result[scope]["expires_in"] = f"{remaining}s" if remaining > 0 else "expired"
    return result


def _parse_duration(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("m"):
        return int(s[:-1]) * 60
    elif s.endswith("h"):
        return int(s[:-1]) * 3600
    elif s.endswith("d"):
        return int(s[:-1]) * 86400
    else:
        return 300  # default 5 min
