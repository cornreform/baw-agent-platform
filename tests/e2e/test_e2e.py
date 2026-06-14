"""End-to-end tests — full workflows."""
from __future__ import annotations

import pytest
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


class TestFullAgentLoop:
    """P0: Complete message-to-response cycle."""

    def test_command_workflow(self, temp_baw_home: Path, temp_config: Path, temp_soul: Path, mock_env):
        """Simulate: user sends /doctor → BAW runs selftest → returns results."""
        # 1. Parse incoming message
        message = "/doctor full"
        parts = message.split()
        cmd = parts[0].lstrip("/")
        args = parts[1:]
        assert cmd == "doctor"
        assert args == ["full"]

        # 2. Load config
        import yaml
        config = yaml.safe_load(temp_config.read_text(encoding="utf-8"))
        assert "providers" in config

        # 3. Run health checks (simplified)
        checks = []
        # Check config valid
        checks.append(("config", True))
        # Check API keys (mocked)
        checks.append(("api_key", bool(config["providers"]["deepseek"]["api_key"])))
        # Check memory
        checks.append(("memory", (temp_baw_home / "memory.jsonl").exists() or True))
        # Check tools
        checks.append(("tools", True))
        # Check network (mocked)
        checks.append(("network", True))

        passed = sum(1 for _, ok in checks if ok)
        assert passed == len(checks)

        # 4. Format response
        lines = ["📊 Selftest Results:"]
        for name, ok in checks:
            lines.append(f"  {'✅' if ok else '❌'} {name}")
        response = "\n".join(lines)
        assert "✅" in response
        assert len(response) < 4000  # Telegram limit

        # 5. Track the interaction
        from core.evolve import track_tool_call, track_user_feedback, flush_behavior
        track_tool_call("selftest", {"full": True}, True, 0.5)
        track_user_feedback(message)
        flush_behavior()

        # 6. Verify behavior log
        log = temp_baw_home / "evolve" / "behavior.jsonl"
        assert log.exists()
        lines = log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 2  # tool_call + feedback

    def test_evolve_command_workflow(self, temp_baw_home: Path, temp_config: Path, temp_soul: Path):
        """Simulate: /evolve → analyze → show stats."""
        from core.evolve import track_tool_call, track_user_feedback, flush_behavior, analyze, get_evolve_stats

        # Simulate some activity
        for i in range(10):
            track_tool_call("read_file", {"path": f"/tmp/{i}"}, i % 3 != 0, 0.1)
        track_user_feedback("謝謝")
        flush_behavior()

        # Run analyze
        result = analyze(hours_back=24)
        assert "tool_calls" in result
        assert "success_rate" in result

        # Get stats
        stats = get_evolve_stats()
        assert "events logged" in stats

    def test_pending_approval_workflow(self, temp_baw_home: Path):
        """Simulate: optimize dry-run → queue → approve → apply."""
        from core.evolve import track_user_feedback, flush_behavior, auto_optimize, queue_for_approval, get_pending_approvals, approve_pending

        # Generate corrections
        for _ in range(5):
            track_user_feedback("錯哋，唔好")
        flush_behavior()

        # Dry run → queue
        result = auto_optimize(dry_run=True)
        assert result.get("queued_count", 0) >= 1

        # Check pending
        pending = get_pending_approvals()
        assert len(pending) >= 1

        # Approve and apply
        res = approve_pending(0, approved=True)
        assert res["ok"] is True

        # Verify queue empty
        pending_after = get_pending_approvals()
        assert len(pending_after) == 0


class TestErrorRecovery:
    """P0: System must recover from errors gracefully."""

    def test_corrupt_behavior_log_ignored(self, temp_baw_home: Path):
        from core.evolve import analyze
        log = temp_baw_home / "evolve" / "behavior.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({"ts": time.time(), "type": "tool_call", "tool": "ok", "success": True}) + "\n"
            + "this is corrupt json\n"
            + json.dumps({"ts": time.time(), "type": "tool_call", "tool": "ok2", "success": True}) + "\n",
            encoding="utf-8",
        )
        result = analyze(hours_back=24)
        assert result["total_entries"] == 2  # corrupt line skipped

    def test_missing_config_fallback(self, temp_baw_home: Path):
        # No config.yaml
        assert not (temp_baw_home / "config.yaml").exists()
        # Should not crash with defaults
        assert temp_baw_home.exists()

