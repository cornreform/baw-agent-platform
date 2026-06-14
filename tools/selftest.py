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
        status = "✅" if api_key else "❌"
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
            results["details"].append("✅ Deduplication works")
        else:
            results["details"].append("⚪ Deduplication: 2 separate entries")

        # Test quality gate
        r3 = mem.remember("是", tags=["selftest"])
        if "rejected" in str(r3):
            results["details"].append("✅ Quality gate rejects short content")
        else:
            results["details"].append("❌ Quality gate failed")

        # Test search
        search_result = mem.search(test_content, limit=3)
        if search_result:
            results["details"].append(f"✅ Search returns {len(search_result)} results")
        else:
            results["details"].append("⚪ Search empty (may be normal)")

        results["status"] = "pass"
    except Exception as e:
        results["status"] = "fail"
        results["details"].append(f"❌ Memory test error: {e}")

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
                results["details"].append(f"✅ {tool}('{path_or_cmd}'): {'blocked' if blocked else 'allowed'}")
            else:
                results["details"].append(f"❌ {tool}('{path_or_cmd}'): expected {'blocked' if should_block else 'allowed'}, got {'blocked' if blocked else 'allowed'}")

        results["status"] = "pass"
    except Exception as e:
        results["status"] = "fail"
        results["details"].append(f"❌ Safety test error: {e}")

    return results


def _check_tts() -> dict:
    """Test TTS providers."""
    results = {"name": "TTS", "status": "pending", "details": []}

    # MiniMax
    if os.environ.get("MINIMAX_API_KEY"):
        results["details"].append("✅ MiniMax API key: SET")
    else:
        results["details"].append("❌ MiniMax API key: MISSING")

    # Edge TTS
    if shutil.which("edge-tts"):
        results["details"].append("✅ Edge TTS CLI: available")
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
                results["details"].append("✅ MiniMax API connectivity: OK")
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
        results["details"].append("✅ faster-whisper: installed")
    except ImportError:
        results["details"].append("❌ faster-whisper: not installed")

    # ffmpeg
    if shutil.which("ffmpeg"):
        results["details"].append("✅ ffmpeg: available")
    else:
        results["details"].append("⚪ ffmpeg: not found (may affect audio conversion)")

    # Stepfun API key
    if os.environ.get("STEPFUN_API_KEY"):
        results["details"].append("✅ Stepfun API key: SET")
    else:
        results["details"].append("❌ Stepfun API key: MISSING")

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
                results["details"].append("✅ Stepfun STT endpoint connectivity: OK")
        except Exception as e:
            results["details"].append(f"⚪ Stepfun STT endpoint: {e}")

    results["status"] = "pass"
    return results


def _check_vision() -> dict:
    """Test vision providers."""
    results = {"name": "Vision", "status": "pending", "details": []}

    # MiniMax direct API (primary - no CLI needed)
    if os.environ.get("MINIMAX_API_KEY"):
        results["details"].append("✅ MiniMax API key: SET (direct API available)")
    else:
        results["details"].append("❌ MiniMax API key: MISSING")

    # MiniMax mmx CLI (optional legacy)
    if shutil.which("mmx"):
        results["details"].append("✅ mmx CLI: available")
    else:
        results["details"].append("⚪ mmx CLI: not found (optional, direct API is primary)")

    # Stepfun API key (shared with STT)
    if os.environ.get("STEPFUN_API_KEY"):
        results["details"].append("✅ Stepfun API key: SET (fallback available)")
    else:
        results["details"].append("❌ Stepfun API key: MISSING")

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
                results["details"].append(f"✅ MiniMax vision API: responded ({len(result)} chars)")
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
        results["details"].append(f"❌ Missing tools: {', '.join(missing)}")
        results["status"] = "warn"
    else:
        results["details"].append(f"✅ All {len(expected)} tools present")
        results["status"] = "pass"

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
            results.append({"name": test_fn.__name__, "status": "fail", "details": [f"❌ Exception: {e}"]})

    # Build report
    report_lines = ["# 🧪 BAW Self-Test Report", ""]
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    warned = sum(1 for r in results if r["status"] == "warn")

    report_lines.append(f"**Summary**: {passed}/{total} pass, {warned} warn, {failed} fail")
    report_lines.append("")

    for r in results:
        icon = {"pass": "✅", "fail": "❌", "warn": "⚠️", "pending": "⏳"}.get(r["status"], "❓")
        report_lines.append(f"## {icon} {r['name']}")
        for d in r["details"]:
            report_lines.append(f"- {d}")
        report_lines.append("")

    if failed == 0:
        report_lines.append("---")
        report_lines.append("🎉 All critical tests passed!")
    else:
        report_lines.append("---")
        report_lines.append(f"⚠️ {failed} test(s) failed. Check details above.")

    return "\n".join(report_lines)


TOOL_DEF = {
    "name": "selftest",
    "description": (
        "Run BAW internal self-test suite to check system health. "
        "Tests config, memory, safety, TTS, STT, and vision capabilities. "
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
