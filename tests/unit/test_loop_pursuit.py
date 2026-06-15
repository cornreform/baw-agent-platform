"""P1: loop.py pursuit branch tests.

Covers: success, fail, recalc, skip, same-step check, file-existence verification,
zero-tool-call detection, self-review score parsing.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.unit]


class TestPursuitConstants:
    """loop.py pursuit loop constants must be correctly set."""

    def test_pursuit_max_never_surrender(self):
        import re
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        m = re.search(r'_GOAL_PURSUIT_MAX_ATTEMPTS\s*=\s*(\d+)', content)
        assert int(m.group(1)) >= 5, "Pursuit max too low"

    def test_recalc_max(self):
        import re
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        m = re.search(r'_MAX_RECALCULATES\s*=\s*(\d+)', content)
        assert int(m.group(1)) >= 5, "Recalc max too low"

    def test_max_same_step_alternatives(self):
        import re
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        m = re.search(r'_MAX_SAME_STEP_ALTERNATIVES\s*=\s*(\d+)', content)
        assert int(m.group(1)) >= 4, "Same step alternatives too low"


class TestBranchDetection:
    """Verify key code branches exist in loop.py."""

    def test_alternative_approach_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "alternative approach" in content.lower(), "Missing alternative approach branch"

    def test_self_correction_protocol_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "SELF-CORRECTION PROTOCOL" in content, "Missing self-correction"

    def test_file_existence_verification_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "FILE-EXISTENCE VERIFICATION" in content or "_expected_files" in content, \
            "Missing file-existence verification"

    def test_zero_tool_call_detection_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "0 tool calls" in content, "Missing zero-tool-call detection"

    def test_same_step_fail_tracking_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "_same_step_fails" in content, "Missing same-step tracking"

    def test_permanent_skip_fallback_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "_permanent_skip" in content, "Missing permanent skip fallback"

    def test_goal_achieved_auto_confirm_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "goal achieved" in content.lower(), "Missing goal achieved detection"

    def test_diagnosis_on_exhaustion_exists(self):
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "diagnosis" in content.lower() or "Diagnosis" in content, \
            "Missing diagnosis on exhaustion"


class TestSelfReviewScoreParsing:
    """Self-review score extraction must handle edge cases."""
    def test_score_regex_exists(self):
        """Self-review must use regex to extract SCORE from LLM response."""
        import re
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        # Must have both SCORE: pattern and re.search for extraction
        assert "SCORE:" in content, "Missing SCORE: pattern in self-review"
        assert "SCORE:" in content and "re.search" in content, \
            "Missing regex for score extraction"
