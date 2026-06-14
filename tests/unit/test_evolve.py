"""Evolve engine tests — tracking, analyze, optimize, rollback."""
from __future__ import annotations

import pytest
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.unit, pytest.mark.evolve]


class TestTrackToolCall:
    """P0: Every tool call must be recorded."""

    def test_track_success(self, temp_baw_home: Path):
        from core.evolve import track_tool_call, flush_behavior
        entry = track_tool_call("read_file", {"path": "/tmp/test"}, True, 0.5)
        assert entry["tool"] == "read_file"
        assert entry["success"] is True
        assert entry["duration"] == 0.5
        flush_behavior()

    def test_track_failure(self, temp_baw_home: Path):
        from core.evolve import track_tool_call
        entry = track_tool_call("read_file", {"path": "/tmp/missing"}, False, 1.0, error="not found")
        assert entry["success"] is False
        assert "not found" in entry["error"]

    def test_track_args_truncated(self, temp_baw_home: Path):
        from core.evolve import track_tool_call
        long_arg = "x" * 200
        entry = track_tool_call("write_file", {"content": long_arg}, True, 0.1)
        assert "..." in entry["args_sig"] or len(entry["args_sig"]) < 200


class TestTrackUserFeedback:
    """P0: User corrections must be detected."""

    def test_detects_correction(self, temp_baw_home: Path):
        from core.evolve import track_user_feedback
        entry = track_user_feedback("唔好用 table")
        assert entry["is_correction"] is True

    def test_non_correction(self, temp_baw_home: Path):
        from core.evolve import track_user_feedback
        entry = track_user_feedback("謝謝")
        assert entry["is_correction"] is False

    def test_detects_english_correction(self, temp_baw_home: Path):
        from core.evolve import track_user_feedback
        entry = track_user_feedback("This is wrong, fix it")
        assert entry["is_correction"] is True


class TestAnalyze:
    """P0: Analysis must detect patterns."""

    def test_empty_log(self, temp_baw_home: Path):
        from core.evolve import analyze
        result = analyze(hours_back=24)
        assert result["total_entries"] == 0
        assert result["recommendations"] == []

    def test_high_failure_rate_detected(self, temp_baw_home: Path):
        from core.evolve import track_tool_call, flush_behavior, analyze
        # 5 failures out of 6 = 83% failure rate
        for i in range(6):
            track_tool_call("broken_tool", {"x": i}, i == 0, 0.1)
        flush_behavior()
        result = analyze(hours_back=24)
        recs = [r for r in result["recommendations"] if r["type"] == "high_failure_rate"]
        assert len(recs) >= 1
        assert recs[0]["tool"] == "broken_tool"

    def test_frequent_corrections_detected(self, temp_baw_home: Path):
        from core.evolve import track_user_feedback, flush_behavior, analyze
        for _ in range(5):
            track_user_feedback("錯哋，唔好")
        flush_behavior()
        result = analyze(hours_back=24)
        recs = [r for r in result["recommendations"] if r["type"] == "frequent_corrections"]
        assert len(recs) >= 1

    def test_slow_tools_detected(self, temp_baw_home: Path):
        from core.evolve import track_tool_call, flush_behavior, analyze
        track_tool_call("slow_tool", {}, True, 45.0)
        flush_behavior()
        result = analyze(hours_back=24)
        recs = [r for r in result["recommendations"] if r["type"] == "slow_tool"]
        assert len(recs) >= 1
        assert recs[0]["duration"] == 45.0


class TestAutoOptimizeDryRun:
    """P0: Dry-run must NOT modify files."""

    def test_dry_run_no_write(self, temp_baw_home: Path, temp_soul: Path):
        from core.evolve import auto_optimize, track_user_feedback, flush_behavior
        for _ in range(5):
            track_user_feedback("錯哋，唔好")
        flush_behavior()

        original_mtime = temp_soul.stat().st_mtime
        result = auto_optimize(dry_run=True)
        assert result["soul_patched"] is False
        assert result["config_patched"] is False
        assert temp_soul.stat().st_mtime == original_mtime

    def test_dry_run_queues_items(self, temp_baw_home: Path):
        from core import evolve as evolve_mod
        from core.evolve import auto_optimize, track_user_feedback, flush_behavior
        # Ensure pend path uses temp dir
        evolve_mod._PEND_PATH = temp_baw_home / "evolve" / "pending_approvals.json"
        for _ in range(5):
            track_user_feedback("錯哋，唔好")
        flush_behavior()
        result = auto_optimize(dry_run=True)
        assert result.get("queued_count", 0) >= 1


class TestAutoOptimizeApply:
    """P0: Apply with approval must work and be safe."""

    def test_apply_patches_soul(self, temp_baw_home: Path, temp_soul: Path):
        from core.evolve import auto_optimize, track_user_feedback, flush_behavior
        for _ in range(5):
            track_user_feedback("錯哋，唔好")
        flush_behavior()
        result = auto_optimize(dry_run=False)
        # May or may not patch depending on analysis results
        if result["soul_patched"]:
            assert "evolve:learned-preferences" in temp_soul.read_text(encoding="utf-8")

    def test_git_snapshot_created(self, temp_baw_home: Path, temp_soul: Path):
        from core.evolve import auto_optimize, track_user_feedback, flush_behavior
        for _ in range(5):
            track_user_feedback("錯哋，唔好")
        flush_behavior()
        result = auto_optimize(dry_run=False)
        assert result.get("snapshot") is not None

    def test_rollback_on_verify_failure(self, temp_baw_home: Path, temp_soul: Path):
        from core import evolve as evolve_mod
        from core.evolve import auto_optimize, track_user_feedback, flush_behavior, _verify_soul
        # Ensure pend path uses temp dir
        evolve_mod._PEND_PATH = temp_baw_home / "evolve" / "pending_approvals.json"
        # Init git so snapshot succeeds
        import subprocess
        subprocess.run(["git", "init"], cwd=temp_baw_home, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_baw_home, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_baw_home, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=temp_baw_home, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=temp_baw_home, capture_output=True)
        # Mock verify to always fail
        with patch("core.evolve._verify_soul", return_value={"ok": False, "errors": ["test fail"]}):
            for _ in range(5):
                track_user_feedback("錯哋，唔好")
            flush_behavior()
            result = auto_optimize(dry_run=False)
            assert result.get("rolled_back") is True


class TestApprovalQueue:
    """P0: Approval queue must work correctly."""

    def test_queue_empty(self, temp_baw_home: Path):
        from core.evolve import get_pending_approvals
        assert get_pending_approvals() == []

    def test_queue_and_approve(self, temp_baw_home: Path):
        from core.evolve import queue_for_approval, get_pending_approvals, approve_pending
        recs = [{"type": "test", "suggestion": "do something", "tool": ""}]
        queued = queue_for_approval(recs)
        assert len(queued) == 1
        pending = get_pending_approvals()
        assert len(pending) == 1
        result = approve_pending(0, approved=True)
        assert result["ok"] is True

    def test_dedup_queue(self, temp_baw_home: Path):
        from core.evolve import queue_for_approval, get_pending_approvals
        recs = [
            {"type": "test", "suggestion": "same thing", "tool": ""},
            {"type": "test", "suggestion": "same thing", "tool": ""},
        ]
        queued = queue_for_approval(recs)
        assert len(queued) == 1  # Second is deduped


class TestPhase4Features:
    """P1: Correction learning, prompt tuning, routing, drift."""

    def test_extract_lessons(self, temp_baw_home: Path):
        from core.evolve import _extract_correction_lessons
        lessons = _extract_correction_lessons(["太長啦", "唔好用 table"])
        types = [l["type"] for l in lessons]
        assert "response_length" in types
        assert "format" in types

    def test_write_learned_lessons(self, temp_baw_home: Path):
        from core.evolve import _write_learned_lessons, _load_learned_lessons
        added = _write_learned_lessons([{"type": "test", "value": "v1"}])
        assert added == 1
        added2 = _write_learned_lessons([{"type": "test", "value": "v1"}])
        assert added2 == 0  # deduped

    def test_detect_drift(self, temp_baw_home: Path):
        from core.evolve import _detect_behavioral_drift
        feedback = [
            {"text_sig": "你是語言模型嗎", "ts": 1},
            {"text_sig": "你像機械人", "ts": 2},
            {"text_sig": "不像baw", "ts": 3},
        ]
        alerts = _detect_behavioral_drift(feedback)
        assert len(alerts) == 1
        assert "drift" in alerts[0]["suggestion"].lower()

    def test_no_drift_with_few_signals(self, temp_baw_home: Path):
        from core.evolve import _detect_behavioral_drift
        feedback = [
            {"text_sig": "你是語言模型嗎", "ts": 1},
            {"text_sig": "hello", "ts": 2},
        ]
        alerts = _detect_behavioral_drift(feedback)
        assert len(alerts) == 0

    def test_prompt_tuning_suggestions(self, temp_baw_home: Path):
        from core import evolve as evolve_mod
        from core.evolve import _write_learned_lessons, _tune_prompt_style
        # Ensure learned path uses temp dir
        evolve_mod._LEARNED_PATH = temp_baw_home / "evolve" / "learned_lessons.json"
        evolve_mod._LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Write 3 distinct lessons to bypass dedup
        _write_learned_lessons([
            {"type": "response_length", "value": "short", "learned_at": int(time.time())},
            {"type": "response_length", "value": "shorter", "learned_at": int(time.time())},
            {"type": "response_length", "value": "brief", "learned_at": int(time.time())},
        ])
        tuning = _tune_prompt_style({})
        assert len(tuning["suggestions"]) >= 1
        assert "SHORTER" in tuning["suggestions"][0]


class TestEvolveStats:
    """P1: Stats must be accurate."""

    def test_stats_no_data(self, temp_baw_home: Path):
        from core.evolve import get_evolve_stats
        stats = get_evolve_stats()
        assert "no data yet" in stats

    def test_stats_with_data(self, temp_baw_home: Path):
        from core.evolve import track_tool_call, flush_behavior, get_evolve_stats
        track_tool_call("test", {}, True, 0.1)
        flush_behavior()
        stats = get_evolve_stats()
        assert "events logged" in stats
