"""BAW — Canonical Path Resolver

Single source of truth for every path BAW code touches. Eliminates the
class of bug where sub-agents (or me) hardcode ``/home/baw/baw`` or
``~/baw`` and miss because of container-vs-host, Docker-mount, or
chdir differences.

Resolution rules:
  1. ``$BAW_HOME`` env var if set (lets tests / Docker override)
  2. ``Path(__file__).resolve().parent.parent`` — the repo root
     (works inside and outside Docker)
  3. ``Path.home() / "baw"`` — fallback for the rare case where
     the file is moved but HOME is set correctly

Every getter returns a ``pathlib.Path`` and creates the parent directory
on demand. NEVER hardcode a BAW path anywhere else — call these.
"""
from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache

_REPO_ROOT_HINT = "BAW_HOME"  # env var to override
_KNOWN_REPO_MARKERS = ("config.sample.yaml", "cli", "core", "tools", "data")


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Return the BAW source-code root (where cli/, core/, tools/, data/ live)."""
    env = os.environ.get(_REPO_ROOT_HINT)
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p

    # Heuristic 1: walk up from this file until we see the known repo markers
    here = Path(__file__).resolve().parent
    for ancestor in [here, *here.parents]:
        if all((ancestor / m).exists() for m in _KNOWN_REPO_MARKERS):
            return ancestor

    # Heuristic 2: ~/baw
    p = Path.home() / "baw"
    if p.is_dir():
        return p

    # Last resort: relative to CWD
    return Path.cwd()


@lru_cache(maxsize=1)
def runtime_home() -> Path:
    """Return the BAW runtime data root (config, memory, sessions, todos)."""
    env = os.environ.get("BAW_RUNTIME_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    p = Path.home() / ".baw"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Repo-root subdirs ─────────────────────────────────────────

def data_dir() -> Path:
    """Persistent datasets scraped/built by BAW (``~/baw/data/``)."""
    p = repo_root() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def docs_dir() -> Path:
    p = repo_root() / "docs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def skills_dir() -> Path:
    """Source-tree skill recipes (markdown + code)."""
    return repo_root() / "skills"


def tools_dir() -> Path:
    return repo_root() / "tools"


def cli_dir() -> Path:
    return repo_root() / "cli"


# ── Runtime subdirs ────────────────────────────────────────────

def config_path() -> Path:
    return runtime_home() / "config.yaml"


def env_path() -> Path:
    return runtime_home() / ".env"


def memory_dir() -> Path:
    p = runtime_home() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sessions_dir() -> Path:
    p = runtime_home() / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def todos_dir() -> Path:
    p = runtime_home() / "todos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tts_cache_dir() -> Path:
    p = runtime_home() / "tts_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Self-introspection (so BAW can verify its own location) ────

def self_check() -> dict:
    """Return a dict proving BAW can locate all its own files.

    Designed to be called at boot AND from the ``baw self-test`` CLI. If
    any required path is missing, the report says so. BAW should refuse
    to declare a 'self-build' task successful when this check fails.
    """
    report = {
        "repo_root": str(repo_root()),
        "runtime_home": str(runtime_home()),
        "checks": {},
    }
    required = {
        "cli/main.py": cli_dir() / "main.py",
        "core/loop.py": repo_root() / "core" / "loop.py",
        "core/paths.py": repo_root() / "core" / "paths.py",
        "tools/__init__.py": tools_dir() / "__init__.py",
        "data/": data_dir(),
        "config.yaml (runtime)": config_path(),
        "SOUL.md": runtime_home() / "SOUL.md",
    }
    for label, p in required.items():
        report["checks"][label] = {
            "path": str(p),
            "exists": p.exists(),
        }
    report["all_present"] = all(c["exists"] for c in report["checks"].values())
    return report


# ── URL / file helpers (used by self-build tasks) ─────────────

def ensure_data_file(name: str) -> Path:
    """Return a path inside data/ for a given filename, creating parent dirs."""
    p = data_dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
