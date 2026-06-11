"""
BAW — Health Check (doctor)
Checks config, deps, Docker, disk, and API keys.
"""
from __future__ import annotations
import os, sys, json, subprocess, shutil
from pathlib import Path
from datetime import datetime


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def _ok(msg: str):
    print(f"  {C.GREEN}✓{C.RESET} {msg}")


def _warn(msg: str):
    print(f"  {C.YELLOW}⚠{C.RESET} {msg}")


def _fail(msg: str):
    print(f"  {C.RED}✗{C.RESET} {msg}")


def _header(title: str):
    w, _ = shutil.get_terminal_size()
    print(f"\n{C.CYAN}{'─' * w}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {title}{C.RESET}")
    print(f"{C.CYAN}{'─' * w}{C.RESET}")


def cmd_doctor(data_dir: Path, fix: bool = False):
    """Run comprehensive health check."""
    errors = 0
    warnings = 0

    _header("BAW Health Check")
    print(f"  {C.DIM}Data dir: {data_dir}{C.RESET}")
    print(f"  {C.DIM}Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}{C.RESET}")

    # 1. Config
    _header("Configuration")
    config_path = data_dir / "config.yaml"
    env_path = data_dir / ".env"
    if config_path.exists():
        _ok(f"config.yaml found ({config_path.stat().st_size:,} bytes)")
        try:
            import yaml
            cfg = yaml.safe_load(config_path.read_text())
            if cfg:
                _ok("config.yaml is valid YAML")
                # Check required sections
                if cfg.get("model", {}).get("default"):
                    _ok(f"Model: {cfg['model']['default']}")
                else:
                    _warn("No model.default configured")
                    warnings += 1
                if cfg.get("providers"):
                    _ok(f"Providers: {', '.join(cfg['providers'].keys())}")
                else:
                    _warn("No providers configured")
                    warnings += 1
                caps = cfg.get("capabilities", {})
                if caps.get("stt"):
                    _ok(f"STT: {caps['stt'].get('method', 'model-based')}")
                else:
                    _warn("No STT capability configured")
                if caps.get("tts"):
                    _ok(f"TTS: {caps['tts'].get('model', '?')}")
            else:
                _fail("Empty config.yaml")
                errors += 1
        except Exception as e:
            _fail(f"config.yaml parse error: {e}")
            errors += 1
    else:
        _fail("config.yaml not found")
        errors += 1

    if env_path.exists():
        _ok(f".env found ({env_path.stat().st_size:,} bytes)")
    else:
        _fail(".env not found")
        errors += 1

    # 2. Required env vars
    _header("API Keys")
    required_keys = ["STEPFUN_API_KEY", "MINIMAX_API_KEY"]
    if env_path.exists():
        env_vars = {}
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
        for key in required_keys:
            val = env_vars.get(key, "")
            if val and len(val) > 8:
                _ok(f"{key}=…{val[-4:]}")
            elif val:
                _warn(f"{key} seems too short ({len(val)} chars)")
                warnings += 1
            else:
                _warn(f"{key} not set")
                warnings += 1

    # 3. Docker
    _header("Docker")
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", "name=baw", "--format", "{{.Names}} {{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "baw-telegram" in r.stdout and "healthy" in r.stdout:
            _ok("baw-telegram container is running and healthy")
        elif "baw-telegram" in r.stdout:
            _warn(f"baw-telegram state: {r.stdout.strip()}")
            warnings += 1
        else:
            _fail("baw-telegram container not running")
            errors += 1
    except FileNotFoundError:
        _fail("Docker not installed")
        errors += 1
    except Exception as e:
        _fail(f"Docker check error: {e}")
        errors += 1

    # 4. Disk space
    _header("Disk")
    try:
        st = os.statvfs(str(data_dir))
        free_gb = st.f_bavail * st.f_frsize / (1024**3)
        if free_gb > 5:
            _ok(f"Free disk: {free_gb:.1f} GB")
        elif free_gb > 1:
            _warn(f"Free disk: {free_gb:.1f} GB (low)")
            warnings += 1
        else:
            _fail(f"Free disk: {free_gb:.1f} GB (critical)")
            errors += 1
    except Exception:
        _warn("Could not check disk space")

    # 5. Python packages
    _header("Python Packages")
    required_pkgs = ["faster-whisper", "httpx", "PyYAML"]
    for pkg in required_pkgs:
        try:
            __import__(pkg.replace("-", "_"))
            _ok(f"{pkg} installed")
        except ImportError:
            if fix:
                _warn(f"{pkg} not found — installing...")
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
                    capture_output=True, text=True, timeout=60,
                )
                if r.returncode == 0:
                    _ok(f"{pkg} installed successfully")
                else:
                    _fail(f"{pkg} install failed: {r.stderr[:100]}")
                    errors += 1
            else:
                _warn(f"{pkg} not found (use --fix to auto-install)")
                warnings += 1

    # 6. Git repo status
    _header("Git Repo")
    try:
        r = subprocess.run(
            ["git", "-C", "/home/user/baw", "status", "--short"],
            capture_output=True, text=True, timeout=5,
        )
        r2 = subprocess.run(
            ["git", "-C", "/home/user/baw", "log", "--oneline", "-1"],
            capture_output=True, text=True, timeout=5,
        )
        branch_r = subprocess.run(
            ["git", "-C", "/home/user/baw", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        commit = r2.stdout.strip()
        branch = branch_r.stdout.strip()
        if r.stdout.strip():
            _warn(f"Uncommitted changes ({len(r.stdout.splitlines())} file(s))")
            warnings += 1
        _ok(f"Branch: {branch} @ {commit[:12] if commit else '?'}")
    except Exception as e:
        _warn(f"Git check: {e}")

    # Summary
    _header("Summary")
    if errors == 0 and warnings == 0:
        print(f"  {C.GREEN}All checks passed ✓{C.RESET}")
    elif errors == 0:
        print(f"  {C.YELLOW}{warnings} warning(s) found — {C.RESET}{C.DIM}run with --fix to auto-resolve some issues{C.RESET}")
    else:
        print(f"  {C.RED}{errors} error(s), {warnings} warning(s) found{C.RESET}")

    return errors == 0
