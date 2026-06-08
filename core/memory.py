"""
BAW — Unified Memory Store
JSONL append-only + edges.json graph for associative spread.
Agent sees one API: remember() + search()
Internal: graph-based 2-hop scoring (BAW-PLAN v1)
"""

import json
import time
import re
import random
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone


class MemoryStore:
    _id_counter: int = 0

    def __init__(self, data_dir: Path):
        self.store_path = data_dir / "memory" / "store.jsonl"
        self.edges_path = data_dir / "memory" / "edges.json"
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: list[dict] = []
        self._load()

    # ── persistence ──────────────────────────────────────────────

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

    def _save_edges(self, edges_data: dict):
        """Persist edges.json."""
        self.edges_path.write_text(
            json.dumps(edges_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── graph helpers ────────────────────────────────────────────

    @staticmethod
    def _keywords(text: str) -> set[str]:
        r"""Extract meaningful keywords from text.

        For Latin text: \w+ tokens (filtered for stop words).
        For CJK text: 2-char bigrams (each consecutive pair of CJK chars).
        Combined into a single keyword set for Jaccard comparison.
        """
        result: set[str] = set()

        # 1. Latin/ASCII words via regex
        latin_words = set(re.findall(r'[a-zA-Z0-9_]+', text.lower()))
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "in", "on", "at", "to", "for", "of", "with", "by", "from",
            "and", "or", "but", "not", "this", "that", "these", "those",
            "it", "its", "i", "you", "he", "she", "we", "they",
            "do", "does", "did", "have", "has", "had", "can", "will",
            "would", "could", "should", "may", "might", "shall",
            "about", "into", "through", "during", "before", "after",
            "above", "below", "between", "out", "off", "over", "under",
            "again", "further", "then", "once", "here", "there",
            "when", "where", "why", "how", "all", "each", "every",
            "both", "few", "more", "most", "other", "some", "such",
            "no", "nor", "only", "own", "same", "so", "than", "too",
            "very", "just", "because", "as", "until", "while",
        }
        result.update(w for w in latin_words if w not in stop_words)

        # 2. CJK bigrams (consecutive 2-char windows for Chinese text)
        # CJK Unified Ideographs range: U+4E00–U+9FFF
        cjk_chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
        for i in range(len(cjk_chars) - 1):
            bigram = cjk_chars[i] + cjk_chars[i+1]
            result.add(bigram)

        # 3. Also add single CJK chars that aren't common stop chars
        cjk_stop_chars = {
            '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
            '这', '那', '他', '她', '它', '們', '你', '會', '要', '可',
            '以', '都', '一', '個', '上', '也', '很', '到', '說', '去',
            '能', '與', '對', '等', '多', '之', '為', '所', '而', '被',
            '於', '由', '把', '讓', '從', '向', '將', '沒', '還', '又',
        }
        for c in cjk_chars:
            if c not in cjk_stop_chars:
                result.add(c)

        return result

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        """Jaccard similarity between two keyword sets."""
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)

    def _find_or_create_edge(self, edges: list, source: str, target: str) -> dict | None:
        """Find an existing edge between source and target, or None."""
        for e in edges:
            if (e["source"] == source and e["target"] == target) or \
               (e["source"] == target and e["target"] == source):
                return e
        return None

    def _create_edges_for_new_memory(self, mem_id: str, content: str):
        """Compare new memory with all existing entries and auto-create edges.

        An edge is created when two memories share ≥2 keywords,
        with weight = Jaccard similarity of their keyword sets.
        """
        words = self._keywords(content)
        if len(words) < 2:
            return  # Not enough keywords to form meaningful edges

        edges_data = self._get_edges()
        edges = edges_data["edges"]
        changed = False

        for existing in self._cache:
            if existing["id"] == mem_id:
                continue
            existing_words = self._keywords(existing.get("content", ""))
            overlap = words & existing_words
            if len(overlap) < 1:
                continue

            weight = round(self._jaccard(words, existing_words), 4)
            # Only create if weight meets threshold
            if weight < 0.05:
                continue

            # Check if edge already exists
            existing_edge = self._find_or_create_edge(edges, mem_id, existing["id"])
            if existing_edge:
                # Update weight to max of current and new (boost on re-discovery)
                new_weight = max(existing_edge["weight"], weight)
                existing_edge["weight"] = round(min(1.0, new_weight), 4)
            else:
                edges.append({
                    "source": mem_id,
                    "target": existing["id"],
                    "weight": round(min(1.0, weight), 4),
                })
            changed = True

        if changed:
            self._save_edges(edges_data)

    def _build_adjacency(self, edges: list[dict]) -> dict[str, list[tuple[str, float]]]:
        """Build adjacency list: {mem_id: [(neighbour_id, weight), ...]}"""
        adj: dict[str, list[tuple[str, float]]] = {}
        for e in edges:
            s, t, w = e["source"], e["target"], e.get("weight", 0.5)
            adj.setdefault(s, []).append((t, w))
            adj.setdefault(t, []).append((s, w))
        return adj

    def _associative_spread(self, source_id: str, direct_boost: float = 0.05, indirect_boost: float = 0.02):
        """2-hop score propagation through edges graph.

        Original design (BAW-PLAN):
          - Direct neighbours (1-hop): +0.05 × edge_weight
          - Neighbour-of-neighbour (2-hop): +0.02 × edge_weight × 0.5 (decaying)

        For 2-hop, the total boost = 0.02 × w₁ × w₂ × 0.5
        where w₁ = edge weight source→neighbour, w₂ = edge weight neighbour→n2
        """
        edges_data = self._get_edges()
        edges = edges_data.get("edges", [])
        if not edges:
            return

        adj = self._build_adjacency(edges)
        source_id_set = {source_id}

        # ── 1-hop: direct neighbours ──
        neighbours_1 = adj.get(source_id, [])
        n1_ids = set()
        for nid, w in neighbours_1:
            n1_ids.add(nid)
            boost = round(direct_boost * w, 6)
            if boost <= 0:
                continue
            for entry in self._cache:
                if entry["id"] == nid:
                    entry["score"] = min(1.0, entry["score"] + boost)
                    break

        # ── 2-hop: neighbours of neighbours (decaying) ──
        for nid1, w1 in neighbours_1:
            n2_list = adj.get(nid1, [])
            for nid2, w2 in n2_list:
                if nid2 in source_id_set or nid2 in n1_ids:
                    continue  # skip source and direct neighbours
                boost = round(indirect_boost * w1 * w2 * 0.5, 6)
                if boost <= 0:
                    continue
                for entry in self._cache:
                    if entry["id"] == nid2:
                        entry["score"] = min(1.0, entry["score"] + boost)
                        break

    # ── public API ───────────────────────────────────────────────

    def remember(self, content: str, tags: list[str] | None = None, source: str = "user") -> dict:
        """Store a new memory entry with initial score 0.50.

        Auto-creates edges to related memories (keyword-based Jaccard).
        Runs associative spread to boost neighbours via the edges graph.
        """
        MemoryStore._id_counter = (MemoryStore._id_counter + 1) % 10000
        entry = {
            "id": f"mem_{int(time.time() * 1000000)}_{MemoryStore._id_counter:04d}",
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

        # Create edges to related existing memories
        self._create_edges_for_new_memory(entry["id"], content)

        # Graph-based associative spread (2-hop)
        self._associative_spread(entry["id"])

        return {"id": entry["id"], "score": entry["score"]}

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search memory by keyword, sorted by score (descending).

        Hit entry gets +0.05 score boost.
        Then graph-based associative spread to neighbours (1-hop + 2-hop).
        """
        query = query.lower()
        results = []

        for entry in self._cache:
            content = entry.get("content", "").lower()
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

                # Graph-based associative spread from hit entry
                self._associative_spread(entry["id"])

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

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
        """Return memory statistics, including edge count."""
        if not self._cache:
            return {"total": 0, "avg_score": 0, "high_score": 0, "edges": 0, "avg_degree": 0}
        scores = [e.get("score", 0) for e in self._cache]
        edges_data = self._get_edges()
        edge_count = len(edges_data.get("edges", []))
        avg_deg = round(2 * edge_count / max(len(self._cache), 1), 2) if self._cache else 0
        return {
            "total": len(self._cache),
            "avg_score": round(sum(scores) / len(scores), 2),
            "high_score": round(max(scores), 2),
            "low_score": round(min(scores), 2),
            "edges": edge_count,
            "avg_degree": avg_deg,
        }

    def edge_stats(self) -> str:
        """Return human-readable edge graph summary."""
        edges_data = self._get_edges()
        edges = edges_data.get("edges", [])
        if not edges:
            return "No edges in memory graph."

        adj = self._build_adjacency(edges)
        # Top 5 highest-weight edges
        sorted_edges = sorted(edges, key=lambda e: e.get("weight", 0), reverse=True)[:5]

        lines = [f"📊 Memory Graph: {len(edges)} edges, {len(self._cache)} nodes"]
        lines.append("Top connections:")
        for e in sorted_edges:
            s_content = next((m["content"][:50] for m in self._cache if m["id"] == e["source"]), e["source"])
            t_content = next((m["content"][:50] for m in self._cache if m["id"] == e["target"]), e["target"])
            lines.append(f"  {e['weight']:.3f}  {s_content}… ↔ {t_content}…")
        return "\n".join(lines)

    def compress_old(self, max_age_days: int = 30, min_score: float = 0.15) -> dict:
        """Compress old low-score memories into summary entries.

        Groups similar entries by keyword overlap, creates a compressed
        summary per group, removes originals, rewrites JSONL + edges.
        Returns stats dict.
        """
        import math
        now = time.time()
        cutoff = now - max_age_days * 86400

        # Find candidates: low score + old
        candidates = []
        keep = []
        for entry in self._cache:
            try:
                last_ts = datetime.fromisoformat(entry.get("last_accessed", entry["created"])).timestamp()
            except (ValueError, TypeError):
                last_ts = now
            score = float(entry.get("score", 0))
            if score < min_score and last_ts < cutoff:
                candidates.append(entry)
            else:
                keep.append(entry)

        if not candidates:
            return {"compressed": 0, "groups": 0, "entries_removed": 0}

        # Group by keyword similarity (Jaccard >= 0.3)
        groups = []
        used = set()
        for i, c1 in enumerate(candidates):
            if c1["id"] in used:
                continue
            group = [c1]
            used.add(c1["id"])
            k1 = self._keywords(c1.get("content", ""))
            for j, c2 in enumerate(candidates):
                if c2["id"] in used:
                    continue
                k2 = self._keywords(c2.get("content", ""))
                if self._jaccard(k1, k2) >= 0.3:
                    group.append(c2)
                    used.add(c2["id"])
            if len(group) >= 2:
                groups.append(group)

        if not groups:
            return {"compressed": 0, "groups": 0, "entries_removed": 0}

        # Create compressed entries
        compressed_count = 0
        removed_ids = set()
        import random
        for group in groups:
            contents = [e.get("content", "")[:200] for e in group]
            oldest = min(e["created"] for e in group)
            newest = max(e["created"] for e in group)
            summary = f"[Compressed] {len(group)} entries ({oldest[:10]} to {newest[:10]}): " + " | ".join(contents)
            # Truncate to reasonable length
            if len(summary) > 1000:
                summary = summary[:997] + "..."

            compact = {
                "id": f"cmp_{int(time.time() * 1000)}_{random.randint(100,999)}",
                "content": summary,
                "type": "compressed",
                "tags": ["compressed", "auto"],
                "source": "compress",
                "created": datetime.now(timezone.utc).isoformat(),
                "last_accessed": datetime.now(timezone.utc).isoformat(),
                "access_count": 1,
                "score": round(min_score * 1.5, 3),  # Slightly above archive threshold
            }
            keep.append(compact)
            removed_ids.update(e["id"] for e in group)
            compressed_count += 1

        # Rewrite JSONL
        self._cache = keep
        lines = [json.dumps(e, ensure_ascii=False) for e in keep]
        self.store_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Clear and rebuild edges (skip removed ids)
        edges_data = self._get_edges()
        old_edges = edges_data.get("edges", [])
        new_edges = [
            e for e in old_edges
            if e["source"] not in removed_ids and e["target"] not in removed_ids
        ]
        if len(new_edges) != len(old_edges):
            edges_data["edges"] = new_edges
            self._save_edges(edges_data)

        return {
            "compressed": compressed_count,
            "groups": len(groups),
            "entries_removed": len(removed_ids),
            "remaining": len(keep),
        }
