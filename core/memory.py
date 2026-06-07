"""
BAW — Unified Memory Store
JSONL append-only, with scoring and search.
Agent sees one API: remember() + search()
"""

import json
import time
import re
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone


class MemoryStore:
    def __init__(self, data_dir: Path):
        self.store_path = data_dir / "memory" / "store.jsonl"
        self.edges_path = data_dir / "memory" / "edges.json"
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: list[dict] = []
        self._load()

    def _load(self):
        """Load all entries from JSONL into cache."""
        self._cache = []
        if not self.store_path.exists():
            return
        for line in self.store_path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    self._cache.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def _save(self, entry: dict):
        """Append one entry to JSONL."""
        with open(self.store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _get_edges(self) -> dict:
        """Load edge relationships."""
        if self.edges_path.exists():
            return json.loads(self.edges_path.read_text())
        return {"edges": []}

    def remember(self, content: str, tags: list[str] | None = None, source: str = "user") -> dict:
        """Store a new memory entry with initial score 0.50."""
        entry = {
            "id": f"mem_{int(time.time() * 1000)}",
            "content": content,
            "type": "note",
            "tags": tags or [],
            "source": source,
            "created": datetime.now(timezone.utc).isoformat(),
            "last_accessed": datetime.now(timezone.utc).isoformat(),
            "access_count": 1,
            "score": 0.50,
        }
        self._cache.append(entry)
        self._save(entry)

        # Boost related memories via associative spread
        self._associative_boost(content)

        return {"id": entry["id"], "score": entry["score"]}

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memory by keyword, sorted by score (descending)."""
        query = query.lower()
        results = []
        
        for entry in self._cache:
            content = entry.get("content", "").lower()
            # Simple keyword match (will improve with FTS later)
            if query in content:
                # Boost score on access
                entry["access_count"] += 1
                entry["score"] = min(1.0, entry["score"] + 0.05)
                entry["last_accessed"] = datetime.now(timezone.utc).isoformat()
                
                results.append({
                    "id": entry["id"],
                    "content": entry["content"],
                    "score": entry["score"],
                    "tags": entry.get("tags", []),
                    "created": entry["created"],
                    "access_count": entry["access_count"],
                })
                
                # Associative boost: also boost related entries
                self._associative_boost(entry["content"])
        
        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def _associative_boost(self, content: str):
        """Boost related memories via keyword overlap."""
        words = set(re.findall(r'\w+', content.lower()))
        for entry in self._cache:
            entry_words = set(re.findall(r'\w+', entry.get("content", "").lower()))
            overlap = words & entry_words
            if overlap and len(overlap) >= 2:  # 2+ shared keywords = related
                boost = 0.02 * (len(overlap) / max(len(words), 1))
                entry["score"] = min(1.0, entry["score"] + boost)

    def decay(self):
        """Decay scores for old entries. Call daily."""
        now = time.time()
        for entry in self._cache:
            last_access = entry.get("last_accessed", entry["created"])
            try:
                last_ts = datetime.fromisoformat(last_access).timestamp()
            except (ValueError, TypeError):
                last_ts = now
            hours_since = (now - last_ts) / 3600
            
            if hours_since > 168:  # 7 days
                entry["score"] = max(0.0, entry["score"] - 0.05)
            elif hours_since > 24:  # 1 day
                entry["score"] = max(0.0, entry["score"] - 0.01)

    def stats(self) -> dict:
        """Return memory statistics."""
        if not self._cache:
            return {"total": 0, "avg_score": 0, "high_score": 0}
        scores = [e.get("score", 0) for e in self._cache]
        return {
            "total": len(self._cache),
            "avg_score": round(sum(scores) / len(scores), 2),
            "high_score": round(max(scores), 2),
            "low_score": round(min(scores), 2),
        }
