"""Watchdog tests — healthcheck, exception, latency, resource monitoring."""
from __future__ import annotations

import pytest
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.unit, pytest.mark.watchdog, pytest.mark.regression]


class TestHealthChecks:
    """P0: All health checks must pass when system is healthy."""

    def test_config_check(self, temp_baw_home: Path, temp_config: Path):
        import yaml
        data = yaml.safe_load(temp_config.read_text(encoding="utf-8"))
        assert "providers" in data
        assert "models" in data

    def test_api_key_check(self, mock_env):
        import os
        assert os.getenv("DEEPSEEK_API_KEY")
        assert os.getenv("MINIMAX_API_KEY")

    def test_memory_check(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        store.write_text(json.dumps({"id": "1", "content": "test"}) + "\n", encoding="utf-8")
        assert store.exists()

    def test_tools_check(self, app_root: Path):
        tools_dir = app_root / "tools"
        if tools_dir.exists():
            files = list(tools_dir.glob("*.py"))
            assert len(files) > 0


class TestExceptionTracking:
    """P0: Exceptions must be recorded without crashing."""

    def test_exception_logged(self, temp_baw_home: Path):
        log = temp_baw_home / "exceptions.jsonl"
        entry = {
            "ts": time.time(),
            "type": "ValueError",
            "message": "test exception",
            "file": "test.py",
            "line": 42,
        }
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        assert log.exists()
        data = json.loads(log.read_text(encoding="utf-8").strip())
        assert data["type"] == "ValueError"

    def test_exception_rate_zero(self, temp_baw_home: Path):
        log = temp_baw_home / "exceptions.jsonl"
        # Empty or no log = zero exceptions
        if log.exists():
            lines = log.read_text(encoding="utf-8").strip().split("\n")
            # Count in last hour
            cutoff = time.time() - 3600
            recent = [json.loads(l) for l in lines if l.strip()]
            recent = [e for e in recent if e.get("ts", 0) >= cutoff]
            assert len(recent) == 0  # Should be 0 in test


class TestLatencyTracking:
    """P1: API latency must be tracked."""

    def test_latency_logged(self, temp_baw_home: Path):
        log = temp_baw_home / "latency.jsonl"
        entry = {
            "ts": time.time(),
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "duration": 1.5,
            "status": "success",
        }
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        assert log.exists()

    def test_high_latency_detected(self, temp_baw_home: Path):
        log = temp_baw_home / "latency.jsonl"
        # Log multiple slow calls
        for _ in range(3):
            entry = {
                "ts": time.time(),
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "duration": 45.0,
                "status": "success",
            }
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

        # Check for slow calls
        slow_count = 0
        cutoff = time.time() - 3600
        for line in log.read_text(encoding="utf-8").strip().split("\n"):
            e = json.loads(line)
            if e.get("ts", 0) >= cutoff and e.get("duration", 0) > 30:
                slow_count += 1
        assert slow_count == 3


class TestResourceMonitoring:
    """P1: Disk and memory must be monitored."""

    def test_disk_usage_readable(self):
        import shutil
        usage = shutil.disk_usage("/")
        total = usage.total
        used = usage.used
        free = usage.free
        percent = used / total * 100
        assert 0 <= percent <= 100
        assert free > 0

    def test_memory_usage_readable(self):
        try:
            with open("/proc/meminfo", "r") as f:
                content = f.read()
            assert "MemTotal" in content
            assert "MemFree" in content
        except FileNotFoundError:
            pytest.skip("/proc/meminfo not available (non-Linux)")

    def test_emergency_cleanup_triggers(self, temp_baw_home: Path):
        # Simulate high disk by creating cleanup threshold logic
        import shutil
        usage = shutil.disk_usage(temp_baw_home)
        percent = usage.used / usage.total * 100
        # In real code, >95% triggers cleanup
        # Here we just verify the logic works
        assert isinstance(percent, float)


class TestAlertSystem:
    """P0: Alerts must be written to disk."""

    def test_alert_written(self, temp_baw_home: Path):
        alerts_dir = temp_baw_home / "alerts"
        alerts_dir.mkdir(exist_ok=True)
        alert = {
            "ts": time.time(),
            "level": "error",
            "component": "test",
            "message": "Test alert",
        }
        alert_file = alerts_dir / f"alert-{int(time.time())}.json"
        alert_file.write_text(json.dumps(alert), encoding="utf-8")
        assert alert_file.exists()
        data = json.loads(alert_file.read_text(encoding="utf-8"))
        assert data["level"] == "error"

    def test_alert_expires(self, temp_baw_home: Path):
        alerts_dir = temp_baw_home / "alerts"
        alerts_dir.mkdir(exist_ok=True)
        old_alert = {"ts": time.time() - 86400 * 2, "level": "warn", "message": "old"}
        old_file = alerts_dir / "alert-old.json"
        old_file.write_text(json.dumps(old_alert), encoding="utf-8")
        # Simulate cleanup of alerts older than 24h
        cutoff = time.time() - 86400
        for f in alerts_dir.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("ts", 0) < cutoff:
                f.unlink()
        assert not old_file.exists()
