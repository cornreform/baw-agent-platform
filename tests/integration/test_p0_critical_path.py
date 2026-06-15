"""P0 Critical Path Validation Tests.

Covers: config capability routing, Never Surrender constants, court smoke test.

These tests verify the P0 fixes applied 2026-06-15:
  P0-1: capability routing (image→step-image-edit-2, tts→stepaudio-2.5-tts, vision→MiniMax-M3)
  P0-2: cron daemon (schedule.yaml path)
  P0-3: Never Surrender code enforcement (pursuit/recalc limits)
"""
from __future__ import annotations

import pytest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.integration]


# ═══════════════════════════════════════════════════════════════════
# P0-1: Config Capability Routing
# ═══════════════════════════════════════════════════════════════════

class TestCapabilityRouting:
    """Verify resolve_capability returns correct models after P0-1 fix."""

    @pytest.fixture
    def fixed_config(self) -> dict:
        """Minimal config matching P0-1 fixes."""
        return {
            "model": {"default": "step-3.7-flash"},
            "capabilities": {
                "chat": {"model": "step-3.7-flash"},
                "vision": {"model": "MiniMax-M3"},
                "image_generation": {"model": "step-image-edit-2"},
                "tts": {"model": "stepaudio-2.5-tts"},
                "stt": {
                    "method": "stepfun-asr",
                    "model": "stepaudio-2.5-asr",
                    "base_url": "https://api.stepfun.ai/step_plan/v1",
                    "api_key_env": "STEPFUN_API_KEY",
                },
            },
            "providers": {
                "stepfun": {
                    "api_key_env": "STEPFUN_API_KEY",
                    "base_url": "https://api.stepfun.ai/step_plan/v1",
                    "models": [
                        {"id": "step-3.7-flash", "context_window": 262144, "vision": False},
                        {"id": "step-3.5-flash", "context_window": 131072,
                         "capabilities": ["chat"]},
                        {"id": "step-image-edit-2", "context_window": 131072,
                         "capabilities": ["chat"]},
                        {"id": "stepaudio-2.5-tts", "context_window": 131072,
                         "capabilities": ["chat", "tts"]},
                        {"id": "stepaudio-2.5-asr", "context_window": 131072,
                         "capabilities": ["chat", "stt"]},
                    ],
                },
                "minimax": {
                    "api_key_env": "MINIMAX_API_KEY",
                    "base_url": "https://api.minimax.io/v1",
                    "models": [
                        {"id": "MiniMax-M3", "context_window": 1048576, "vision": True},
                        {"id": "MiniMax-M2.5", "context_window": 1048576, "vision": False},
                    ],
                },
            },
        }

    def test_vision_routes_to_vision_capable_model(self, fixed_config):
        """P0-1: vision MUST NOT route to step-3.7-flash (no vision)."""
        from core.capabilities import resolve_capability
        result = resolve_capability(fixed_config, "vision")
        assert result is not None, "vision capability must resolve"
        assert result["type"] == "model"
        # MiniMax-M3 has vision:true; step-3.7-flash has vision:false
        assert "step-3.7" not in result["id"], \
            f"vision must NOT route to text-only model, got {result['id']}"

    def test_image_generation_routes_to_image_model(self, fixed_config):
        """P0-1: image_generation MUST NOT route to step-3.7-flash."""
        from core.capabilities import resolve_capability
        result = resolve_capability(fixed_config, "image_generation")
        assert result is not None, "image_generation must resolve"
        assert "step-3.7" not in result["id"], \
            f"image_generation must NOT route to text-only model, got {result['id']}"

    def test_tts_routes_to_tts_capable_model(self, fixed_config):
        """P0-1: tts MUST route to a model with tts capability."""
        from core.capabilities import resolve_capability
        result = resolve_capability(fixed_config, "tts")
        assert result is not None, "tts must resolve"
        assert "step-3.7" not in result["id"], \
            f"tts must NOT route to text-only model, got {result['id']}"

    def test_chat_still_routes_to_text_model(self, fixed_config):
        """Chat should still use step-3.7-flash (unchanged)."""
        from core.capabilities import resolve_capability
        result = resolve_capability(fixed_config, "chat")
        assert result is not None
        assert result["id"] == "step-3.7-flash"

    def test_stt_uses_correct_model(self, fixed_config):
        """STT should resolve to stepaudio-2.5-asr model (has stt capability)."""
        from core.capabilities import resolve_capability
        result = resolve_capability(fixed_config, "stt")
        assert result is not None
        # stepaudio-2.5-asr has capabilities: [chat, stt] → matched at step 3
        assert result["type"] == "model"
        assert "stepaudio-2.5-asr" in result["id"] or result["id"] == "stepaudio-2.5-asr"


# ═══════════════════════════════════════════════════════════════════
# P0-3: Never Surrender Code Enforcement
# ═══════════════════════════════════════════════════════════════════

class TestNeverSurrender:
    """Verify Never Surrender constants and behavior in loop.py."""

    def test_pursuit_max_increased(self):
        """_GOAL_PURSUIT_MAX_ATTEMPTS must be >= 5 (was 2)."""
        import re
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        m = re.search(r'_GOAL_PURSUIT_MAX_ATTEMPTS\s*=\s*(\d+)', content)
        assert m, "Cannot find _GOAL_PURSUIT_MAX_ATTEMPTS in loop.py"
        assert int(m.group(1)) >= 5, \
            f"Expected >=5 pursuits, got {m.group(1)}"

    def test_recalc_max_increased(self):
        """_MAX_RECALCULATES must be >= 5 (was 3)."""
        import re
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        m = re.search(r'_MAX_RECALCULATES\s*=\s*(\d+)', content)
        assert m, "Cannot find _MAX_RECALCULATES in loop.py"
        assert int(m.group(1)) >= 5, \
            f"Expected >=5 recalculates, got {m.group(1)}"

    def test_alternative_approach_in_loop(self):
        """loop.py must contain 'alternative approach' logic (Never Surrender)."""
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "alternative approach" in content.lower(), \
            "Never Surrender: loop.py must have alternative approach logic"
        assert "never surrender" in content.lower(), \
            "Never Surrender: loop.py must document NEVER SURRENDER intent"


# ═══════════════════════════════════════════════════════════════════
# P0-2: Cron Daemon Verification
# ═══════════════════════════════════════════════════════════════════

class TestCronDaemon:
    """Verify scheduler infrastructure is in place."""

    def test_schedule_yaml_path_exists(self):
        """schedule.yaml must exist at ~/.baw/schedule.yaml."""
        schedule_path = Path.home() / ".baw" / "schedule.yaml"
        if schedule_path.exists():
            import yaml
            data = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
            assert isinstance(data, list), "schedule.yaml must be a list"
            assert len(data) >= 3, f"Expected >=3 cron jobs, got {len(data)}"
        else:
            pytest.skip("schedule.yaml not found — may be running without cron")

    def test_state_file_has_recent_run(self):
        """schedule_state.json should have timestamp for each job."""
        state_path = Path.home() / ".baw" / "schedule_state.json"
        if not state_path.exists():
            pytest.skip("schedule_state.json not found")
        import json
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert len(state) >= 3, f"Expected >=3 state entries, got {len(state)}"
        # At least one job should have run in the last 7 days
        import time
        from datetime import datetime, timezone
        now = time.time()
        recent = False
        for name, ts_str in state.items():
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
                if now - ts < 7 * 86400:
                    recent = True
                    break
            except (ValueError, TypeError):
                pass
        assert recent, "No cron job has run in the last 7 days"


# ═══════════════════════════════════════════════════════════════════
# Config Integrity
# ═══════════════════════════════════════════════════════════════════

class TestConfigIntegrity:
    """Verify ~/.baw/config.yaml has correct P0 fixes."""

    @pytest.fixture
    def runtime_config(self) -> dict:
        config_path = Path.home() / ".baw" / "config.yaml"
        if not config_path.exists():
            pytest.skip("~/.baw/config.yaml not found")
        import yaml
        return yaml.safe_load(config_path.read_text(encoding="utf-8"))

    def test_no_dead_minimax_block(self, runtime_config):
        """capabilities must NOT contain MiniMax-M3 dead block."""
        caps = runtime_config.get("capabilities", {})
        assert "MiniMax-M3" not in caps, \
            "Dead MiniMax-M3 capabilities block must be removed"

    def test_has_angel_model(self, runtime_config):
        """Adversarial must have both devil and angel models."""
        adv = runtime_config.get("adversarial", {})
        assert "devil_model" in adv, "devil_model missing"
        assert "angel_model" in adv, "angel_model missing — add angel_model: kimi-k2.6"

    def test_court_has_timeout(self, runtime_config):
        """Court config must have timeout_sec."""
        court = runtime_config.get("court", {})
        assert "timeout_sec" in court, "court.timeout_sec missing — add timeout_sec: 120"

    def test_judge_not_older_than_default(self, runtime_config):
        """Court judge must not be an older model than default."""
        court = runtime_config.get("court", {})
        judge = court.get("judge_model", "")
        # step-3.5-flash is older than step-3.7-flash
        assert "3.5" not in judge, \
            f"Judge model {judge} is older than default step-3.7-flash"
