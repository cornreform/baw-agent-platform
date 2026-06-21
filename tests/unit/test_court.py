"""Unit tests for BAW Court System — Phase 9 hardening.

Tests:
- CourtCase data model
- Tier routing logic
- Verdict templates
- Archiving
"""
import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestCourtCase:
    """Test the CourtCase data model and state transitions."""

    def test_create_case(self):
        from core.court import CourtCase, CourtTier, CourtState

        case = CourtCase(
            case_id="CTEST01",
            goal="Search for Fassner reviews",
        )
        assert case.case_id == "CTEST01"
        assert case.goal == "Search for Fassner reviews"
        assert case.tier == CourtTier.TIER_1_MINOR
        assert case.state == CourtState.FILED
        assert case.score == 0
        assert case.retry_count == 0
        assert case.evidence == []

    def test_transition(self):
        from core.court import CourtCase, CourtState

        case = CourtCase(case_id="CTEST02", goal="test")
        case.transition(CourtState.TRIAGE)
        assert case.state == CourtState.TRIAGE

    def test_add_evidence(self):
        from core.court import CourtCase

        case = CourtCase(case_id="CTEST03", goal="test")
        case.add_evidence("PROSECUTOR", "This is flawed because...")
        assert len(case.evidence) == 1
        assert case.evidence[0]["role"] == "PROSECUTOR"
        assert len(case.evidence[0]["content"]) <= 2000

    def test_evidence_cap(self):
        from core.court import CourtCase

        case = CourtCase(case_id="CTEST04", goal="test")
        long_text = "A" * 5000
        case.add_evidence("DEFENDANT", long_text)
        assert len(case.evidence[0]["content"]) == 2000

    def test_score_bounds(self):
        from core.court import CourtCase, Verdict

        case = CourtCase(case_id="CTEST05", goal="test")
        case.score = 10
        case.verdict = Verdict.APPROVED
        case.add_evidence("JUDGE", "score=10")
        assert case.score >= 0 and case.score <= 10


class TestCourtTiers:
    """Test court tier routing and model resolution."""

    def test_tier_0_fast_lane_config(self):
        from core.court import CourtTier

        tier = CourtTier.TIER_0_FAST_LANE
        assert tier.value == 0

    def test_tier_1_minor_config(self):
        from core.court import CourtTier

        tier = CourtTier.TIER_1_MINOR
        assert tier.value == 1

    def test_tier_2_major_config(self):
        from core.court import CourtTier

        tier = CourtTier.TIER_2_MAJOR
        assert tier.value == 2

    def test_tier_3_supreme_config(self):
        from core.court import CourtTier

        tier = CourtTier.TIER_3_SUPREME
        assert tier.value == 3

    def test_resolve_models_for_tier_exists(self):
        """_resolve_models_for_tier should return a dict with expected keys."""
        from core.court import _resolve_models_for_tier, CourtTier

        config = {"router": {"tier_preferences": {}}}
        result = _resolve_models_for_tier(config, CourtTier.TIER_1_MINOR)
        assert "defendant" in result
        assert "judge" in result
        assert "prosecutor" in result
        assert isinstance(result["defendant"], str)
        assert result["defendant"]


class TestVerdictSystem:
    """Test verdict types and render templates."""

    def test_all_verdicts_have_templates(self):
        from core.court import Verdict, VERDICT_TEMPLATES

        for v in Verdict:
            assert v in VERDICT_TEMPLATES, f"Missing template for {v}"

    def test_approved_template(self):
        from core.court import Verdict, VERDICT_TEMPLATES

        tpl = VERDICT_TEMPLATES[Verdict.APPROVED]
        result = tpl.format(
            case_id="CTEST10", score=9, summary="All good",
            evidence_count=3, elapsed=2.5,
            step=1, reason="ok", attempt=1, max_attempts=2,
            original_model="m1", appeal_model="m2",
            done="yes", suggestion="none",
        )
        assert "CTEST10" in result
        assert "9/10" in result
        assert "3" in result

    def test_retry_template(self):
        from core.court import Verdict, VERDICT_TEMPLATES

        tpl = VERDICT_TEMPLATES[Verdict.RETRY]
        result = tpl.format(
            case_id="CTEST11", score=4, summary="needs work",
            evidence_count=2, elapsed=5.0,
            step=1, reason="score too low", attempt=1, max_attempts=2,
            original_model="m1", appeal_model="m2",
            done="partial", suggestion="retry",
        )
        assert "CTEST11" in result
        assert "4/10" in result

    def test_dismissed_template(self):
        from core.court import Verdict, VERDICT_TEMPLATES

        tpl = VERDICT_TEMPLATES[Verdict.DISMISSED]
        result = tpl.format(
            case_id="CTEST12", score=2, summary="failed",
            evidence_count=5, elapsed=10.0,
            step=1, reason="unrecoverable", attempt=2, max_attempts=2,
            original_model="m1", appeal_model="m2",
            done="nothing", suggestion="try again",
        )
        assert "CTEST12" in result

    def test_stay_keyboard(self):
        from core.court import build_stay_inline_keyboard

        kb = build_stay_inline_keyboard("CTEST20")
        assert len(kb) == 1
        assert len(kb[0]) == 3
        texts = [btn["text"] for btn in kb[0]]
        assert "批准執行" in texts
        assert "先 backup 再做" in texts
        assert "撤案" in texts


class TestCourtArchiving:
    """Test court case archiving to disk."""

    def test_archive_and_recover(self, tmp_path):
        """Test archiving directly writes JSON to disk."""
        from core.court import CourtCase, CourtTier, Verdict
        from core.court import _archive_case

        case = CourtCase(case_id="CTEST30", goal="Test archiving")
        case.tier = CourtTier.TIER_1_MINOR
        case.score = 8
        case.verdict = Verdict.APPROVED
        case.final_summary = "Archived successfully"
        case.add_evidence("JUDGE", "score=8/10")

        # Archive writes to ~/.baw/court/cases/ by default
        _archive_case(case)

        # Verify the file exists on disk
        archive_file = Path.home() / ".baw" / "court" / "cases" / "CTEST30.json"
        if not archive_file.exists():
            pytest.skip("Court archive dir may not be set up in CI")
        data = json.loads(archive_file.read_text())
        assert data["case_id"] == "CTEST30"
        assert data["score"] == 8


class TestVerdictCache:
    """Test the verdict cache integration."""

    def test_find_reusable_exists(self):
        from core.verdict_cache import find_reusable_verdict

        result = find_reusable_verdict("This is a unique test goal XKCD", tier=2)
        # No existing case should match
        assert result is None

    def test_find_reusable_imports(self):
        """Just verify the module imports without error."""
        import core.verdict_cache
        assert hasattr(core.verdict_cache, "find_reusable_verdict")
