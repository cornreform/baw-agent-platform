"""BAW Test Suite — Shared fixtures and utilities."""
from __future__ import annotations

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

# ── Ensure BAW root is on path ───────────────────────────────────
_APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_APP_ROOT))

# ── Constants ────────────────────────────────────────────────────

TEST_DATA_DIR = Path(__file__).parent / "fixtures"


# ── Session-scoped fixtures ──────────────────────────────────────

@pytest.fixture(scope="session")
def app_root() -> Path:
    return _APP_ROOT


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return TEST_DATA_DIR


# ── Function-scoped temp environment ─────────────────────────────

@pytest.fixture
def temp_baw_home(tmp_path: Path) -> Generator[Path, None, None]:
    """Create an isolated ~/.baw directory for a single test."""
    baw_dir = tmp_path / ".baw"
    baw_dir.mkdir(parents=True, exist_ok=True)

    # Patch Path.home() to return tmp_path
    with patch.object(Path, "home", return_value=tmp_path):
        yield baw_dir


@pytest.fixture
def temp_config(temp_baw_home: Path) -> Path:
    """Write a minimal valid config.yaml into temp_baw_home."""
    config = {
        "providers": {
            "deepseek": {"api_key": "test-key-ds", "base_url": "https://api.deepseek.com/v1"},
            "minimax": {"api_key": "test-key-mm", "base_url": "https://api.minimax.chat/v1"},
        },
        "models": {
            "default": "deepseek-v4-flash",
            "chat": "deepseek-v4-flash",
        },
        "evolve": {"enabled": True, "known_issues": []},
    }
    cfg_path = temp_baw_home / "config.yaml"
    import yaml
    cfg_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    return cfg_path


@pytest.fixture
def temp_soul(temp_baw_home: Path) -> Path:
    """Write a minimal SOUL.md into temp_baw_home."""
    soul = temp_baw_home / "SOUL.md"
    soul.write_text("# BAW \u2014 Black And White\\n\\nI am BAW.\\n", encoding="utf-8")
    return soul


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set safe environment variables for testing."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-mm-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-tg-token")


# ── Mock helpers ─────────────────────────────────────────────────

@pytest.fixture
def mock_llm_response() -> Generator[MagicMock, None, None]:
    """Mock LLM API responses."""
    with patch("core.llm._post") as mock:
        mock.return_value = {
            "choices": [{"message": {"content": "Mocked LLM response"}}]
        }
        yield mock


@pytest.fixture
def mock_telegram_send() -> Generator[MagicMock, None, None]:
    """Mock Telegram message sending."""
    with patch("core.messaging.telegram.send_message") as mock:
        mock.return_value = {"ok": True, "message_id": 12345}
        yield mock


# ── Assertion helpers ────────────────────────────────────────────

def assert_valid_yaml(path: Path) -> dict:
    import yaml
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text)


def assert_valid_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def assert_file_contains(path: Path, substring: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert substring in text, f"Expected '{substring}' in {path}"


# ── Performance helpers ──────────────────────────────────────────

class Timer:
    """Simple context manager for timing code blocks."""
    def __init__(self):
        self.start: float = 0
        self.elapsed: float = 0

    def __enter__(self):
        import time
        self.start = time.time()
        return self

    def __exit__(self, *args):
        import time
        self.elapsed = time.time() - self.start


@pytest.fixture
def timer() -> Timer:
    return Timer()
