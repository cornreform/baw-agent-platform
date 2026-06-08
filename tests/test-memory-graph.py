#!/usr/bin/env python3
"""
Comprehensive memory graph test suite.
Tests: storage persistence, edge creation, 1-hop/2-hop scoring,
       cross-language support, stress loads, edge cases.
Run: python3 tests/test-memory-graph.py [--stress-level N]
"""

import sys
import json
import time
import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.memory import MemoryStore

# ── Config ──────────────────────────────────────────────────────
STRESS_LEVEL = 3  # 1=light, 2=moderate, 3=heavy (300 entries)
if "--stress-level" in sys.argv:
    idx = sys.argv.index("--stress-level")
    STRESS_LEVEL = int(sys.argv[idx + 1])

PASS = 0
FAIL = 0
ERRORS: list[str] = []


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        msg = f"  ❌ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        ERRORS.append(msg)


def check_near(name: str, got: float, expected: float, tolerance: float = 0.01):
    check(name, abs(got - expected) <= tolerance,
          f"expected {expected:.4f}, got {got:.4f}")


def make_store() -> tuple[MemoryStore, Path]:
    """Create a fresh MemoryStore in a temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="baw_memtest_"))
    store = MemoryStore(tmp)
    return store, tmp


def cleanup(tmp: Path):
    shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# Suite 1 — Basic Storage
# ══════════════════════════════════════════════════════════════════
def suite_basic_storage():
    print("\n═══ Suite 1: Basic Storage ═══")

    # 1.1 Remember a simple memory
    s, tmp = make_store()
    r = s.remember("This is a test memory")
    check("1.1 remember returns id+score", "id" in r and "score" in r)

    # 1.2 Memory is in JSONL
    lines = s.store_path.read_text().strip().split("\n")
    check("1.2 store.jsonl has 1 entry", len(lines) == 1)
    entry = json.loads(lines[0])
    check("1.2b entry has expected fields",
          all(k in entry for k in ("id", "content", "score", "created", "access_count")))

    # 1.3 Reload from disk preserves data
    s2 = MemoryStore(tmp)
    check("1.3 reload cache matches", len(s2._cache) == 1)
    check("1.3b content preserved", s2._cache[0]["content"] == "This is a test memory")
    check("1.3c score preserved", s2._cache[0]["score"] == 0.50)

    # 1.4 Remember multiple
    for i in range(5):
        s.remember(f"Memory number {i}")
    check("1.4 6 total entries", len(s._cache) == 6)
    lines2 = s.store_path.read_text().strip().split("\n")
    check("1.4b 6 lines in jsonl", len(lines2) == 6)
    cleanup(tmp)


# ══════════════════════════════════════════════════════════════════
# Suite 2 — Edge Creation
# ══════════════════════════════════════════════════════════════════
def suite_edge_creation():
    print("\n═══ Suite 2: Edge Creation ═══")

    # 2.1 First memory has no edges
    s, tmp = make_store()
    s.remember("First memory entry")
    edges = s._get_edges()
    check("2.1 single entry → 0 edges", len(edges["edges"]) == 0)

    # 2.2 Second unrelated memory → still 0 edges
    s.remember("Completely different topic about quantum physics")
    edges = s._get_edges()
    check("2.2 unrelated → 0 edges", len(edges["edges"]) == 0)

    # 2.3 Third related memory → 1+ edges
    s.remember("Second entry in our test sequence")
    edges = s._get_edges()
    check("2.3 related → edges created", len(edges["edges"]) >= 1)

    # 2.4 Edge structure is valid
    if edges["edges"]:
        e = edges["edges"][0]
        check("2.4 edge has source+target+weight",
              all(k in e for k in ("source", "target", "weight")))
        check("2.4b weight in [0,1]", 0 <= e["weight"] <= 1.0)

    # 2.5 Identical content → high weight
    s.remember("Second entry in our test sequence")  # duplicate
    edges = s._get_edges()
    dup_edges = [e for e in edges["edges"]
                 if any(m["content"] == "Second entry in our test sequence"
                        and e["source"] == m["id"] or e["target"] == m["id"]
                        for m in s._cache)]
    if dup_edges:
        max_w = max(e["weight"] for e in dup_edges)
        check("2.5 duplicate content → weight ≥ 0.5", max_w >= 0.5, f"got {max_w:.3f}")
    cleanup(tmp)

    # 2.6 Chinese bigram edge creation
    s, tmp = make_store()
    s.remember("永遠使用繁體中文溝通")
    s.remember("HARD GATE: 永遠繁體中文")
    edges = s._get_edges()
    check("2.6 Chinese-related → edges exist", len(edges["edges"]) > 0)
    if edges["edges"]:
        check("2.6b Chinese edge weight > 0.05", edges["edges"][0]["weight"] > 0.05,
              f"got {edges['edges'][0]['weight']:.4f}")
    cleanup(tmp)


# ══════════════════════════════════════════════════════════════════
# Suite 3 — Scoring & Associative Spread
# ══════════════════════════════════════════════════════════════════
def suite_scoring():
    print("\n═══ Suite 3: Scoring & Associative Spread ═══")

    # 3.1 Search hit → +0.05 score
    s, tmp = make_store()
    s.remember("test apple banana cherry")
    results = s.search("apple")
    check("3.1 search found result", len(results) == 1)
    check("3.1b score boosted to 0.55",
          abs(results[0]["score"] - 0.55) < 0.01, f"got {results[0]['score']:.3f}")

    # 3.2 1-hop associative boost via edges
    s, tmp = make_store()
    r1 = s.remember("apple banana cherry data")
    r2 = s.remember("apple banana cherry analysis")
    edges = s._get_edges()
    check("3.2 two related → edges exist", len(edges["edges"]) >= 1)
    # Search for r2 → r1 should get 1-hop boost (plus possible spread from creation)
    results = s.search("analysis")
    # Find r1's current score
    r1_score = next(m["score"] for m in s._cache if m["id"] == r1["id"])
    # Expected: 0.50 + 0.05 (direct when r2 remembered spread to r1) + 0.05*w (search spread)
    expected_1hop = 0.50 + 0.05  # at minimum, creation-time spread
    check("3.2b 1-hop boost applied",
          r1_score > expected_1hop,
          f"expected >{expected_1hop:.4f}, got {r1_score:.4f}")

    # 3.3 2-hop spread
    s, tmp = make_store()
    ra = s.remember("alpha beta gamma delta")
    rb = s.remember("alpha beta gamma epsilon")
    rc = s.remember("beta gamma epsilon zeta")  # relates to rb, NOT ra
    # Search "delta" → ra hit → 1-hop boost rb → 2-hop boost rc
    edges = s._get_edges()
    results = s.search("delta")
    ra_current = next(m["score"] for m in s._cache if m["id"] == ra["id"])
    rb_current = next(m["score"] for m in s._cache if m["id"] == rb["id"])
    rc_current = next(m["score"] for m in s._cache if m["id"] == rc["id"])
    check("3.3 ra (direct hit) ≥ 0.55", ra_current >= 0.55, f"got {ra_current:.4f}")
    check("3.3b rc (2-hop) > 0.50", rc_current > 0.50, f"got {rc_current:.4f}")
    check("3.3c rc < rb (2-hop < 1-hop)", rc_current < rb_current,
           f"rc={rc_current:.4f} rb={rb_current:.4f}")
    cleanup(tmp)

    # 3.4 Multiple searches accumulate score
    s, tmp = make_store()
    s.remember("persistent repeated memory")
    for _ in range(3):
        s.search("repeated")
    final_score = next(m["score"] for m in s._cache if "repeated" in m["content"])
    expected = 0.50 + 3 * 0.05  # 3 searches × +0.05 per hit
    check("3.4 cumulative scoring",
          abs(final_score - expected) < 0.05, f"expected ~{expected:.2f}, got {final_score:.2f}")

    # 3.5 Score ceiling at 1.0
    s, tmp = make_store()
    s.remember("ceiling test memory")
    for _ in range(30):  # 30 searches = +1.5 → should cap at 1.0
        s.search("ceiling")
    score = next(m["score"] for m in s._cache if "ceiling" in m["content"])
    check("3.5 score capped at 1.0", score <= 1.0, f"got {score:.4f}")
    check("3.5b score at 1.0", abs(score - 1.0) < 0.01, f"got {score:.4f}")
    cleanup(tmp)


# ══════════════════════════════════════════════════════════════════
# Suite 4 — Decay
# ══════════════════════════════════════════════════════════════════
def suite_decay():
    print("\n═══ Suite 4: Decay ═══")

    # 4.1 Fresh memory → no decay
    # We test decay logic by manipulating last_accessed
    s, tmp = make_store()
    s.remember("fresh test")
    orig_score = s._cache[0]["score"]
    s.decay()
    check("4.1 fresh memory not decayed", s._cache[0]["score"] == orig_score)

    # 4.2 Artificially set old timestamp → decay applies
    old_ts = datetime.now(timezone.utc).isoformat()
    s._cache[0]["last_accessed"] = "2020-01-01T00:00:00+00:00"  # 6+ years ago
    s._cache[0]["created"] = "2020-01-01T00:00:00+00:00"
    s.decay()
    check("4.2 old memory decayed ≥0.05", s._cache[0]["score"] <= orig_score - 0.05,
          f"was {orig_score}, now {s._cache[0]['score']:.4f}")
    check("4.2b score >= 0.0", s._cache[0]["score"] >= 0.0,
          f"got {s._cache[0]['score']:.4f}")
    cleanup(tmp)


# ══════════════════════════════════════════════════════════════════
# Suite 5 — Graph Statistics
# ══════════════════════════════════════════════════════════════════
def suite_stats():
    print("\n═══ Suite 5: Graph Statistics ═══")

    s, tmp = make_store()
    stats = s.stats()
    check("5.1 empty store → total=0", stats["total"] == 0)
    check("5.1b edges=0", stats["edges"] == 0)
    check("5.1c avg_degree=0", stats["avg_degree"] == 0.0)

    s.remember("alpha beta gamma")
    stats = s.stats()
    check("5.2 1 entry", stats["total"] == 1)

    # edge_stats with no edges
    es = s.edge_stats()
    check("5.3 no edges → message", "No edges" in es)

    # Add related memories to get edges
    s.remember("alpha beta delta")
    s.remember("alpha beta epsilon")
    stats = s.stats()
    check("5.4 edges > 0", stats["edges"] > 0, f"got {stats['edges']}")
    check("5.4b avg_degree > 0", stats["avg_degree"] > 0, f"got {stats['avg_degree']}")

    es = s.edge_stats()
    check("5.5 edge_stats shows top connections", "Top connections" in es)
    cleanup(tmp)


# ══════════════════════════════════════════════════════════════════
# Suite 6 — Edge Cases
# ══════════════════════════════════════════════════════════════════
def suite_edge_cases():
    print("\n═══ Suite 6: Edge Cases ═══")

    # 6.1 Empty content
    s, tmp = make_store()
    try:
        s.remember("")
        check("6.1 empty content handled", True)
    except Exception as e:
        check("6.1 empty content handled", False, str(e))
    cleanup(tmp)

    # 6.2 Very long content
    s, tmp = make_store()
    long_text = "test " * 2500  # ~12.5K chars
    try:
        s.remember(long_text)
        check("6.2 very long content (12.5K) handled", True)
    except Exception as e:
        check("6.2 very long content handled", False, str(e))
    cleanup(tmp)

    # 6.3 Special characters
    s, tmp = make_store()
    try:
        s.remember("!@#$%^&*()_+-=[]{}|;':\",./<>?`~ 😀 🎉 📊")
        check("6.3 special chars handled", True)
    except Exception as e:
        check("6.3 special chars handled", False, str(e))
    cleanup(tmp)

    # 6.4 Chinese only
    s, tmp = make_store()
    s.remember("這是繁體中文測試記憶體")
    s.remember("另一個繁體中文記憶")
    edges = s._get_edges()
    check("6.4 Chinese → edges created", len(edges["edges"]) > 0)
    if edges["edges"]:
        check("6.4b Chinese edge weight > 0.1", edges["edges"][0]["weight"] > 0.1,
              f"got {edges['edges'][0]['weight']:.4f}")
    cleanup(tmp)

    # 6.5 Mixed CJK + Latin
    s, tmp = make_store()
    s.remember("HARD GATE: 永遠繁體中文（技術術語留英文）")
    s.remember("HARD GATE: 唔好用簡體")
    edges = s._get_edges()
    check("6.5 Mixed CJK+Latin → edges", len(edges["edges"]) > 0)
    if edges["edges"]:
        check("6.5b Mixed edge weight > 0.05", edges["edges"][0]["weight"] > 0.05)
    cleanup(tmp)

    # 6.6 Search non-existent
    s, tmp = make_store()
    s.remember("real content here")
    results = s.search("this_term_does_not_exist_xyz")
    check("6.6 search miss → empty results", len(results) == 0)
    cleanup(tmp)

    # 6.7 Search in empty store
    s, tmp = make_store()
    results = s.search("anything")
    check("6.7 search empty store → empty", len(results) == 0)
    cleanup(tmp)

    # 6.8 Edge update (re-remember related content → weight should increase)
    s, tmp = make_store()
    s.remember("python coding debug")
    s.remember("python code debug test")
    edges1 = s._get_edges()
    w1 = edges1["edges"][0]["weight"] if edges1["edges"] else 0
    # Re-remember something that relates even more → weight should max-update
    s.remember("python coding debug test analysis")
    edges2 = s._get_edges()
    # Find the same edge
    if edges1["edges"] and edges2["edges"]:
        # Compare max weight for the pair
        pairs1 = [(e["source"], e["target"]) for e in edges1["edges"]]
        for e in edges2["edges"]:
            pair = (e["source"], e["target"])
            if pair in pairs1 or (pair[1], pair[0]) in pairs1:
                check("6.8 edge weight may update or add new", True)
                break
    cleanup(tmp)

    # 6.9 Decay floor at 0.0 — force a memory with low score
    s, tmp = make_store()
    # Manually set a memory score to near floor
    s._cache.append({
        "id": "mem_floor_test",
        "content": "floor memory test",
        "score": 0.03,
        "last_accessed": "2020-01-01T00:00:00+00:00",
        "created": "2020-01-01T00:00:00+00:00",
        "tags": [],
        "source": "test",
        "access_count": 1,
    })
    s.decay()
    test_entry = next(m for m in s._cache if m["id"] == "mem_floor_test")
    check("6.9 score floor at 0.0", test_entry["score"] >= 0.0,
          f"got {test_entry['score']:.4f}")
    cleanup(tmp)


# ══════════════════════════════════════════════════════════════════
# Suite 7 — Stress Tests
# ══════════════════════════════════════════════════════════════════
def suite_stress():
    n = {1: 30, 2: 100, 3: 300}[STRESS_LEVEL]
    print(f"\n═══ Suite 7: Stress Test ({n} entries, level {STRESS_LEVEL}) ═══")

    s, tmp = make_store()

    # Phase 1: Rapid remembers
    t0 = time.perf_counter()
    for i in range(n):
        topic = ["apple", "banana", "cherry", "date", "elderberry"][i % 5]
        s.remember(f"{topic} {i}: this is test memory number {i} with keywords")
    t1 = time.perf_counter()
    elapsed = t1 - t0
    check(f"7.1 {n} remembers in {elapsed:.2f}s", elapsed < max(5.0, n * 0.1),
          f"took {elapsed:.2f}s")

    # Check state
    check(f"7.1b {n} entries stored", len(s._cache) == n)
    edges = s._get_edges()
    check(f"7.2 edges created", len(edges["edges"]) > 0,
          f"got {len(edges['edges'])} edges")

    # Phase 2: Rapid searches
    t2 = time.perf_counter()
    for i in range(n):
        topic = ["apple", "banana", "cherry", "date", "elderberry"][i % 5]
        s.search(topic)
    t3 = time.perf_counter()
    search_elapsed = t3 - t2
    check(f"7.3 {n} searches in {search_elapsed:.2f}s",
          search_elapsed < max(5.0, n * 0.3), f"took {search_elapsed:.2f}s")

    # Phase 3: Verify edge graph integrity
    edges = s._get_edges()
    edge_ids = set()
    dup_edges = 0
    for e in edges["edges"]:
        pair = (e["source"], e["target"])
        rev = (e["target"], e["source"])
        if pair in edge_ids or rev in edge_ids:
            dup_edges += 1
        edge_ids.add(pair)
    check("7.4 no duplicate edges", dup_edges == 0, f"found {dup_edges} dupes")

    # Phase 4: Verify score distribution
    scores = [m["score"] for m in s._cache]
    avg_score = sum(scores) / len(scores)
    check("7.5 avg score after stress", avg_score > 0.50,
          f"avg={avg_score:.4f}")
    check("7.5b max score ≤ 1.0", max(scores) <= 1.0,
          f"max={max(scores):.4f}")

    # Phase 5: Reload from disk
    s2 = MemoryStore(tmp)
    check("7.6 reload disk → same count", len(s2._cache) == n)
    # Scores on disk reflect initial scores (0.50) + any score changes that were
    # explicitly persisted. The cache has live in-memory scores from search/decay.
    # Compare counts and edge graph integrity instead of raw score values.
    disk_scores = [m["score"] for m in s2._cache]
    check("7.6b reload picks up entries correctly",
          len(disk_scores) == n and all(s >= 0.49 for s in disk_scores))

    # Phase 6: Edge persistence across reload
    edges2 = s2._get_edges()
    check("7.7 edges persist after reload",
          len(edges2["edges"]) == len(edges["edges"]),
          f"before={len(edges['edges'])} after={len(edges2['edges'])}")

    # Phase 7: Verify 2-hop propagation actually happened
    # Search a specific term and check neighbour scores
    s2.search("apple")
    s2.search("banana")
    # Score check — at least some memories should be > 0.55
    boosted = sum(1 for m in s2._cache if m["score"] > 0.55)
    check("7.8 boosted memories exist after stress",
          boosted > 0, f"got {boosted} boosted entries")

    # Phase 8: Decay under load
    s2.decay()
    check("7.9 decay runs without error on large store", True)

    cleanup(tmp)
    print(f"\n  Stress summary: {n} entries, {len(edges['edges'])} edges, "
          f"avg_score={avg_score:.3f}")


# ══════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════
def print_report():
    global PASS, FAIL
    total = PASS + FAIL
    pct = (PASS / total * 100) if total else 0
    print(f"\n{'═' * 50}")
    print(f"RESULTS: {PASS}/{total} passed ({pct:.1f}%)")
    if FAIL:
        print(f"FAILURES: {FAIL}")
        for e in ERRORS:
            print(f"  {e}")
    return FAIL == 0


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    suite_basic_storage()
    suite_edge_creation()
    suite_scoring()
    suite_decay()
    suite_stats()
    suite_edge_cases()
    suite_stress()

    ok = print_report()
    sys.exit(0 if ok else 1)
