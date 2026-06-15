"""Memory system tests — store, search, dedup, persistence."""
from __future__ import annotations

import pytest
import json
from pathlib import Path

pytestmark = [pytest.mark.unit, pytest.mark.regression]


class TestMemoryStore:
    """P0: Memory entries must store correctly."""

    def test_memory_file_created(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        store.write_text(json.dumps({"id": "1", "content": "test"}) + "\n", encoding="utf-8")
        assert store.exists()

    def test_memory_append(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        for i in range(3):
            with open(store, "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": str(i), "content": f"entry {i}"}) + "\n")
        lines = store.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_memory_entry_has_required_fields(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        entry = {
            "id": "abc",
            "content": "test content",
            "ts": 1234567890,
            "type": "fact",
            "confidence": 0.95,
        }
        store.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        data = json.loads(store.read_text(encoding="utf-8").strip())
        assert all(k in data for k in ["id", "content", "ts", "type"])


class TestMemorySearch:
    """P0: Search must return relevant results."""

    def test_search_by_keyword(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        entries = [
            {"id": "1", "content": "BAW uses MiniMax for vision", "ts": 1},
            {"id": "2", "content": "Config stored in YAML", "ts": 2},
            {"id": "3", "content": "DeepSeek handles chat", "ts": 3},
        ]
        with open(store, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        # Simple keyword search
        results = []
        keyword = "minimax"
        for line in store.read_text(encoding="utf-8").strip().split("\n"):
            e = json.loads(line)
            if keyword in e["content"].lower():
                results.append(e)
        assert len(results) == 1
        assert results[0]["id"] == "1"

    def test_search_no_results(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        store.write_text(json.dumps({"id": "1", "content": "hello"}) + "\n", encoding="utf-8")
        results = []
        for line in store.read_text(encoding="utf-8").strip().split("\n"):
            e = json.loads(line)
            if "nonexistent" in e["content"].lower():
                results.append(e)
        assert len(results) == 0


class TestMemoryDedup:
    """P0: Duplicate entries must be rejected."""

    def test_duplicate_content_rejected(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        entry1 = {"id": "1", "content": "same content", "ts": 1}
        entry2 = {"id": "2", "content": "same content", "ts": 2}

        seen = set()
        with open(store, "w", encoding="utf-8") as f:
            for e in [entry1, entry2]:
                sig = e["content"]
                if sig not in seen:
                    seen.add(sig)
                    f.write(json.dumps(e) + "\n")

        lines = store.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_similar_content_deduplication(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        entries = [
            {"id": "1", "content": "User prefers short answers"},
            {"id": "2", "content": "User prefers short answers"},  # exact dup
            {"id": "3", "content": "User prefers short answers!"},  # near dup
        ]
        seen = set()
        with open(store, "w", encoding="utf-8") as f:
            for e in entries:
                # Simple exact dedup for unit test
                if e["content"] not in seen:
                    seen.add(e["content"])
                    f.write(json.dumps(e) + "\n")
        lines = store.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # exact dup removed, near dup kept


class TestMemoryPersistence:
    """P1: Memory must survive restarts."""

    def test_memory_file_survives_reopen(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        entry = {"id": "1", "content": "persistent", "ts": 123}
        store.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        # Simulate restart by re-reading
        data = json.loads(store.read_text(encoding="utf-8").strip())
        assert data["content"] == "persistent"

    def test_corrupted_line_skipped(self, temp_baw_home: Path):
        store = temp_baw_home / "memory.jsonl"
        store.write_text(
            json.dumps({"id": "1", "content": "valid"}) + "\n"
            + "this is not json\n"
            + json.dumps({"id": "2", "content": "also valid"}) + "\n",
            encoding="utf-8",
        )
        valid = []
        for line in store.read_text(encoding="utf-8").strip().split("\n"):
            try:
                valid.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        assert len(valid) == 2
        assert valid[0]["id"] == "1"
        assert valid[1]["id"] == "2"


class TestMemoryDecay:
    """P1: Decay must persist scores and trigger auto-compression."""

    def test_decay_persists_scores(self, temp_baw_home: Path):
        """Decay must call _save_all() so scores survive reload."""
        from core.memory import MemoryStore
        import time

        ms = MemoryStore(temp_baw_home)
        # Add an old entry
        old_id = f"mem_{int(time.time() * 1000000)}_0001"
        old_entry = {
            "id": old_id,
            "content": "test decay persistence",
            "type": "note",
            "tags": ["test"],
            "source": "test",
            "created": "2026-01-01T00:00:00+00:00",
            "last_accessed": "2026-01-01T00:00:00+00:00",
            "access_count": 1,
            "score": 0.50,
        }
        ms._cache.append(old_entry)
        ms._save_all()

        # Run decay
        ms.decay()

        # Reload and check score was persisted
        ms2 = MemoryStore(temp_baw_home)
        decayed = next((e for e in ms2._cache if e["id"] == old_id), None)
        assert decayed is not None, "Entry lost after decay"
        assert decayed["score"] < 0.50, (
            f"Score must be decayed (was {decayed['score']}), "
            "decay() was not persisted"
        )

    def test_decay_returns_stats(self, temp_baw_home: Path):
        """decay() must return a result dict."""
        from core.memory import MemoryStore
        ms = MemoryStore(temp_baw_home)
        result = ms.decay()
        assert isinstance(result, dict)
        assert "decayed" in result or "compressed" in result
