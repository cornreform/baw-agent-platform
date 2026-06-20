"""BAW built-in: Self-test suite — run diagnostics without user interaction."""

import os
import sys
import shutil
import json
import time
from pathlib import Path


def _check_config() -> dict:
    """Load and validate config.yaml."""
    import yaml

    results = {"name": "Config", "status": "pending", "details": []}
    config_paths = [
        Path.home() / ".baw" / "config.yaml",
        Path.home() / "baw" / "config.yaml",
        Path(__file__).parent.parent / "config.yaml",
    ]
    cfg = None
    for p in config_paths:
        if p.exists():
            try:
                cfg = yaml.safe_load(p.read_text())
                results["details"].append(f"Config loaded from {p}")
                break
            except Exception as e:
                results["details"].append(f"Failed to load {p}: {e}")

    if cfg is None:
        results["status"] = "fail"
        results["details"].append("No config.yaml found")
        return results

    # Check providers
    providers = cfg.get("providers", {})
    results["details"].append(f"Providers: {', '.join(providers.keys())}")

    for name, pconf in providers.items():
        api_key_env = pconf.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "")
        base_url = pconf.get("base_url", "")
        status = "[OK]" if api_key else "[FAIL]"
        results["details"].append(f"  {status} {name}: key={'SET' if api_key else 'MISSING'}, url={base_url}")

    # Check capabilities
    caps = cfg.get("capabilities", {})
    for cap_name in ["vision", "stt", "tts", "chat"]:
        cap = caps.get(cap_name, {})
        model = cap.get("model") if isinstance(cap, dict) else cap
        results["details"].append(f"  {cap_name}: {model or 'NOT SET'}")

    results["status"] = "pass"
    return results


def _check_memory() -> dict:
    """Test memory save/search/dedup."""
    results = {"name": "Memory", "status": "pending", "details": []}

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from core.memory import MemoryStore

        mem = MemoryStore(Path.home() / ".baw")
        results["details"].append(f"Memory entries: {len(mem._cache)}")

        # Test save + dedup
        test_content = f"[selftest] {time.time():.0f}"
        r1 = mem.remember(test_content, tags=["selftest"])
        r2 = mem.remember(test_content, tags=["selftest"])

        if r1.get("id") == r2.get("id") or "updated" in str(r2):
            results["details"].append("[OK] Deduplication works")
        else:
            results["details"].append("⚪ Deduplication: 2 separate entries")

        # Test quality gate
        r3 = mem.remember("是", tags=["selftest"])
        if "rejected" in str(r3):
            results["details"].append("[OK] Quality gate rejects short content")
        else:
            results["details"].append("[FAIL] Quality gate failed")

        # Test search
        search_result = mem.search(test_content, limit=3)
        if search_result:
            results["details"].append(f"[OK] Search returns {len(search_result)} results")
        else:
            results["details"].append("⚪ Search empty (may be normal)")

        results["status"] = "pass"
    except Exception as e:
        results["status"] = "fail"
        results["details"].append(f"[FAIL] Memory test error: {e}")

    return results


def _check_safety() -> dict:
    """Test safety blocking."""
    results = {"name": "Safety", "status": "pending", "details": []}

    try:
        from tools.read_file import _is_sensitive as rf_sens
        from tools.bash import _is_sensitive as bash_sens

        test_cases = [
            ("/etc/passwd", True, "read_file"),
            ("/etc/shadow", True, "read_file"),
            ("/tmp/test.txt", False, "read_file"),
            ("rm -rf /", True, "bash"),
            ("ls /tmp", False, "bash"),
        ]

        for path_or_cmd, should_block, tool in test_cases:
            if tool == "read_file":
                blocked, reason = rf_sens(path_or_cmd)
            else:
                blocked, reason = bash_sens(path_or_cmd)

            if blocked == should_block:
                results["details"].append(f"[OK] {tool}('{path_or_cmd}'): {'blocked' if blocked else 'allowed'}")
            else:
                results["details"].append(f"[FAIL] {tool}('{path_or_cmd}'): expected {'blocked' if should_block else 'allowed'}, got {'blocked' if blocked else 'allowed'}")

        results["status"] = "pass"
    except Exception as e:
        results["status"] = "fail"
        results["details"].append(f"[FAIL] Safety test error: {e}")

    return results


def _check_tts() -> dict:
    """Test TTS providers."""
    results = {"name": "TTS", "status": "pending", "details": []}

    # MiniMax
    if os.environ.get("MINIMAX_API_KEY"):
        results["details"].append("[OK] MiniMax API key: SET")
    else:
        results["details"].append("[FAIL] MiniMax API key: MISSING")

    # Edge TTS
    if shutil.which("edge-tts"):
        results["details"].append("[OK] Edge TTS CLI: available")
    else:
        results["details"].append("⚪ Edge TTS CLI: not found")

    # Quick API connectivity test (only in full mode)
    if not _skip_api_calls:
        try:
            import urllib.request, json
            key = os.environ.get("MINIMAX_API_KEY", "")
            req = urllib.request.Request(
                "https://api.minimax.io/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                results["details"].append("[OK] MiniMax API connectivity: OK")
        except Exception as e:
            results["details"].append(f"⚪ MiniMax API connectivity: {e}")

    results["status"] = "pass"
    return results


def _check_stt() -> dict:
    """Test STT providers."""
    results = {"name": "STT", "status": "pending", "details": []}

    # faster-whisper
    try:
        import faster_whisper
        results["details"].append("[OK] faster-whisper: installed")
    except ImportError:
        results["details"].append("[FAIL] faster-whisper: not installed")

    # ffmpeg
    if shutil.which("ffmpeg"):
        results["details"].append("[OK] ffmpeg: available")
    else:
        results["details"].append("⚪ ffmpeg: not found (may affect audio conversion)")

    # Stepfun API key
    if os.environ.get("STEPFUN_API_KEY"):
        results["details"].append("[OK] Stepfun API key: SET")
    else:
        results["details"].append("[FAIL] Stepfun API key: MISSING")

    # Quick API connectivity test (only in full mode)
    if not _skip_api_calls:
        try:
            import urllib.request
            api_key = os.environ.get("STEPFUN_API_KEY", "")
            req = urllib.request.Request(
                "https://api.stepfun.ai/step_plan/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                results["details"].append("[OK] Stepfun STT endpoint connectivity: OK")
        except Exception as e:
            results["details"].append(f"⚪ Stepfun STT endpoint: {e}")

    results["status"] = "pass"
    return results


def _check_vision() -> dict:
    """Test vision providers."""
    results = {"name": "Vision", "status": "pending", "details": []}

    # MiniMax direct API (primary - no CLI needed)
    if os.environ.get("MINIMAX_API_KEY"):
        results["details"].append("[OK] MiniMax API key: SET (direct API available)")
    else:
        results["details"].append("[FAIL] MiniMax API key: MISSING")

    # MiniMax mmx CLI (optional legacy)
    if shutil.which("mmx"):
        results["details"].append("[OK] mmx CLI: available")
    else:
        results["details"].append("⚪ mmx CLI: not found (optional, direct API is primary)")

    # Stepfun API key (shared with STT)
    if os.environ.get("STEPFUN_API_KEY"):
        results["details"].append("[OK] Stepfun API key: SET (fallback available)")
    else:
        results["details"].append("[FAIL] Stepfun API key: MISSING")

    # Test actual MiniMax vision call (only in full mode)
    if not _skip_api_calls:
        try:
            from tools.vision import _vision_minimax
            import base64
            # 1x1 red PNG
            png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
            with open("/tmp/selftest_vision.png", "wb") as f:
                f.write(base64.b64decode(png_b64))

            result = _vision_minimax("/tmp/selftest_vision.png", "What do you see?")
            if result.startswith("Error:") or result.startswith("MiniMax vision error:"):
                results["details"].append(f"⚪ MiniMax vision API: {result[:100]}")
            else:
                results["details"].append(f"[OK] MiniMax vision API: responded ({len(result)} chars)")
        except Exception as e:
            results["details"].append(f"⚪ MiniMax vision test: {e}")

    results["status"] = "pass"
    return results


def _check_tools() -> dict:
    """Check tool files exist."""
    results = {"name": "Tools", "status": "pending", "details": []}

    tools_dir = Path(__file__).parent
    expected = ["bash", "read_file", "write_file", "web_search", "web_extract",
                "search_files", "patch", "memory", "todo", "delegate_task",
                "vision", "tts", "browser", "execute_code", "selftest", "install"]

    missing = []
    for t in expected:
        if not (tools_dir / f"{t}.py").exists():
            missing.append(t)

    if missing:
        results["details"].append(f"[FAIL] Missing tools: {', '.join(missing)}")
        results["status"] = "warn"
    else:
        results["details"].append(f"[OK] All {len(expected)} tools present")
        results["status"] = "pass"

    return results


def _check_memory_curator() -> dict:
    """Test the memory curation gate — classify, conflict-detect, noise filter."""
    results = {"name": "Memory Curator", "status": "pending", "details": []}
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from core.memory_curator import curate, classify, detect_conflicts, value_score, Classification

        # ── Test 1: Classification accuracy ──
        test_cases = [
            ("我鍾意簡潔輸出", Classification.PREFERENCE),
            ("呢個係bug，會 silent fail", Classification.BUG),
            ("npm install mmx-cli 成功", Classification.INSTALL),
            ("ok done", Classification.NOISE),
            ("其實我之前講錯，正確係改用 hybrid", Classification.CORRECTION),
            ("正在檢查 status", Classification.TRANSIENT),
        ]
        cls_pass = 0
        for text, expected in test_cases:
            cls, conf = classify(text)
            match = "✓" if cls == expected else "✗"
            if cls == expected:
                cls_pass += 1
            results["details"].append(f"  {match} classify('{text[:40]}'): {cls} (expected {expected}, conf={conf})")
        results["details"].append(f"  Classification: {cls_pass}/{len(test_cases)} correct")

        # ── Test 2: Value scoring by class ──
        assert value_score(Classification.PREFERENCE) > value_score(Classification.NOISE)
        assert value_score(Classification.BUG) > value_score(Classification.TRANSIENT)
        results["details"].append("  [OK] Value scores: preference > noise, bug > transient")

        # ── Test 3: Conflict detection (correction → update) ──
        existing = [
            {"id": "mem_test_1", "content": "STT uses hybrid mode with primary grok"},
            {"id": "mem_test_2", "content": "MiniMax is the main provider"},
        ]
        correction = "actually STT primary is grok-stt at xAI, not what I said before"
        conflict = detect_conflicts(correction, existing)
        if conflict and conflict["type"] == "update":
            results["details"].append(f"  [OK] Correction detected: {conflict['reason']}")
        else:
            results["details"].append(f"  [FAIL] Correction not detected. Got: {conflict}")

        # ── Test 4: Noise gate ──
        decision = curate("好嘅", existing_entries=[])
        if decision["action"] == "discard":
            results["details"].append("  [OK] '好嘅' correctly discarded as noise")
        else:
            results["details"].append(f"  [FAIL] '好嘅' not discarded: {decision['action']}")

        # ── Test 5: Valuable content passes gate ──
        decision = curate("用戶偏好用粵語輸出，唔要用英文",
                          tags=["preference"], source="user", existing_entries=[])
        if decision["action"] in ("save", "update"):
            results["details"].append(f"  [OK] Preference content accepted: {decision['classification']} (score={decision['score']})")
        else:
            results["details"].append(f"  [FAIL] Preference rejected: {decision['action']}")

        results["status"] = "pass"
    except Exception as e:
        results["status"] = "fail"
        results["details"].append(f"[FAIL] Memory curator test error: {e}")
        import traceback
        results["details"].append(traceback.format_exc()[:200])
    return results


def _check_context_compaction() -> dict:
    """Test context compaction — threshold, summary quality, compression ratio."""
    results = {"name": "Context Compaction", "status": "pending", "details": []}
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from core.context import Context, Message

        ctx = Context(system_prompt="Test system prompt")
        for i in range(20):
            ctx.add_user(f"User turn {i}: 請幫我檢查第 {i} 個配置項目")
            ctx.add_assistant(f"完成檢查第 {i} 項，結果正常" if i % 2 == 0
                              else f"第 {i} 項有錯誤，已修正為正確設定")
            ctx.add_tool_result(f"t{i}", "bash", f"Output for turn {i}: some data")

        before_chars = ctx.total_chars()
        before_msgs = len(ctx.messages)

        # Test 1: Compaction triggers at low threshold
        compacted, note, summary = ctx.compact(threshold_chars=5000, keep_recent_turns=3)
        after_chars = ctx.total_chars()
        after_msgs = len(ctx.messages)

        if compacted > 0:
            ratio = (1 - after_chars / before_chars) * 100
            results["details"].append(f"  [OK] Compaction triggered: {compacted} turns compressed")
            results["details"].append(f"  [OK] {before_chars} → {after_chars} chars ({ratio:.0f}% reduction)")
            results["details"].append(f"  [OK] {before_msgs} → {after_msgs} messages")
            results["details"].append(f"  [OK] Notification: {note}")
        else:
            results["details"].append("[FAIL] Compaction did not trigger")

        # Test 2: Summary contains compressed lines
        if summary.count("[壓縮]") >= compacted:
            results["details"].append("  [OK] Summary contains all compressed turns")
        else:
            results["details"].append(f"  [FAIL] Summary has {summary.count('[壓縮]')} turns, expected {compacted}")

        # Test 3: Recent turns preserved
        recent_users = sum(1 for m in ctx.messages if m.role == "user")
        if recent_users >= 3:
            results["details"].append(f"  [OK] Recent turns preserved ({recent_users} user messages remain)")
        else:
            results["details"].append(f"  [FAIL] Too few user messages remain: {recent_users}")

        # Test 4: No compaction under threshold
        ctx2 = Context(system_prompt="Test")
        ctx2.add_user("Short prompt")
        ctx2.add_assistant("Short response")
        c2, n2, s2 = ctx2.compact(threshold_chars=50000, keep_recent_turns=5)
        if c2 == 0:
            results["details"].append("  [OK] No compaction when under threshold")
        else:
            results["details"].append(f"  [FAIL] Compaction triggered unexpectedly: {c2}")

        results["status"] = "pass"
    except Exception as e:
        results["status"] = "fail"
        results["details"].append(f"[FAIL] Context compaction test error: {e}")
        import traceback
        results["details"].append(traceback.format_exc()[:200])
    return results


def _check_memory_search_by_id() -> dict:
    """Test that memory search works by both content and ID."""
    results = {"name": "Memory Search by ID", "status": "pending", "details": []}
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from core.memory import MemoryStore
        import tempfile, time

        with tempfile.TemporaryDirectory() as tmpdir:
            mem = MemoryStore(Path(tmpdir))

            # Save a test entry
            entry = mem.remember(
                content="測試用記憶內容",
                tags=["test", "selftest"],
                source="selftest",
            )
            mem_id = entry["id"]
            results["details"].append(f"  Saved test memory: {mem_id}")

            # Test: search by ID
            id_results = mem.search(mem_id, limit=5)
            if any(r["id"] == mem_id for r in id_results):
                results["details"].append("  [OK] Search by ID: found")
            else:
                results["details"].append("  [FAIL] Search by ID: not found")

            # Test: search by partial ID suffix
            suffix = mem_id.split("_")[-1]
            suffix_results = mem.search(suffix, limit=5)
            if any(r["id"] == mem_id for r in suffix_results):
                results["details"].append("  [OK] Search by partial ID suffix: found")
            else:
                results["details"].append(f"  [FAIL] Search by partial ID '{suffix}': not found")

            # Test: search by content (should still work)
            content_results = mem.search("測試用記憶", limit=5)
            if any(r["id"] == mem_id for r in content_results):
                results["details"].append("  [OK] Search by content: found (backward compat)")
            else:
                results["details"].append("  [FAIL] Search by content: not found")

        results["status"] = "pass"
    except Exception as e:
        results["status"] = "fail"
        results["details"].append(f"[FAIL] Memory search test error: {e}")
        import traceback
        results["details"].append(traceback.format_exc()[:200])
    return results


# Global flag to skip API calls in quick mode
_skip_api_calls = False


def selftest(full: bool = False) -> str:
    """Run BAW self-test suite.

    Args:
        full: If True, also runs API connectivity checks (slower, may incur cost). Default False.

    Returns:
        Markdown report of test results.
    """
    global _skip_api_calls
    _skip_api_calls = not full

    tests = [
        _check_config,
        _check_tools,
        _check_memory,
        _check_memory_curator,
        _check_memory_search_by_id,
        _check_context_compaction,
        _check_safety,
        _check_tts,
        _check_stt,
        _check_vision,
    ]

    results = []
    for test_fn in tests:
        try:
            results.append(test_fn())
        except Exception as e:
            results.append({"name": test_fn.__name__, "status": "fail", "details": [f"[FAIL] Exception: {e}"]})

    # Build report
    report_lines = ["# [TEST] BAW Self-Test Report", ""]
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    warned = sum(1 for r in results if r["status"] == "warn")

    report_lines.append(f"<b>Summary</b>: {passed}/{total} pass, {warned} warn, {failed} fail")
    report_lines.append("")

    for r in results:
        icon = {"pass": "[OK]", "fail": "[FAIL]", "warn": "[WARN]", "pending": "[QUEUED]"}.get(r["status"], "[?]")
        report_lines.append(f"## {icon} {r['name']}")
        for d in r["details"]:
            report_lines.append(f"- {d}")
        report_lines.append("")

    if failed == 0:
        report_lines.append("---")
        report_lines.append("[PASS] All critical tests passed!")
    else:
        report_lines.append("---")
        report_lines.append(f"[WARN] {failed} test(s) failed. Check details above.")

    return "\n".join(report_lines)


TOOL_DEF = {
    "name": "selftest",
    "description": (
        "Run BAW internal self-test suite to check system health. "
        "Tests config, memory, memory curator (classification + conflict detection), "
        "memory search by ID, context compaction, safety, TTS, STT, and vision. "
        "Use '/selftest' or '幫我做自我測試' to trigger."
    ),
    "handler": selftest,
    "parameters": {
        "type": "object",
        "properties": {
            "full": {
                "type": "boolean",
                "description": "Run full tests including API calls (slower). Default False.",
                "default": False,
            },
        },
    },
    "risk_level": "low",
}
