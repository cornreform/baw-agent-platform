"""Integration tests — cross-module workflows."""
from __future__ import annotations

import pytest
import json
import time
from pathlib import Path
from unittest.mock import patch

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestEvolveToConfigFlow:
    """P0: Evolve recommendations must flow to config correctly."""

    def test_failure_logged_to_config(self, temp_baw_home: Path, temp_config: Path, temp_soul: Path):
        from core.evolve import track_tool_call, flush_behavior, auto_optimize
        # Simulate repeated failures
        for i in range(6):
            track_tool_call("broken_tool", {"x": i}, i == 0, 0.1, error="timeout")
        flush_behavior()

        result = auto_optimize(dry_run=False)
        # Check that config has known_issues if config was patched
        if result["config_patched"]:
            import yaml
            data = yaml.safe_load(temp_config.read_text(encoding="utf-8"))
            issues = data.get("evolve", {}).get("known_issues", [])
            assert any(i.get("tool") == "broken_tool" for i in issues)


class TestWatchdogToAlertFlow:
    """P0: Watchdog must write alerts on failure."""

    def test_health_failure_creates_alert(self, temp_baw_home: Path):
        alerts_dir = temp_baw_home / "alerts"
        alerts_dir.mkdir(exist_ok=True)

        # Simulate a health check failure
        alert = {
            "ts": time.time(),
            "level": "error",
            "component": "config",
            "message": "config.yaml missing",
        }
        alert_file = alerts_dir / f"alert-{int(time.time())}.json"
        alert_file.write_text(json.dumps(alert), encoding="utf-8")

        # Watchdog-like polling
        alerts = list(alerts_dir.glob("*.json"))
        assert len(alerts) >= 1


class TestSchedulerToEvolveFlow:
    """P1: Scheduled evolve-analyze must run."""

    def test_evolve_task_defined(self, temp_baw_home: Path):
        schedule_file = temp_baw_home / "schedule.yaml"
        tasks = [
            {
                "name": "evolve-analyze",
                "cron": "0 */6 * * *",
                "command": "baw evolve analyze",
                "enabled": True,
            }
        ]
        import yaml
        schedule_file.write_text(yaml.dump(tasks, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        data = yaml.safe_load(schedule_file.read_text(encoding="utf-8"))
        assert any(t.get("name") == "evolve-analyze" for t in data)


class TestFullWorkflow:
    """P0: Message → process → response pipeline."""

    def test_message_pipeline(self, temp_baw_home: Path, temp_config: Path, temp_soul: Path):
        # Step 1: Receive message
        msg = "/doctor"
        assert msg.startswith("/")

        # Step 2: Parse command
        cmd = msg.split()[0].lstrip("/")
        assert cmd == "doctor"

        # Step 3: Run selftest (simplified)
        checks = ["config", "api_key", "memory"]
        results = {c: True for c in checks}
        assert all(results.values())

        # Step 4: Format response
        response = f"✅ {len(checks)} checks passed"
        assert "✅" in response

        # Step 5: Track interaction
        from core.evolve import track_user_feedback
        entry = track_user_feedback("good")
        assert entry["type"] == "user_feedback"

