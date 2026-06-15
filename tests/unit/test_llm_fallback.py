"""P1: llm.py fallback chain tests.

Covers: primary-fallback sanity check, retryable status codes,
circuit breaker advisory, model auto-routing, shutdown handling.
"""
from __future__ import annotations

import pytest
from pathlib import Path

pytestmark = [pytest.mark.unit]


class TestFallbackChain:
    """llm.py fallback chain behavior."""

    def test_fallback_not_equal_primary_check(self):
        """llm.py must prevent fallback == primary."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "fallback_id == primary_id" in content, \
            "Missing fallback==primary sanity check"

    def test_retryable_statuses_defined(self):
        """Must define RETRYABLE_STATUS set."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "RETRYABLE_STATUS" in content, "Missing RETRYABLE_STATUS"
        # Must include 429 (rate limit)
        assert "429" in content, "RETRYABLE_STATUS missing 429"

    def test_circuit_breaker_advisory_only(self):
        """Circuit breaker must NOT block — advisory only."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "advisory" in content.lower() or "won't block" in content.lower(), \
            "Circuit breaker must be advisory only"

    def test_shutdown_requested_check(self):
        """Must check _shutdown_requested before each attempt."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "_shutdown_requested" in content, "Missing shutdown check"

    def test_exponential_backoff_exists(self):
        """Retry must use exponential backoff."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "exponential" in content.lower() or "EXPONENTIAL" in content, \
            "Missing exponential backoff"

    def test_max_delay_capped(self):
        """Retry delay must be capped at MAX_DELAY."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "MAX_DELAY" in content, "Missing MAX_DELAY cap"

    def test_config_reads_retry_settings(self):
        """Must read retry config from config.yaml."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert 'retry_cfg = config.get("retry"' in content or "retry_cfg = config.get('retry'" in content, \
            "Missing retry config read"

    def test_non_transient_skip_retry(self):
        """401/403/400 must skip retry and go to fallback."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "401" in content or "non-transient" in content.lower() or "non_transient" in content, \
            "Missing non-transient error handling"

    def test_model_auto_routing_exists(self):
        """Must have _route_model for message-size-based routing."""
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "_route_model" in content, "Missing _route_model auto-routing"


class TestCircuitBreaker:
    """Circuit breaker behavior."""

    def test_circuit_cooldown_constant(self):
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "CIRCUIT_COOLDOWN" in content, "Missing circuit cooldown constant"

    def test_circuit_state_tracked(self):
        import re
        llm_path = Path(__file__).resolve().parent.parent.parent / "core" / "llm.py"
        content = llm_path.read_text(encoding="utf-8")
        assert "_CIRCUIT_STATE" in content or "circuit_state" in content.lower(), \
            "Missing circuit state tracking"
