"""Unit tests for BAW Delivery Log — Phase 6.
"""
import json
import time


class TestDeliveryLog:
    """Test delivery confirmation logging."""

    def test_record_send(self, tmp_path):
        from core.delivery_log import record_send, recent_deliveries

        import core.delivery_log as dl
        _orig = dl._LOG_DIR
        dl._LOG_DIR = tmp_path / "logs"

        # Clean any prior state — delete any existing log
        log_file = dl._LOG_DIR / "delivery.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if log_file.exists():
            log_file.unlink()

        entry_id = record_send("12345", "telegram", "Hello world")
        assert entry_id > 0
        assert isinstance(entry_id, int)

        recent = recent_deliveries(minutes=60, limit=10)
        assert len(recent) == 1
        assert recent[0]["chat_id"] == "12345"
        assert recent[0]["platform"] == "telegram"
        assert recent[0]["status"] == "sent"

        dl._LOG_DIR = _orig  # restore

    def test_record_error(self, tmp_path):
        from core.delivery_log import record_send, record_error, recent_deliveries

        import core.delivery_log as dl
        _orig = dl._LOG_DIR
        dl._LOG_DIR = tmp_path / "logs"

        entry_id = record_send("123", "telegram", "Hello")
        record_error(entry_id, "telegram", "HTTP 403 Forbidden")
        recent = recent_deliveries(minutes=60, limit=10)
        error_entries = [e for e in recent if e.get("status") in ("error", "fatal")]
        assert len(error_entries) >= 1

        dl._LOG_DIR = _orig

    def test_delivery_confirmation(self, tmp_path):
        from core.delivery_log import record_send, record_delivery_confirmation, recent_deliveries

        import core.delivery_log as dl
        _orig = dl._LOG_DIR
        dl._LOG_DIR = tmp_path / "logs"

        entry_id = record_send("123", "telegram", "Hello")
        record_delivery_confirmation(entry_id, "telegram", {"message_id": 42})
        recent = recent_deliveries(minutes=60, limit=10)
        delivered = [e for e in recent if e.get("status") == "delivered"]
        assert len(delivered) >= 1

        dl._LOG_DIR = _orig

    def test_delivery_stats(self, tmp_path):
        from core.delivery_log import record_send, delivery_stats

        import core.delivery_log as dl
        _orig = dl._LOG_DIR
        dl._LOG_DIR = tmp_path / "logs"

        record_send("123", "telegram", "Test msg 1")
        record_send("456", "discord", "Test msg 2")

        stats = delivery_stats(minutes=60)
        assert stats["total_entries"] >= 2
        assert stats["sent"] >= 2

        dl._LOG_DIR = _orig

    def test_prune(self, tmp_path):
        """Test that log is pruned when exceeding max entries."""
        from core.delivery_log import record_send

        import core.delivery_log as dl
        _orig_dir = dl._LOG_DIR
        _orig_max = dl._MAX_ENTRIES

        dl._LOG_DIR = tmp_path / "logs"
        dl._MAX_ENTRIES = 5

        # Write 10 entries
        for i in range(10):
            record_send(str(i), "telegram", f"msg {i}")

        # Check log has <= 5 entries after pruning
        log_file = dl._LOG_DIR / "delivery.jsonl"
        # The log may be pruned on the 10th write, or not yet
        # Check that we have no more than the max
        if log_file.exists():
            lines = log_file.read_text().strip().splitlines()
            assert len(lines) <= dl._MAX_ENTRIES

        # Restore
        dl._LOG_DIR = _orig_dir
        dl._MAX_ENTRIES = _orig_max
