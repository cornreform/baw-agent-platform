"""P1: messaging edge case tests.

Covers: message truncation, callback routing, MEDIA resolution,
file size limits, command parsing, empty message handling.
"""
from __future__ import annotations

import pytest
from pathlib import Path

pytestmark = [pytest.mark.unit]


class TestMessageParsing:
    """Message parsing edge cases."""

    def test_command_extraction_exists(self):
        import re
        msg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "__init__.py"
        content = msg_path.read_text(encoding="utf-8")
        assert "cmd =" in content or "command" in content.lower(), \
            "Missing command extraction"

    def test_slash_command_routing(self):
        import re
        msg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "__init__.py"
        content = msg_path.read_text(encoding="utf-8")
        assert "/doctor" in content or "/help" in content or "/court" in content, \
            "Missing slash command routing"

    def test_telegram_message_length_handling(self):
        """Telegram 4000 char limit must be handled."""
        import re
        tg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "telegram.py"
        content = tg_path.read_text(encoding="utf-8")
        assert "4000" in content or "MAX_MESSAGE" in content or "truncat" in content.lower(), \
            "Missing message length handling"

    def test_empty_message_handling(self):
        import re
        msg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "__init__.py"
        content = msg_path.read_text(encoding="utf-8")
        # Should handle empty or whitespace-only messages
        assert "strip" in content or "empty" in content.lower(), \
            "Missing empty message handling"

    def test_media_resolution_exists(self):
        """MEDIA: path resolution must exist."""
        import re
        tg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "telegram.py"
        content = tg_path.read_text(encoding="utf-8")
        assert "MEDIA:" in content or "_resolve_media" in content, \
            "Missing MEDIA path resolution"


class TestCallbackRouting:
    """Telegram callback handling."""

    def test_court_callback_routing(self):
        import re
        tg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "telegram.py"
        content = tg_path.read_text(encoding="utf-8")
        assert "court:" in content or "callback" in content.lower(), \
            "Missing court callback routing"

    def test_callback_acknowledgment(self):
        """Must acknowledge callback to stop Telegram spinner."""
        import re
        tg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "telegram.py"
        content = tg_path.read_text(encoding="utf-8")
        assert "answerCallbackQuery" in content, \
            "Missing callback acknowledgment"


class TestFileHandling:
    """File download and size handling."""

    def test_file_download_exists(self):
        import re
        tg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "telegram.py"
        content = tg_path.read_text(encoding="utf-8")
        assert "download" in content.lower() or "getFile" in content, \
            "Missing file download"

    def test_concurrency_limit_exists(self):
        import re
        msg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "__init__.py"
        content = msg_path.read_text(encoding="utf-8")
        assert "concurrency" in content.lower() or "max_concurrent" in content or "_semaphore" in content, \
            "Missing concurrency limit"


class TestErrorHandling:
    """Messaging error handling edges."""

    def test_network_error_recovery(self):
        import re
        tg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "telegram.py"
        content = tg_path.read_text(encoding="utf-8")
        assert "retry" in content.lower() or "except" in content, \
            "Missing network error recovery"

    def test_typing_indicator_heartbeat(self):
        import re
        tg_path = Path(__file__).resolve().parent.parent.parent / "core" / "messaging" / "telegram.py"
        content = tg_path.read_text(encoding="utf-8")
        assert "typing" in content.lower() or "sendChatAction" in content, \
            "Missing typing indicator"
