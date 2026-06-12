"""BAW — System Defaults (single source of truth for cross-cutting config).

The 2026-06-12 sub-agent failure mode: the LLM doesn't know what
BAW's defaults are, so it picks its own (Google Places, curl binary,
hardcoded paths) and they collide with the actual environment.

This module defines defaults that EVERY sub-agent / CLI / tool should
respect, and exposes a ``summary_block()`` that the agent loop injects
into the system prompt so sub-agents have the rules in their context.

**Rules** (each is enforced somewhere in the codebase; this is the
documentation pointer):

  1. **Fetch strategy** = ``core.http_fetch`` (auto-detects SPA).
     Never `subprocess.run(["curl", ...])` — `curl` isn't in the
     venv. Never bare `urllib` against an SPA — returns empty shell.

  2. **Path resolution** = ``from core.paths import ...``.
     Never hardcode `~/baw/` or `/home/baw/baw/` (host vs container
     mismatch bit the 2026-06-12 pet-restaurant sub-agent).

  3. **Data source** = consult ``core.data_sources.REGISTRY`` first.
     Prefer the free + no-key + stdlib entry. If you must reach for a
     paid service, document why in the tool's docstring and add a
     free fallback to the registry.

  4. **Audio** = ``edge-tts`` (free, Cantonese voices) by default.
     OpenAI TTS only when explicitly requested.

  5. **STT** = local faster-whisper for short clips; OpenAI Whisper
     only for long-form or when explicitly requested.

  6. **Vision** = ``MiniMax-M3`` (internal) for general use; higher-
     cost models only when the user explicitly asks.

  7. **Verify** = run ``baw self-test`` after any self-build task.
     Self-test now also validates TOOL_DEF schema, data source
     registry, and recipe consistency.

  8. **TOOL_DEF shape** = ``core.tool_schema.REQUIRED_KEYS``.
     Required: name, description, handler, parameters, risk_level.
     ``register()`` auto-validates; missing or extra keys warn / fail.

  9. **Tool count budget** = no more than 20 tools. Adding a new
     tool? Consider if an existing one can be extended with a flag
     instead. Tool count above 20 hurts LLM tool-selection accuracy.

 10. **Caches** = 24h TTL by default, write to ``data/*_cache.json``
     (gitignored). Never commit a cache file to git.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Any


@dataclass
class SystemDefault:
    """One system-wide default."""
    name: str
    value: str
    rationale: str
    enforced_at: str  # module path where the default is enforced


DEFAULTS: Dict[str, SystemDefault] = {
    "fetch_strategy": SystemDefault(
        name="fetch_strategy",
        value="core.http_fetch.http_fetch",
        rationale=(
            "Auto-detects Next.js / Gatsby / React SPAs and returns "
            "BROWSER_REQUIRED with a mirror path. Saves the 2026-06-12 "
            "sub-agent failure where urllib returned 0 bytes of an SPA."
        ),
        enforced_at="tools/http_fetch.py + core/loop.py recipe_block",
    ),
    "path_resolution": SystemDefault(
        name="path_resolution",
        value="from core.paths import repo_root, data_dir, tools_dir, runtime_home",
        rationale=(
            "Container vs host path mismatch bit the pet-restaurant "
            "sub-agent. core.paths resolves via $BAW_HOME / repo markers."
        ),
        enforced_at="core/paths.py (used by every tool)",
    ),
    "data_source_default": SystemDefault(
        name="data_source_default",
        value="core.data_sources.REGISTRY — prefer free+no-key+stdlib entry",
        rationale=(
            "The 2026-06-12 sub-agent defaulted to Google Places (paid, "
            "key required) and got stuck. Registry lists free defaults "
            "for restaurants, geocoding, weather, transit, etc."
        ),
        enforced_at="core/data_sources.py + this file's summary_block",
    ),
    "audio_tts": SystemDefault(
        name="audio_tts",
        value="edge-tts (zh-HK-HiuMaanNeural default, user-configurable)",
        rationale=(
            "Free, no key, Cantonese voices pre-installed. OpenAI TTS "
            "only when explicitly requested via [tts: ...] tag."
        ),
        enforced_at="config.yaml capabilities.tts (default) + core/loop.py override tags",
    ),
    "stt": SystemDefault(
        name="stt",
        value="local faster-whisper for short clips; auto-asr handler for Telegram voice",
        rationale=(
            "Local whisper = no network round-trip, no cost. OpenAI "
            "Whisper only for long-form when the user asks."
        ),
        enforced_at="config.yaml capabilities.stt (default) + tools/audio handler",
    ),
    "vision": SystemDefault(
        name="vision",
        value="MiniMax-M3 (internal, no per-call cost)",
        rationale=(
            "Default in config.yaml. Higher-cost vision models only "
            "when the user explicitly asks via [vision: ...] tag."
        ),
        enforced_at="config.yaml capabilities.vision + core/loop.py override tags",
    ),
    "verify_after_self_build": SystemDefault(
        name="verify_after_self_build",
        value="baw self-test (validates path resolution, tool registry, TOOL_DEF schema, data source registry, recipe consistency)",
        rationale=(
            "Self-test is the only thing standing between a sub-agent "
            "claiming 'done' and actually being done."
        ),
        enforced_at="cli/commands/self_test_cmd.py + core/preflight.py",
    ),
    "tool_def_shape": SystemDefault(
        name="tool_def_shape",
        value="name, description, handler, parameters, risk_level — all required",
        rationale=(
            "TOOL_DEF drift bit 4 tools (delegate_task, http_fetch, "
            "restaurant, browser — some lacked risk_level literal). "
            "register() now auto-validates."
        ),
        enforced_at="core/tool_schema.py + core/tools.register()",
    ),
    "tool_count_budget": SystemDefault(
        name="tool_count_budget",
        value="≤ 20 tools total",
        rationale=(
            "Above 20, LLM tool-selection accuracy degrades. "
            "If you need a 21st tool, prefer extending an existing "
            "one with a flag."
        ),
        enforced_at="(soft) — self-test warns when count > 20",
    ),
    "cache_ttl_hours": SystemDefault(
        name="cache_ttl_hours",
        value="24",
        rationale=(
            "Balances freshness vs API quota. Restaurant OSM data "
            "doesn't change faster than once a day in practice."
        ),
        enforced_at="tools/<datasource>.py _cache_get/_cache_put",
    ),
}


def summary_block() -> str:
    """One-paragraph-per-default block for the system prompt."""
    lines = ["## BAW System Defaults (READ before designing a new tool)"]
    for key, d in DEFAULTS.items():
        lines.append(f"- **{key}** = `{d.value}`")
        lines.append(f"  - {d.rationale}")
    lines.append("")
    lines.append("These defaults are also enforced at: " +
                 ", ".join(sorted({d.enforced_at for d in DEFAULTS.values()})))
    return "\n".join(lines)


def validate() -> List[str]:
    """Run by `baw self-test`. Returns list of warnings (empty = OK)."""
    warnings: List[str] = []
    for key, d in DEFAULTS.items():
        if not d.value:
            warnings.append(f"{key}: value is empty")
        if not d.rationale:
            warnings.append(f"{key}: rationale is empty (sub-agents can't see the why)")
    return warnings
