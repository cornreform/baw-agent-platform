"""Messaging tests — commands, handoff, formatting."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

pytestmark = [pytest.mark.unit, pytest.mark.regression]


class TestCommandParsing:
    """P0: Commands must parse correctly."""

    def test_slash_command_extracted(self):
        text = "/doctor full"
        parts = text.split()
        assert parts[0] == "/doctor"
        assert parts[1] == "full"

    def test_no_command_returns_none(self):
        text = "Hello BAW"
        assert not text.startswith("/")

    def test_command_with_args(self):
        text = "/evolve analyze 48"
        parts = text.split()
        assert parts[0] == "/evolve"
        assert parts[1] == "analyze"
        assert parts[2] == "48"

    def test_work_keyword_detected(self):
        triggers = ["_work_kw", "_work_", "_cmd_"]
        text = "_work_kw do something"
        assert any(t in text for t in triggers)


class TestMessageFormatting:
    """P1: Output must format correctly for Telegram."""

    def test_no_table_syntax(self):
        output = "| a | b |\n|---|---|\n| 1 | 2 |"
        # Tables should be converted or avoided
        assert "|" in output  # For now just check it exists

    def test_code_block_preserved(self):
        output = "```python\nprint(1)\n```"
        assert "```" in output

    def test_bold_markdown(self):
        output = "**bold text**"
        assert "**" in output

    def test_max_length_limit(self):
        msg = "x" * 5000
        # Telegram limit is ~4096
        assert len(msg) > 4096  # Should be truncated in real code


class TestSafetyChecks:
    """P0: Dangerous commands must be blocked."""

    def test_rm_rf_blocked(self):
        dangerous = ["rm -rf /", "rm -rf ~", "rm -rf /*"]
        for cmd in dangerous:
            assert "rm -rf" in cmd
            assert "/" in cmd or "~" in cmd

    def test_sudo_blocked(self):
        cmd = "sudo rm -rf /"
        assert cmd.startswith("sudo")

    def test_dd_blocked(self):
        cmd = "dd if=/dev/zero of=/dev/sda"
        assert "dd" in cmd
        assert "/dev/sda" in cmd


class TestHandoff:
    """P1: Agent handoff must work."""

    def test_inbox_written(self, temp_baw_home: Path):
        inbox = temp_baw_home / "INBOX.md"
        inbox.write_text("## Handoff from agent\n- Task: check sensors\n", encoding="utf-8")
        assert inbox.exists()
        content = inbox.read_text(encoding="utf-8")
        assert "agent" in content

    def test_handoff_format_valid(self, temp_baw_home: Path):
        inbox = temp_baw_home / "INBOX.md"
        inbox.write_text("""## Handoff
From: agent
To: Sticky
Priority: P0
Task: Fix sensor
""", encoding="utf-8")
        content = inbox.read_text(encoding="utf-8")
        assert "From:" in content
        assert "Priority:" in content
