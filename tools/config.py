"""BAW built-in: config — safe config.yaml read/write with validation.

Dotted-path get/set/delete with YAML validation + auto-backup + rollback.
Use this tool instead of raw write_file/patch for any config.yaml modification.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

_CFG_PATH = Path.home() / ".baw" / "config.yaml"
_BACKUP_DIR = Path.home() / ".baw" / "config_backups"


def _ensure_backup_dir():
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _backup_config() -> Path | None:
    """Create a timestamped backup before modification."""
    if not _CFG_PATH.exists():
        return None
    _ensure_backup_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = _BACKUP_DIR / f"config_{ts}.yaml"
    shutil.copy2(_CFG_PATH, backup)
    backup.chmod(0o644)  # ensure backup is writable (source may be 444)
    return backup


def _load_cfg() -> tuple[dict | None, str]:
    """Load config.yaml. Returns (config_dict, error_string)."""
    if not _CFG_PATH.exists():
        return None, f"Config file not found: {_CFG_PATH}"
    try:
        import yaml
        with open(_CFG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if cfg is None:
            cfg = {}
        return cfg, ""
    except Exception as e:
        return None, f"YAML syntax error: {e}"


def _save_cfg(cfg: dict) -> str:
    """Save config dict to YAML file. Returns error string or empty on success."""
    try:
        import yaml
        yaml_text = yaml.dump(cfg, default_flow_style=False, allow_unicode=True)
        # Validate by re-parsing
        yaml.safe_load(yaml_text)

        # Filesystem enforcement: config.yaml is 444 (read-only).
        # Temporarily make writable, write, then lock back.
        was_readonly = False
        if _CFG_PATH.exists():
            st = _CFG_PATH.stat()
            if st.st_mode & 0o222 == 0:  # no write bits set
                was_readonly = True
                _CFG_PATH.chmod(0o644)

        _CFG_PATH.write_text(yaml_text, encoding="utf-8")

        if was_readonly:
            _CFG_PATH.chmod(0o444)

        return ""
    except Exception as e:
        return f"Failed to save config: {e}"


def _navigate(cfg: dict, path: str, create_missing: bool = False) -> tuple[dict | bool, str]:
    """Navigate dotted path like 'providers.minimax.base_url'.
    Returns (parent_dict, last_key) or (False, error).
    """
    keys = path.split(".")
    target = cfg
    for key in keys[:-1]:
        if key not in target:
            if create_missing:
                target[key] = {}
            else:
                return False, f"Path not found: '{path}' (missing '{key}')"
        if not isinstance(target[key], dict):
            return False, f"Cannot traverse into non-dict key '{key}' in path '{path}'"
        target = target[key]
    return target, keys[-1]


# ── Public API ──────────────────────────────────────────────────


def config_get(path: str) -> str:
    """Read a config value by dotted path.

    Args:
        path: Dotted path, e.g. 'model.default', 'providers.minimax.base_url'

    Returns:
        Formatted value or error.
    """
    if not path.strip():
        return "Error: path is required. Example: config_get(path='model.default')"

    cfg, err = _load_cfg()
    if err:
        return f"Config error: {err}"

    parent, last_key = _navigate(cfg, path)
    if parent is False:
        return f"Path not found: '{path}'"

    value = parent.get(last_key, "NOT SET")
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def config_set(path: str, value: str) -> str:
    """Set a config value by dotted path. Auto-validates YAML after write.

    Creates a backup before writing. On validation failure, restores backup.

    Args:
        path: Dotted path, e.g. 'model.default'
        value: Value to set (string — will be parsed: "true"/"false"→bool, "123"→int)

    Returns:
        Confirmation or error.
    """
    if not path.strip():
        return "Error: path and value are required."

    # Parse value type
    parsed: bool | int | float | str = value
    if value.lower() == "true":
        parsed = True
    elif value.lower() == "false":
        parsed = False
    else:
        try:
            parsed = int(value)
        except ValueError:
            try:
                parsed = float(value)
            except ValueError:
                parsed = value  # keep as string

    # Backup
    backup = _backup_config()
    backup_note = f" (backup: {backup.name})" if backup else ""

    cfg, err = _load_cfg()
    if err:
        return f"Cannot read config: {err}"

    parent, last_key = _navigate(cfg, path, create_missing=True)
    if parent is False:
        return f"Error navigating to '{path}'"

    parent[last_key] = parsed

    save_err = _save_cfg(cfg)
    if save_err:
        # Rollback
        if backup and backup.exists():
            shutil.copy2(backup, _CFG_PATH)
            return f"Save failed (rolled back): {save_err}"
        return f"Save failed (no backup to restore): {save_err}"

    return f"config.{path} = {parsed}{backup_note}"


def config_delete(path: str) -> str:
    """Delete a config key by dotted path.

    Args:
        path: Dotted path to delete.

    Returns:
        Confirmation or error.
    """
    if not path.strip():
        return "Error: path is required."

    backup = _backup_config()
    backup_note = f" (backup: {backup.name})" if backup else ""

    cfg, err = _load_cfg()
    if err:
        return f"Cannot read config: {err}"

    parent, last_key = _navigate(cfg, path)
    if parent is False:
        return f"Path not found: '{path}'"

    if last_key not in parent:
        return f"Key not found: '{path}'"

    del parent[last_key]

    save_err = _save_cfg(cfg)
    if save_err:
        if backup and backup.exists():
            shutil.copy2(backup, _CFG_PATH)
            return f"Delete failed (rolled back): {save_err}"
        return f"Delete failed: {save_err}"

    return f"Deleted config.{path}{backup_note}"


def config_validate() -> str:
    """Validate config.yaml syntax without modifying anything.

    Returns:
        'OK' or error detail.
    """
    if not _CFG_PATH.exists():
        return "Config file not found."

    cfg, err = _load_cfg()
    if err:
        return f"INVALID: {err}"

    # Quick structure check
    providers = cfg.get("providers", {})
    provider_count = len(providers)
    model_count = sum(len(p.get("models", [])) for p in providers.values())
    caps = cfg.get("capabilities", {})
    cap_count = len(caps)

    return (
        f"Config valid. {provider_count} providers, {model_count} models, "
        f"{cap_count} capabilities configured."
    )


def config_list_backups() -> str:
    """List config backups."""
    _ensure_backup_dir()
    backups = sorted(_BACKUP_DIR.glob("config_*.yaml"), reverse=True)
    if not backups:
        return "No config backups found."
    lines = [f"{len(backups)} backup(s):"]
    for b in backups[:10]:
        size = b.stat().st_size
        lines.append(f"  {b.name} ({size}B)")
    return "\n".join(lines)


def config_restore(backup_name: str = "") -> str:
    """Restore config from a backup.

    Args:
        backup_name: Backup filename (e.g. 'config_20260615_120000.yaml').
                     If empty, restores latest backup.

    Returns:
        Confirmation or error.
    """
    _ensure_backup_dir()
    if backup_name:
        backup = _BACKUP_DIR / backup_name
        if not backup.exists():
            avail = [b.name for b in sorted(_BACKUP_DIR.glob("config_*.yaml"))]
            return f"Backup not found: {backup_name}\nAvailable: {', '.join(avail[:5])}"
    else:
        backups = sorted(_BACKUP_DIR.glob("config_*.yaml"), reverse=True)
        if not backups:
            return "No backups available."
        backup = backups[0]

    # Save current as backup before restoring
    if _CFG_PATH.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_restore = _BACKUP_DIR / f"config_pre_restore_{ts}.yaml"
        shutil.copy2(_CFG_PATH, pre_restore)

    # Make writable if locked, restore, lock back
    was_readonly = False
    if _CFG_PATH.exists():
        st = _CFG_PATH.stat()
        if st.st_mode & 0o222 == 0:
            was_readonly = True
            _CFG_PATH.chmod(0o644)

    shutil.copy2(backup, _CFG_PATH)

    if was_readonly:
        _CFG_PATH.chmod(0o444)

    # Validate restored config
    _, err = _load_cfg()
    if err:
        return f"Restored config has syntax error: {err}"

    return f"Restored from {backup.name}"


# ── Dispatcher ─────────────────────────────────────────────────


def _dispatcher(action: str, path: str = "", value: str = "",
                backup_name: str = "") -> str:
    actions = {
        "get": lambda: config_get(path),
        "set": lambda: config_set(path, value),
        "delete": lambda: config_delete(path),
        "validate": lambda: config_validate(),
        "backups": lambda: config_list_backups(),
        "restore": lambda: config_restore(backup_name),
    }
    fn = actions.get(action)
    if fn is None:
        avail = ", ".join(actions.keys())
        return f"Error: unknown action '{action}'. Available: {avail}"
    return fn()


TOOL_DEF = {
    "name": "config",
    "description": (
        "Safe config.yaml editor — read/write/delete config values by dotted path. "
        "Automatically backs up before every write and validates YAML syntax after. "
        "On validation failure, auto-restores backup. "
        "Actions: 'get' (read value), 'set' (write value, auto-type parse), "
        "'delete' (remove key), 'validate' (check syntax), "
        "'backups' (list backups), 'restore' (restore from backup). "
        "Use this instead of write_file/patch for any config.yaml modification."
    ),
    "handler": _dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "delete", "validate", "backups", "restore"],
                "description": "What to do with the config.",
            },
            "path": {
                "type": "string",
                "description": "Dotted path, e.g. 'model.default' or 'providers.minimax.base_url'",
            },
            "value": {
                "type": "string",
                "description": "Value to set (for 'set' action). Parsed: true/false→bool, 123→int.",
            },
            "backup_name": {
                "type": "string",
                "description": "Backup filename for 'restore' action.",
            },
        },
        "required": ["action"],
    },
    "risk_level": "medium",
}
