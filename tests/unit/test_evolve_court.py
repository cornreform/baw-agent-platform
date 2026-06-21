"""Unit tests for BAW Evolution — Phase 10 additions.
"""
import time


class TestCourtScoreAnalysis:
    """Test the court score drift analysis."""

    def test_no_cases(self):
        """Should handle empty case list gracefully."""
        from core.evolve import _analyze_court_scores

        result = _analyze_court_scores(hours_back=168)
        assert "alerts" in result
        assert "score_stats" in result
        assert isinstance(result["alerts"], list)

    def test_empty_db(self, tmp_path):
        """Should not crash even if court module not available."""
        from core.evolve import _analyze_court_scores

        result = _analyze_court_scores(hours_back=168)
        assert result["score_stats"]["total_cases"] >= 0

    def test_imports_successfully(self):
        """Ensure all evolve functions can be imported."""
        from core.evolve import (
            track_tool_call,
            track_user_feedback,
            get_learned_lessons_summary,
            run_weekly_evolution,
            _analyze_court_scores,
        )
        summary = get_learned_lessons_summary()
        assert isinstance(summary, str)


class TestLearnedLessons:
    """Test the learned lessons system."""

    def test_summary_format(self):
        from core.evolve import get_learned_lessons_summary

        summary = get_learned_lessons_summary()
        assert isinstance(summary, str)
        assert len(summary) > 0
        # Should start with [LEARN] tag
        assert summary.startswith("[LEARN]")
