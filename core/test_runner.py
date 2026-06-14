"""BAW Telegram Test Runner — Run tests directly from chat.

Provides /test suite for Telegram users to verify system health.
"""
from __future__ import annotations

import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone


def run_all_tests() -> str:
    """Run full test suite (unit + integration + e2e)."""
    app_root = Path(__file__).resolve().parent.parent
    start = time.time()
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-v", "--tb=short", "-q"],
        cwd=app_root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed = time.time() - start
    lines = result.stdout.split("\n")
    # Extract summary line
    summary = "Unknown"
    for line in reversed(lines):
        if "passed" in line or "failed" in line:
            summary = line.strip()
            break
    status = "✅" if result.returncode == 0 else "❌"
    return (
        f"{status} **Full Test Suite**\n"
        f"⏱️ {elapsed:.1f}s\n"
        f"📊 {summary}\n"
        f"```\n{result.stdout[-800:]}\n```"
    )


def run_unit_tests() -> str:
    """Run unit tests only."""
    app_root = Path(__file__).resolve().parent.parent
    start = time.time()
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/unit", "-v", "--tb=short", "-q"],
        cwd=app_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed = time.time() - start
    status = "✅" if result.returncode == 0 else "❌"
    lines = result.stdout.split("\n")
    summary = "Unknown"
    for line in reversed(lines):
        if "passed" in line or "failed" in line:
            summary = line.strip()
            break
    return (
        f"{status} **Unit Tests**\n"
        f"⏱️ {elapsed:.1f}s\n"
        f"📊 {summary}"
    )


def run_quick_check() -> str:
    """Quick health check — no external deps."""
    checks = []
    baw_dir = Path.home() / ".baw"

    # Config
    cfg = baw_dir / "config.yaml"
    checks.append(("配置檔案", cfg.exists()))

    # SOUL
    soul = baw_dir / "SOUL.md"
    checks.append(("SOUL.md", soul.exists()))

    # Evolve log
    evolve = baw_dir / "evolve" / "behavior.jsonl"
    checks.append(("Evolve 日誌", evolve.exists()))

    # Memory
    mem = baw_dir / "memory.jsonl"
    checks.append(("記憶庫", mem.exists()))

    # API keys
    import os
    checks.append(("DeepSeek API Key", bool(os.getenv("DEEPSEEK_API_KEY"))))
    checks.append(("MiniMax API Key", bool(os.getenv("MINIMAX_API_KEY"))))

    # Disk
    import shutil
    usage = shutil.disk_usage("/")
    disk_ok = (usage.used / usage.total) < 0.95
    checks.append(("磁碟空間", disk_ok))

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    icon = "✅" if passed == total else "❌"

    lines = [f"{icon} **Quick Health Check** ({passed}/{total})"]
    for name, ok in checks:
        lines.append(f"  {'✅' if ok else '❌'} {name}")

    return "\n".join(lines)


def test_config() -> str:
    """Test config system."""
    import yaml
    baw_dir = Path.home() / ".baw"
    cfg = baw_dir / "config.yaml"
    if not cfg.exists():
        return "❌ config.yaml not found"
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        providers = list(data.get("providers", {}).keys())
        models = list(data.get("models", {}).keys())
        return (
            "✅ **Config Test**\n"
            f"  Providers: {', '.join(providers)}\n"
            f"  Models: {', '.join(models)}\n"
            f"  Default: {data.get('models', {}).get('default', 'N/A')}"
        )
    except Exception as e:
        return f"❌ Config parse error: {e}"


def test_evolve() -> str:
    """Test evolve engine."""
    try:
        from core.evolve import analyze, get_evolve_stats, track_tool_call, flush_behavior
        track_tool_call("test", {"x": 1}, True, 0.1)
        flush_behavior()
        stats = get_evolve_stats()
        analysis = analyze(hours_back=24)
        return (
            "✅ **Evolve Test**\n"
            f"  Stats: {stats[:100]}...\n"
            f"  Entries (24h): {analysis.get('total_entries', 0)}"
        )
    except Exception as e:
        return f"❌ Evolve error: {e}"


def test_memory() -> str:
    """Test memory system."""
    try:
        baw_dir = Path.home() / ".baw"
        mem = baw_dir / "memory.jsonl"
        count = 0
        if mem.exists():
            with open(mem, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        count += 1
        return (
            "✅ **Memory Test**\n"
            f"  Entries: {count}\n"
            f"  File: {mem}"
        )
    except Exception as e:
        return f"❌ Memory error: {e}"


def test_watchdog() -> str:
    """Test watchdog system."""
    try:
        from core.watchdog import Watchdog
        wd = Watchdog(Path.home() / ".baw")
        alerts = wd.recent_alerts(hours=24)
        return (
            "✅ **Watchdog Test**\n"
            f"  Alerts (24h): {len(alerts)}\n"
            f"  Status: {'正常' if len(alerts) == 0 else '有異常'}"
        )
    except Exception as e:
        return f"❌ Watchdog error: {e}"


def test_scheduler() -> str:
    """Test scheduler system."""
    try:
        from core.scheduler import Scheduler
        sched = Scheduler(Path.home() / ".baw")
        tasks = sched.list_tasks()
        return (
            "✅ **Scheduler Test**\n"
            f"  Tasks: {len(tasks)}\n"
            f"  Running: {sched._running}"
        )
    except Exception as e:
        return f"❌ Scheduler error: {e}"


def test_git() -> str:
    """Test git status."""
    app_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=app_root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    clean = result.returncode == 0 and not result.stdout.strip()
    return (
        f"{'✅' if clean else '⚠️'} **Git Test**\n"
        f"  Status: {'clean' if clean else 'dirty'}\n"
        f"  Changes: {len(result.stdout.strip().split(chr(10))) if result.stdout.strip() else 0}"
    )


# ── Dispatcher ──────────────────────────────────────────────

_TEST_COMMANDS = {
    "all": run_all_tests,
    "full": run_all_tests,
    "unit": run_unit_tests,
    "quick": run_quick_check,
    "health": run_quick_check,
    "config": test_config,
    "evolve": test_evolve,
    "memory": test_memory,
    "watchdog": test_watchdog,
    "scheduler": test_scheduler,
    "git": test_git,
}


def dispatch_test_command(subcmd: str) -> str:
    """Dispatch a /test sub-command."""
    subcmd = subcmd.lower().strip()
    if not subcmd or subcmd in ("help", "h", "?"):
        return _test_help()
    handler = _TEST_COMMANDS.get(subcmd)
    if handler:
        try:
            return handler()
        except Exception as e:
            return f"❌ Test `{subcmd}` failed: {e}"
    return f"❌ Unknown test: `{subcmd}`\n{_test_help()}"


def _test_help() -> str:
    return (
        "🧪 **BAW Test Suite**\n\n"
        "`/test` or `/test quick` — 快速健康檢查\n"
        "`/test all` — 運行全部測試 (約 10s)\n"
        "`/test unit` — 僅運行單元測試\n"
        "`/test config` — Config 系統檢查\n"
        "`/test evolve` — Evolve 引擎檢查\n"
        "`/test memory` — Memory 系統檢查\n"
        "`/test watchdog` — Watchdog 檢查\n"
        "`/test scheduler` — Scheduler 檢查\n"
        "`/test git` — Git 狀態檢查"
    )
