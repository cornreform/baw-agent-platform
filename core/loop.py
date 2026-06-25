"""
BAW — Agent Loop: Debate-first, Execute-second

Flow per user turn:
  Phase 1 — Court (independent analysis)
    Devil and Angel analyze the SAME input independently.
    Both give scores. BAW synthesizes neutrally.
    [WARN] NEITHER voice has execution power during court.

  Phase 2 — Neutral response & debate (interactive only)
    BAW responds from neutral perspective.
    NOT trying to please the user — WILLING to disagree.
    User ↔ Agent back and forth until conclusion.

  Phase 3 — Execute (once conclusion is reached)
    BAW executes toward the goal.
    Plan → Execute per step → Verify → Report.
    No re-litigation — the debate is settled.
"""
from __future__ import annotations
import re
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from typing import Callable

logger = logging.getLogger("baw.loop")

from .llm import get_model, call_llm_with_fallback, calculate_cost, FallbackResult
from .context import Context, Message
from .tools import get_openai_tools, execute_tool, list_tools
from .permission import PermissionEngine
from .memory import MemoryStore
from .checkpoint import Checkpointer
from .tool_policy import get_max_retries, classify_error
from .file_history import FileHistory
from .autosave import auto_commit, get_commit_log
from . import render as html

# ── Output token budget (effectively unlimited — user prefers complete answers) ──
OUTPUT_MAX_TOKENS = 100_000  # Effectively unlimited; prevents infinite loops
OUTPUT_MAX_CHARS = 1_000_000  # Effectively unlimited; safety bound only

# ── Per-mode max tokens (input-side budget for LLM generation) ──
# Quick mode: rapid research → 8192
# Hybrid mode: moderate reasoning → 16384
# Tight mode: deep analysis → 32768
# Auto: let LLM decide (12288)
# Focus: intensive tool-driven research → 32768
MODE_MAX_TOKENS = {
    "quick": 8192,
    "hybrid": 16384,
    "tight": 32768,
    "auto": 12288,
    "focus": 32768,
}

# ── Lightweight performance profiler ──
import time as _perf_time
_PERF_LOG: list[tuple[str, float]] = []

def perf_start(label: str) -> None:
    """Start a performance timer."""
    _PERF_LOG.append((label, _perf_time.time()))

def perf_end(label: str, logger=None) -> float:
    """End a performance timer and log duration. Returns seconds."""
    now = _perf_time.time()
    for i, (l, t) in enumerate(_PERF_LOG):
        if l == label:
            elapsed = now - t
            _PERF_LOG.pop(i)
            if logger:
                logger.debug(f"[Perf] {label}: {elapsed:.3f}s")
            return elapsed
    return 0.0

def perf_summary() -> str:
    """Get a summary of all completed perf measurements."""
    return "\n".join(f"  {l}: {t*1000:.0f}ms" for l, t in _PERF_LOG) if _PERF_LOG else "  (none)"

# ── Cost tracking (thread-safe class) ──────────────────────────

import threading


def _human_tokens(n: int) -> str:
    """Format token count as human-readable string."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ── Constants ──────────────────────────────────────────────────
MAX_TOOL_TURNS = 50      # base — overridden dynamically by task complexity below

# ── Tool cap scaling by task complexity ──
_TOOL_TURNS_BY_COMPLEXITY = {
    "simple":   50,
    "moderate": 100,
    "complex":  150,
}

MAX_QUICK_TOOL_TURNS = 5  # stricter cap for quick mode


# ── Task Classification Layer ──────────────────────────────────
# Natural language first: no hardcoded regex patterns.
# Classification is done by the LLM itself during the first interaction.
# The permission engine at tool level is the real security layer.
# For pre-LLM routing we always default to TYPE_A (safe).

def _classify_task_type(prompt: str) -> dict:
    """
    Natural-language-first classification — always safe default.
    The LLM itself determines task safety through the permission engine
    at tool-call time. No rigid keyword/pattern matching required.
    """
    return {"type": "TYPE_A", "needs_audit": False, "reason": "natural language — LLM handles safety at tool level"}


# ═══════════════════════════════════════════════════════════════
# ── Context window tracker (session-scoped) ──
# ═══════════════════════════════════════════════════════════════

_CONTEXT_TRACKER: dict = {"model_id": "", "ctx_window": 0, "current_pct": 0.0}


def set_context_window(model_id: str, ctx_window: int, current_pct: float = 0.0):
    """Update context window tracking for display. Called by _run_baw() each message."""
    _CONTEXT_TRACKER["model_id"] = model_id
    _CONTEXT_TRACKER["ctx_window"] = ctx_window
    _CONTEXT_TRACKER["current_pct"] = round(current_pct, 1)


def context_window_summary() -> str:
    """Return one-line context window status."""
    ctx = _CONTEXT_TRACKER
    if not ctx["model_id"] or not ctx["ctx_window"]:
        return ""
    cw = _human_tokens(ctx["ctx_window"])
    pct = ctx["current_pct"]
    bar = _ctx_bar(pct)
    return f"[MODEL] {ctx['model_id']} {bar} {pct:.0f}% ({cw})"


def _ctx_bar(pct: float) -> str:
    """5-block context bar."""
    filled = int(pct / 20)
    blocks = "".join("█" if i < filled else "░" for i in range(5))
    return f"[{blocks}]"


class CostTracker:
    """Thread-safe token accumulator. One instance per session."""

    MAX_SESSION_TOKENS=500000  # Hard cap: stop loop when total exceeds this

    def __init__(self):
        self._lock = threading.Lock()
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.calls: list[dict] = []
        self.primary_model: str = ""

    def over_budget(self) -> bool:
        """Check if total tokens exceed budget. Thread-safe."""
        with self._lock:
            return (self.total_tokens_in + self.total_tokens_out) > self.MAX_SESSION_TOKENS

    def record(self, model_name: str, tokens_in: int, tokens_out: int, cost: float):
        with self._lock:
            call_num = len(self.calls) + 1
            self.calls.append({
                "model": model_name, "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            })
            self.total_tokens_in += tokens_in
            self.total_tokens_out += tokens_out
            self.primary_model = model_name  # last used model

        # Detailed token log (async-safe write)
        try:
            _log_path = Path.home() / ".baw" / "logs" / "tokens.jsonl"
            _log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(_log_path, "a") as _f:
                _f.write(json.dumps({
                    "ts": time.time(),
                    "call": call_num,
                    "model": model_name,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cumulative_in": self.total_tokens_in,
                    "cumulative_out": self.total_tokens_out,
                }) + "\n")
        except OSError:
            pass

    def summary(self) -> str:
        with self._lock:
            if not self.calls:
                return ""
            ti = self.total_tokens_in
            to = self.total_tokens_out
            total = ti + to
            model_tag = f" ({self.primary_model})" if self.primary_model else ""
            n = len(self.calls)
            label = "call" if n == 1 else "calls"
            return (
                f"{n} {label}{model_tag} — total: {_human_tokens(total)} tokens"
            )

    def html_summary(self) -> str:
        with self._lock:
            if not self.calls:
                return ""
            ti = self.total_tokens_in
            to = self.total_tokens_out
            total = ti + to
            model_tag = f" ({self.primary_model})" if self.primary_model else ""
            n = len(self.calls)
            label = "call" if n == 1 else "calls"
            return (
                f"📊 <b>{n} {label}</b>{model_tag} — total: {_human_tokens(total)}"
            )

    def reset(self):
        with self._lock:
            self.total_tokens_in = 0
            self.total_tokens_out = 0
            self.calls = []
            self.primary_model = ""


_TRACKER: CostTracker | None = None


def _get_tracker() -> CostTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = CostTracker()
    return _TRACKER


def record_cost(model_name: str, tokens_in: int, tokens_out: int, cost: float):
    _get_tracker().record(model_name, tokens_in, tokens_out, cost)


def format_cost_summary() -> str:
    return _get_tracker().summary()


def html_cost_summary() -> str:
    return _get_tracker().html_summary()


def reset_cost():
    _get_tracker().reset()


# ── System prompt ──────────────────────────────────────────────

def _summarize_providers(config: dict) -> str:
    """Compact provider summary: 'deepseek(3) minimax(8) openrouter(342) ...'"""
    parts = []
    for pname, pdata in config.get("providers", {}).items():
        count = len(pdata.get("models", []))
        if count > 0:
            parts.append(f"{pname}({count})")
    return " ".join(parts) if parts else "none configured"


def build_system_prompt(config: dict, data_dir = None,
                       fresh_start: bool = False) -> str:
    import logging, os
    logger = logging.getLogger(__name__)
    base_path = data_dir or Path.home() / ".baw"
    soul_path = base_path / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")
    fb = Path.home() / ".baw" / "SOUL.md"
    if fb.exists():
        return fb.read_text(encoding="utf-8")
    return "You are BAW, Sunny assistant on QB A7S.\n"

def _build_todo_block(data_dir: Path) -> str:
    """Build the todo-system reminder + carry-over follow-ups block.

    Injected at the end of the system prompt so BAW is reminded every turn
    to think out loud, track work, and surface pending follow-ups.
    """
    try:
        from .todo_state import TodoState
    except Exception:
        return ""

    try:
        # Carry-over follow-ups from any previous session
        ts = TodoState(data_dir=data_dir, session_id="__probe__")
        carried = ts.load_pending_followups()
    except Exception:
        carried = []

    # Check for SELF_BUILD_RECIPE — if present, point BAW at it for any
    # 'scrape this URL / build me a tool' task. This is the missing link
    # that caused the 2026-06-12 pet-restaurant sub-agent to fail.
    recipe_block = ""
    try:
        from . import paths as _paths
        recipe_path = _paths.docs_dir() / "SELF_BUILD_RECIPE.md"
        if recipe_path.exists():
            recipe_block = (
                "\n\n## Self-Build Recipe (READ BEFORE any 'scrape / build me a tool' task)\n"
                "A 6-step workflow lives at `~/baw/docs/SELF_BUILD_RECIPE.md`:\n"
                "  0. PRE-FLIGHT — `python -m core.preflight <url>`. Refuse to start if BLOCKED.\n"
                "  1. PLAN — read source, define fields, write todos\n"
                "  2. FETCH — call `tools.http_fetch.http_fetch(url)`. If it returns\n"
                "     `strategy == 'BROWSER_REQUIRED'`, the page is a Next.js / Gatsby /\n"
                "     React SPA — mirror it to the suggested `mirror_path` via browser render\n"
                "     `web_extract` (which renders JS), then call `read_mirror(mirror_path)`\n"
                "     to get the parse input. Never use `urllib` against an SPA and\n"
                "     declare the result valid. Never `subprocess.run([\"curl\", ...])`\n"
                "     — `curl` is not in the venv.\n"
                "  3. PARSE — BeautifulSoup or regex, tolerant of missing fields\n"
                "  4. STORE — write to `data_dir() / '<thing>.json'` (via `core.paths`)\n"
                "  5. TOOL — create `tools/<thing>.py` with TOOL_DEF (`name`, `description`,\n"
                "     `handler`, `parameters`, `risk_level` ALL required — see\n"
                "     `core/tool_schema.py`) + register in `tools/__init__.py`\n"
                "  6. VERIFY — `baw self-test` (now also validates TOOL_DEF schema,\n"
                "     data source registry, and recipe consistency)\n"
                "After steps 2-5, run `baw self-test` to confirm. NEVER hardcode `/home/baw/baw/`\n"
                "or `~/baw/` paths — use `from core.paths import ...` for everything.\n"
                "The 2026-06-12 pet-restaurant sub-agent failed because it skipped this protocol.\n"
            )
    except Exception:
        pass

    # System defaults block — what BAW defaults to, in one place
    defaults_block = ""
    try:
        from . import system_defaults as _sd
        defaults_block = "\n\n" + _sd.summary_block()
    except Exception:
        pass

    # Data source registry — free + no-key + stdlib first
    data_sources_block = ""
    try:
        from . import data_sources as _ds
        data_sources_block = "\n\n" + _ds.summary_block()
    except Exception:
        pass

    # Only inject full todo block when there are actual pending items
    if not carried and not os.environ.get("BAW_SHOW_TODO"):
        # No pending items → skip the block entirely (saves ~375 tokens per call)
        return ""
    
    # Full block only when needed
    block = (
        "\n\n## Todo / Follow-up System\n"
        "Use `todo` tool for tracking: thoughts, tasks, follow-ups.\n"
    )
    if carried:
        block += "\n\n### [WARN] Pending follow-ups carried over from previous sessions:\n"
        for it in carried[:8]:
            tag = f" (from {it.session_id})" if it.session_id else ""
            block += f"- [TODO] [{it.id[-6:]}]{tag} {it.content}"
            if it.note:
                block += f" — {it.note}"
            block += "\n"
        if len(carried) > 8:
            block += f"- …and {len(carried) - 8} more (run `baw todo surface`)\n"
    return block + recipe_block + defaults_block + data_sources_block


# ── Post-turn verification (architectural enforcement) ──

def _verify_post_turn_claims(output: str, data_dir: Optional[Path] = None) -> str:
    """Verify config change claims in BAW's output against actual config.yaml.
    
    BAW framework prevents fabrication by mediating tool execution.
    BAW's post-turn hook catches fabrication after generation.
    If BAW claims a config change that doesn't exist, append a correction.
    """
    import re as _vre
    cfg_path = (data_dir / "config.yaml") if data_dir else (Path.home() / ".baw" / "config.yaml")
    
    # Detect config change claims in output
    _CLAIM_RE = _vre.compile(
        r'(?:已設定|config 已|已更新|設定好|改動已經生效|已切[換到]|搞掂)',
        _vre.IGNORECASE
    )
    if not _CLAIM_RE.search(output):
        return output
    
    # Read actual config
    try:
        import yaml as _vyaml
        cfg = _vyaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return output
    
    corrections = []
    
    # Read config as flat text for generic provider/model scanning
    try:
        cfg_text = cfg_path.read_text(encoding="utf-8").lower()
    except Exception:
        cfg_text = ""
    
    # Pattern 1: explicit key=value claims — scan ALL capabilities sections
    caps = (cfg or {}).get("capabilities", {})
    for section, cap in caps.items():
        if not isinstance(cap, dict):
            continue
        actual_method = cap.get("method", "")
        actual_model = cap.get("model", "")
        
        for key, actual, label in [
            ("method", actual_method, f"{section} method"),
            ("model", actual_model, f"{section} model"),
        ]:
            if not actual:
                continue
            # Only match key=value or key:value patterns (NOT natural language)
            m = _vre.search(rf'{key}[:=][\s]*["\']?(\S+?)(?:["\'\)]|\s|$)', output)
            if m:
                claimed = m.group(1).rstrip('"\'",.)')
                if claimed and claimed != actual:
                    corrections.append(f"Claimed {label}={claimed}, config shows {label}={actual}")
    
    # Pattern 2: Generic provider/model name claims
    # "改用 Grok" / "用 xAI STT" / "切換到 faster-whisper" → check if name appears in config
    _CLAIM_VERBS = r'(?:改用|用|切換到|設定為|轉用|改用咗|已改用)'
    provider_claims = _vre.findall(
        rf'{_CLAIM_VERBS}\s*(\S+?)(?:\s|$|。|STT|TTS|做|，)',
        output, _vre.IGNORECASE
    )
    for claim in provider_claims:
        claim_clean = claim.strip('"\'",.）)').lower()
        # Skip very short or Chinese-only tokens (false positives)
        if len(claim_clean) < 3 or _vre.match('^[\u4e00-\u9fff]+$', claim_clean):
            continue
        # Skip code refs: key=val, starts with ` or /, contains .py
        if '=' in claim_clean or claim_clean.startswith('`') or claim_clean.startswith('/') or '.py' in claim_clean:
            continue
        # Skip natural language: enumeration commas, full-width punct, >40 chars
        if '\u3001' in claim_clean or '\uff0c' in claim_clean or '\u3002' in claim_clean or len(claim_clean) > 40:
            continue
        if claim_clean and cfg_text and claim_clean not in cfg_text:
            corrections.append(
                f"Output claims '{claim}' but this name does not appear in config.yaml — "
                "config change may be fabricated"
            )
    
    # Pattern 3: User directive override — user asked for X, config doesn't reflect it
    _USER_DIRECTIVE = _vre.search(
        r'(?:叫|要求|需要|想|要)\s*(?:我|你|BAW)?\s*(?:用|改|設|轉)\s*(\S+?)(?:\s|做|STT|TTS|model)',
        output, _vre.IGNORECASE
    )
    if _USER_DIRECTIVE:
        directive = _USER_DIRECTIVE.group(1).strip('"\'\,.）)').lower()
        if len(directive) >= 3:
            for pname in (cfg or {}).get("providers", {}):
                if directive in pname.lower() or pname.lower() in directive:
                    prov_models = [
                        m.get("id", "").lower()
                        for m in (cfg or {}).get("providers", {}).get(pname, {}).get("models", [])
                    ]
                    caps = (cfg or {}).get("capabilities", {})
                    in_use = any(
                        isinstance(cap, dict) and cap.get("model", "").lower() in prov_models
                        for cap in caps.values()
                    )
                    if not in_use:
                        corrections.append(
                            f"User requested '{directive}' but {pname} models not "
                            f"configured for any capability — config was not updated"
                        )
    
    # Pattern 4: Model name verification — check claimed model IDs exist in providers
    _MODEL_CLAIMS = _vre.findall(
        r'(?:model[:=])([\w.-]+?)(?:[\s,\)]|$)', output
    )
    if _MODEL_CLAIMS:
        valid_models = set()
        for pname, pcfg in (cfg or {}).get("providers", {}).items():
            for m in pcfg.get("models", []):
                mid = m.get("id", "")
                if mid:
                    valid_models.add(mid)
        for mc in _MODEL_CLAIMS:
            mc_clean = mc.rstrip('"\'\,.)').strip()
            if mc_clean and mc_clean not in valid_models:
                corrections.append(
                    f"Claims model='{mc_clean}' but this model does not exist in any "
                    f"provider's model list — model name may be fabricated"
                )
    
    if corrections:
        import yaml as _vyaml2
        # Build a compact summary of what's actually configured
        _actual_caps = (cfg or {}).get("capabilities", {})
        _actual_lines = []
        for _cap_name, _cap_cfg in _actual_caps.items():
            if isinstance(_cap_cfg, dict):
                _m = _cap_cfg.get("method", "?")
                _mod = _cap_cfg.get("model", "")
                _actual_lines.append(f"  {_cap_name}: method={_m}" + (f", model={_mod}" if _mod else ""))
        _actual_block = "\n".join(_actual_lines) if _actual_lines else "  (none)"
        
        output += (
            "\n\n---\n"
            "## [SYSTEM] POST-TURN VERIFICATION FAILED\n"
            + "\n".join(f"- {c}" for c in corrections)
            + "\n\n"
            "Auto-recovered: actual config state loaded.\n"
            f"<pre>Current capabilities:\n{_actual_block}</pre>\n"
            "The claims above did not match config.yaml. They have been overridden by the actual values shown above."
        )
    
    return output


# ── Main agent loop ────────────────────────────────────────────

MAX_STEP_RETRIES = 3
MAX_CONSECUTIVE_FAILURES = 3
MAX_STEP_SECONDS = 300  # individual step timeout (was 60 — TTS/API calls + edge-tts install need 2-5 min)


# ── Write tools that modify system state — auto-verify after execution ──
_WRITE_TOOLS = {"write_file", "patch", "config", "delegate_task", "execute_code", "cronjob"}


def _verify_after_write(name: str, args: dict, result: str, data_dir: Optional[Path] = None) -> str:
    """Code-enforced verify after write tools. Reads back to confirm changes persist.
    
    Returns original result with verification note appended.
    Does NOT block execution — just flags for the LLM to see.
    """
    if name not in _WRITE_TOOLS:
        return result
    if name == "config" and args.get("action", "") in ("get", "list", "validate"):
        return result

    base = data_dir or Path.home() / ".baw"
    notes = []

    try:
        if name == "write_file":
            path = args.get("path", "")
            if path:
                p = Path(path).expanduser()
                if p.exists() and p.stat().st_size > 0:
                    notes.append(f"✅ {p.name} written ({p.stat().st_size} bytes)")
                else:
                    notes.append(f"⚠️ {p.name} — read-back failed (not found or empty)")

        elif name == "patch":
            path = args.get("path", "")
            new_text = args.get("new_string", "")[:60]
            if path and new_text:
                p = Path(path).expanduser()
                if p.exists():
                    content = p.read_text(encoding="utf-8", errors="replace")
                    if new_text in content:
                        notes.append(f"✅ {p.name} — patch confirmed in file")
                    else:
                        notes.append(f"⚠️ {p.name} — patch string not found (may use fuzzy match)")
                else:
                    notes.append(f"⚠️ {p.name} — file not found after patch")

        elif name == "config":
            action = args.get("action", "")
            if action == "set":
                section = args.get("section", "")
                key = args.get("key", "")
                cfg_file = base / "config.yaml"
                if cfg_file.exists():
                    import yaml
                    try:
                        cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
                        if section and key:
                            val = cfg.get(section, {}).get(key, "⛔ NOT FOUND")
                            notes.append(f"✅ config.{section}.{key} = {val}")
                        elif section:
                            val = cfg.get(section, "⛔ NOT FOUND")
                            notes.append(f"✅ config.{section} = (exists)")
                    except Exception as e:
                        notes.append(f"⚠️ config read-back error: {e}")
            elif action == "set_key":
                key_name = args.get("key_name", "")
                env_file = base / ".env"
                if env_file.exists() and key_name:
                    content = env_file.read_text(encoding="utf-8", errors="replace")
                    if key_name in content:
                        notes.append(f"✅ .env contains {key_name}")
                    else:
                        notes.append(f"⚠️ {key_name} not found in .env")

        elif name == "execute_code":
            # Verify by checking for FileNotFoundError in the result
            if "FileNotFoundError" in result or "Permission denied" in result:
                notes.append("⚠️ execute_code reported file access error")
            else:
                notes.append("✅ execute_code completed")

        elif name == "cronjob":
            action = args.get("action", "")
            if action in ("create", "update", "resume"):
                notes.append("✅ cron job registered")

    except Exception as e:
        notes.append(f"⚠️ verify error: {e}")

    if notes:
        return result + "\n" + "\n".join(notes)
    return result


def run_agent(
    prompt: str,
    config: dict,
    model_id: Optional[str] = None,
    data_dir: Optional[Path] = None,
    verbose: bool = False,
    interactive: bool = False,
    fresh_start: bool = False,
    mode: Optional[str] = None,
    conversation_history: Optional[list[dict]] = None,
    progress_callback: Optional[Callable[..., None]] = None,
    max_tool_turns: int = MAX_TOOL_TURNS,
) -> tuple[str, dict]:
    """Run BAW agent with debate-first, execute-second flow.

    Phase 1 — Court: Devil + Angel analyze independently.
    Phase 2 — Neutral response & debate (interactive only).
    Phase 3 — Execute: plan → execute → verify → report.

    If fresh_start=True, SOUL.md and memories are bypassed entirely.
    conversation_history: list of {role, content, ...} dicts from previous turns.
    progress_callback: called on each tool call or milestone (for timeout refresh).
    Returns: (response_text, info_dict)
    """
    # ── Initialise ──
    # CRITICAL: Register all tools BEFORE any LLM call.
    # Without this, get_openai_tools() returns empty → model has no tools to call.
    from tools import register_all
    register_all()

    model = get_model(config, model_id)
    model_temperature = getattr(model, "temperature", 0.7)

    # ── Natural language routing — no keyword rules ──
    # The LLM understands the user's intent naturally and uses
    # the config tool to switch models if needed.
    perm = PermissionEngine(config)
    mem = MemoryStore(data_dir or Path.home() / ".baw")
    checkpointer = Checkpointer()

    # Init search providers
    try:
        from .search import _auto_discover as _init_search
        _init_search()
    except ImportError:
        logger.debug("[loop] search module not present (optional)")
    except Exception as _se:
        logger.warning(f"[loop] search provider init failed: {_se}")

    # ── Capability config health check & auto-heal ──
    try:
        from .capabilities import validate_capability_health
        drift_fixes = validate_capability_health(config)
        if drift_fixes:
            for fix in drift_fixes:
                logger.warning(f"[health] {fix['capability']}: {fix['issue']} → {fix['fix_applied']}")
            # Sync auto-heal changes to user config (NOT full merged dump)
            # Use the config tool's mechanism to write only changed keys
            # so user config isn't overwritten by repo defaults
            try:
                import yaml
                user_cfg_path = (data_dir or Path.home() / ".baw") / "config.yaml"
                if user_cfg_path.exists():
                    user_cfg = yaml.safe_load(user_cfg_path.read_text(encoding="utf-8")) or {}
                    changed = False
                    caps = config.get("capabilities", {})
                    for cap_name in caps:
                        if cap_name not in user_cfg.setdefault("capabilities", {}):
                            user_cfg["capabilities"][cap_name] = caps[cap_name]
                            changed = True
                    if changed:
                        import os as _os
                        if user_cfg_path.exists() and not _os.access(user_cfg_path, _os.W_OK):
                            user_cfg_path.chmod(0o644)
                        user_cfg_path.write_text(
                            yaml.dump(user_cfg, allow_unicode=True, default_flow_style=False),
                            encoding="utf-8",
                        )
            except Exception as _sc:
                logger.warning(f"[health] Could not sync drift fixes to user config: {_sc}")
            logger.info(f"[health] Auto-healed {len(drift_fixes)} capability config drift(s)")
    except Exception as _he:
        logger.debug(f"[health] capability health check skipped: {_he}")

    # ── Sync schedule.yaml from project to runtime (factory defaults) ──
    try:
        _baw_home = Path(os.environ.get("BAW_HOME", ""))
        _runtime_dir = Path(data_dir or Path.home() / ".baw")
        # Ensure reports dir exists (for cron delivery)
        (_runtime_dir / "reports").mkdir(parents=True, exist_ok=True)
        if _baw_home.exists():
            _src_schedule = _baw_home / "schedule.yaml"
            _dst_schedule = _runtime_dir / "schedule.yaml"
            if _src_schedule.exists():
                _src_mtime = _src_schedule.stat().st_mtime
                _dst_mtime = _dst_schedule.stat().st_mtime if _dst_schedule.exists() else 0
                if _src_mtime > _dst_mtime or not _dst_schedule.exists():
                    import shutil as _shutil
                    _shutil.copy2(str(_src_schedule), str(_dst_schedule))
                    logger.info(f"[startup] schedule.yaml synced from project ({_src_schedule.name})")
    except Exception as _ss:
        logger.debug(f"[startup] schedule.yaml sync skipped: {_ss}")

    # ── KG auto-curation: lightweight check on every startup ──
    try:
        from tools.kg_curator import stats, curate
        kg_report = stats()
        logger.info(f"[startup] {kg_report.split(chr(10))[0]}")
        # Auto-curate if signal ratio below 15% and last curation >12h ago
        import json as _json
        _kg_data = _json.loads((Path(data_dir or Path.home() / ".baw") / "knowledge_graph.json").read_text(encoding="utf-8"))
        _last = _kg_data.get("_curated_at", "")
        _should_run = False
        if _last:
            from datetime import datetime, timezone
            _last_dt = datetime.fromisoformat(_last)
            _hours_since = (datetime.now(timezone.utc) - _last_dt).total_seconds() / 3600
            _should_run = _hours_since > 12
        else:
            _should_run = True  # never curated

        total_triples = _kg_data.get("triples", [])
        signal_count = sum(1 for t in total_triples if t.get("r", "") not in {"mentioned_in", "tagged"})
        noise_ratio = 1 - (signal_count / max(len(total_triples), 1))
        if noise_ratio > 0.85 and _should_run:
            result = curate(action="curate", dry_run=False)
            logger.info(f"[startup] KG auto-curated: {result.split(chr(10))[1]}")
    except Exception as _ke:
        logger.debug(f"[startup] KG auto-curation skipped: {_ke}")

    system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start)

    # ── Fusion pre-check: parallel inference for complex tasks ──
    if mode in ("auto", "fusion") and not fresh_start:
        try:
            from .fusion_router import should_fuse, classify_task, route, run_parallel, synthesize
            if should_fuse(prompt, mode):
                task_type = classify_task(prompt)
                fusion_models = route(task_type)
                # Build messages from system prompt + prompt
                _fusion_msgs = [{"role": "system", "content": system_prompt}]
                if conversation_history:
                    _fusion_msgs.extend(conversation_history)
                _fusion_msgs.append({"role": "user", "content": prompt})
                logger.info(f"[Fusion] Running {len(fusion_models)} models in parallel ({task_type})")
                fusion_results = run_parallel(fusion_models, _fusion_msgs, config, timeout=45)
                if fusion_results:
                    _synthesized = synthesize(fusion_results, prompt, config)
                    if _synthesized and len(_synthesized) > 100:
                        # Inject synthesized result as pre-loaded context
                        system_prompt += (
                            f"\n\n## Fusion Pre-Context\n"
                            f"The following was synthesized from "
                            f"{len([r for r in fusion_results if r.get('status') == 'ok'])} "
                            f"models responding to the user's request. Use this as a starting point:\n"
                            f"{_synthesized[:3000]}\n\n"
                            f"You may build on this or refine it. Do NOT simply repeat it — add value.\n"
                        )
                        logger.info(f"[Fusion] Injected synthesized context ({len(_synthesized)} chars)")
        except Exception as _fe:
            logger.warning(f"[Fusion] Pre-check failed: {_fe}")

    # ── Tier-based routing decision ──
    from .router import route_task, INLINE_DIRECT, INLINE_WITH_HINT
    _ctx_tokens_est = len(prompt) // 3  # rough estimate
    _route = route_task(
        prompt, config,
        estimated_tool_count=0,  # updated after first tool call
        context_tokens=_ctx_tokens_est,
    )
    logger.info(f"[router] {_route.reasoning}")
    # Override model_id if router picked a better tier-specific one
    # AND user didn't explicitly pass model_id
    if not model_id and _route.model_id:
        try:
            model = get_model(config, _route.model_id)
            model_id = _route.model_id
            model_temperature = getattr(model, "temperature", 0.7)
            logger.info(f"[router] using tier-appropriate model: {model_id}")
        except Exception as _re:
            logger.warning(f"[router] could not load model {_route.model_id}: {_re}")

    # ── Detect tone switch ──
    from .tone import detect_tone_switch, format_tone_confirmation
    old_tone = config.get("tone", {}).get("default", "casual")
    new_tone = detect_tone_switch(prompt)
    tone_change = False
    if new_tone and new_tone != old_tone:
        config.setdefault("tone", {})["default"] = new_tone
        system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start)
        tone_change = True

    # ── Selective memory search: skip for trivial prompts ──
    _search_prompt = (prompt or "").strip()
    _greetings = {"hi", "hello", "hey", "thanks", "thank", "ok", "okay", "bye",
                  "good", "done", "搞掂", "好", "ok", "hi", "hello", "早安", "晚安"}
    _should_search = (
        len(_search_prompt) > 15
        and _search_prompt.lower().strip() not in _greetings
        and not all(c in _search_prompt for c in _search_prompt if c in ".,!? \\n\\t")
    )
    memories = []
    mem_text = ""
    if _should_search:
        # 1. Memory store search (existing)
        memories = mem.search(_search_prompt, limit=3)
        mem_parts = []
        if memories:
            for m in memories:
                mem_parts.append(f"- [MEM:{m['score']:.2f}] {m['content']}")

        # 2. Weighted KG search — signal > noise
        try:
            import json as _kg_json
            from pathlib import Path as _P
            _kg_path = _P(data_dir or _P.home() / ".baw") / "knowledge_graph.json"
            if _kg_path.exists():
                _kg_data = _kg_json.loads(_kg_path.read_text(encoding="utf-8"))
                _triples = _kg_data.get("triples", [])
                _noise_rels = {"mentioned_in", "tagged"}
                _query_lower = _search_prompt.lower()
                _query_words = set(w for w in _query_lower.split() if len(w) > 2)

                # Score each triple by relevance to query
                _kg_hits: list[tuple[str, float, str]] = []  # (display, weight, relation)
                for _t in _triples:
                    _s = (_t.get("s", "") or "").lower()
                    _o = (_t.get("o", "") or "").lower()
                    _r = _t.get("r", "")
                    _is_noise = _r in _noise_rels

                    # Match: subject or object contains query keyword
                    _kw_match = any(kw in _s or kw in _o for kw in _query_words)
                    if not _kw_match:
                        # Also try full query substring
                        if _query_lower not in _s and _query_lower not in _o:
                            continue

                    # Weight: signal gets 3x priority over noise
                    _base_weight = 0.2 if _is_noise else 1.0
                    _match_bonus = 0.3 if _query_lower in _s or _query_lower in _o else 0.0
                    _weight = _base_weight + _match_bonus
                    _tag = "[SIG]" if not _is_noise else "[REF]"
                    _display = f"- {_tag}:{_weight:.2f} {_t.get('s','')} --{_r}-> {_t.get('o','')}"
                    _kg_hits.append((_display, _weight, _r))

                # Sort by weight descending, take top 5
                _kg_hits.sort(key=lambda x: -x[1])
                # Cap noise per entity: max 3 mentions per subject
                _seen_subjects: dict[str, int] = {}
                for _disp, _w, _r in _kg_hits:
                    if _r in _noise_rels:
                        _subj = _disp.split("'s'")[1] if "'s'" in _disp else ""
                        _cnt = _seen_subjects.get(_subj, 0)
                        if _cnt >= 3:
                            continue
                        _seen_subjects[_subj] = _cnt + 1
                    mem_parts.append(_disp)
                    if len(mem_parts) >= 8:  # cap total results
                        break

        except Exception:
            pass  # KG search is additive; silence on error

        if mem_parts:
            mem_text = "\n".join(mem_parts)

    # ── Auto-deliver cron reports if fresh (last 24h, undelivered) ──
    from pathlib import Path as _PDir
    _reports_dir = _PDir(data_dir or _PDir.home() / ".baw") / "reports"
    if _reports_dir.exists() and not fresh_start:
        _delivered_mark = _reports_dir / ".delivered_at"
        _last_delivered = 0.0
        if _delivered_mark.exists():
            try:
                _last_delivered = float(_delivered_mark.read_text().strip())
            except (ValueError, OSError):
                _last_delivered = 0.0
        _now_ts = time.time()
        _all_reports = sorted(_reports_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
        _fresh_reports = [f for f in _all_reports if f.stat().st_mtime > _last_delivered]
        if _fresh_reports:
            _report_header = "\n[WEEKLY_REPORT] Recent maintenance reports (auto-delivered):"
            _report_lines = [_report_header]
            for _f in _fresh_reports[:3]:
                _age_h = (_now_ts - _f.stat().st_mtime) / 3600
                if _age_h > 48:
                    continue  # skip stale
                _content = _f.read_text(encoding="utf-8").strip()
                _short = _content[:500]
                _report_lines.append(f"--- {_f.stem} ({_age_h:.0f}h old) ---")
                _report_lines.append(_short)
            if len(_report_lines) > 1:
                mem_parts = [mem_text] if mem_text else []
                mem_parts.append("\n".join(_report_lines))
                mem_text = "\n".join(mem_parts)
                # Mark delivered
                _delivered_mark.write_text(str(_now_ts))

    reset_cost()
    session_cost = 0.0
    court_result = None
    _delegation_results = []
    _synthesis_results = []

    # ── Mode selection (auto default with 4 options) ──
    # auto:    LLM self-judges complexity — no keyword/pattern matching
    # quick:   Direct execution, skip court/plan/adversarial
    # hybrid:  Moderate processing with some auditing
    # tight:   Full court (Devil+Angel), plan phase, adversarial checks
    _mode_value = (mode or config.get("mode", "auto")).lower()
    if _mode_value not in ("auto", "quick", "hybrid", "tight"):
        _mode_value = "auto"
    _mode = _mode_value  # used by downstream court/complexity logic
    if _mode == "auto":
        _mode = "hybrid"  # auto → hybrid: court for complex tasks, fast for simple

    # ── Task classification (Layer 1) ──
    _classification = _classify_task_type(prompt)
    _task_type = _classification["type"]
    logger.info(f"[classify] {_classification['reason']}")

    # ── Build system prompt ──
    system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start)

    # ── Phase 1: Build context ──
    ctx = Context(system_prompt=system_prompt, temperature=model_temperature)
    ctx.task_type = _task_type
    # audit not needed — LLM + permission engine handle safety naturally

    # Inject conversation history (between system prompt and current prompt)
    _history_msg_count = 1  # system message at index 0
    if conversation_history:
        for _hmsg in conversation_history:
            _role = _hmsg.get("role", "")
            _content = _hmsg.get("content", "")
            if _role == "user":
                ctx.add_user(_content)
                _history_msg_count += 1
            elif _role == "assistant":
                ctx.add_assistant(_content, _hmsg.get("tool_calls"),
                                  _hmsg.get("reasoning_content"))
                _history_msg_count += 1
            elif _role == "tool":
                ctx.add_tool_result(
                    _hmsg.get("tool_call_id", "call_xxx"),
                    _hmsg.get("name", ""),
                    _content or "",
                )
                _history_msg_count += 1

        # ── Sequence validation: fix truncated history mid-tool-sequence ──
        # (P0: conversation_history[-60:] may cut between assistant(tool_calls) and tool(result))
        # Fix 1: strip dangling tool_calls from assistant at end (no following tool result)
        for _i in range(len(ctx.messages) - 1):
            _msg = ctx.messages[_i]
            if _msg.role != "assistant" or not _msg.tool_calls:
                continue
            _next = ctx.messages[_i + 1]
            if _next.role != "tool":
                logger.warning(
                    f"[Loop] Stripped dangling tool_calls from assistant message {_i} "
                    f"(not followed by tool response — history truncated)"
                )
                _msg.tool_calls = None
        # Also strip last message's tool_calls if it's the tail
        if ctx.messages and ctx.messages[-1].role == "assistant" and ctx.messages[-1].tool_calls:
            logger.warning(
                f"[Loop] Stripped dangling tool_calls from trailing assistant message "
                f"(history truncated / interrupted session)"
            )
            ctx.messages[-1].tool_calls = None

        # Fix 2: strip orphaned tool results (tool role at start with no preceding assistant tool_calls)
        # This happens when session[-60:] truncation cuts off the assistant message
        # but keeps the following tool result messages.
        _cleaned = []
        _expecting_tool = False
        for _m in ctx.messages:
            if _m.role == "assistant" and _m.tool_calls:
                _expecting_tool = True
                _cleaned.append(_m)
            elif _m.role == "tool":
                if _expecting_tool:
                    _cleaned.append(_m)
                    _expecting_tool = False
                else:
                    logger.warning(
                        f"[Loop] Stripped orphaned tool result (no preceding tool_calls) — "
                        f"truncated history or state corruption"
                    )
            else:
                _cleaned.append(_m)
        if _cleaned != ctx.messages:
            ctx.messages = _cleaned

    # ── Quick Mode marker for new-message extraction ──
    _pre_prompt_count = _history_msg_count
    ctx.add_user(prompt)

    # ── Helper: extract new messages from this turn ──
    def _extract_new_msgs(_ctx, _since_idx):
        _out = []
        for _m in _ctx.messages[_since_idx:]:
            if hasattr(_m, 'role'):
                _d = {"role": _m.role, "content": _m.content or ""}
                if hasattr(_m, 'tool_calls') and _m.tool_calls:
                    _d["tool_calls"] = _m.tool_calls
                if hasattr(_m, 'tool_call_id') and getattr(_m, 'tool_call_id', None):
                    _d["tool_call_id"] = _m.tool_call_id
                if hasattr(_m, 'name') and getattr(_m, 'name', None):
                    _d["name"] = _m.name
                if _m.role != "system":
                    _out.append(_d)
            elif isinstance(_m, dict):
                if _m.get("role") != "system":
                    _out.append(dict(_m))
        return _out
    if mem_text:
        ctx.add_user(f"Relevant memories:\n{mem_text}")

    # ── Quick Mode: no court, no plan, just execute ──
    if _mode == "quick":
        skip_verify = True
        skip_adversarial = True
        skip_plan = True
        max_recovery = 1
        try:
            fb = call_llm_with_fallback(
                config, ctx.to_openai_messages(),
                tools=get_openai_tools(), temperature=model_temperature,
                max_tokens=MODE_MAX_TOKENS.get(_mode, 4096),
            )
        except RuntimeError as _llm_err:
            return f"{_llm_err}", {
                "cost": round(session_cost, 4),
                "model": f"{model.provider}/{model.id}",
                "iterations": 0,
                "steps": 0,
                "mode": "quick",
                "error": str(_llm_err)[:200],
            }
        quick_resp = fb.response
        q_cost = calculate_cost(model, quick_resp.input_tokens, quick_resp.output_tokens)
        session_cost += q_cost
        record_cost(f"{model.provider}/{model.id}", quick_resp.input_tokens, quick_resp.output_tokens, q_cost)
        ctx.add_assistant(quick_resp.content, quick_resp.tool_calls,
                          getattr(quick_resp, 'reasoning_content', None))

        # Execute any tool calls (with hard iteration cap for quick mode)
        _quick_turns = 0
        while quick_resp.tool_calls:
            if _quick_turns >= MAX_QUICK_TOOL_TURNS:
                ctx.add_user("[SYSTEM] You have exceeded the maximum tool iterations for quick mode. Synthesize results now. Do NOT call more tools.")
                break
            _quick_turns += 1
            for tc in quick_resp.tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if progress_callback:
                    progress_callback("tool", name, args)
                _show_progress = verbose or interactive
                if _show_progress:
                    print(f"\033[90m[FIX] {name}", end="", flush=True)
                perm_result = perm.check(name, args)
                if perm_result["decision"] == "deny":
                    if _show_progress:
                        print(f" ⛔ BLOCKED: {perm_result['reason']}\033[0m")
                    ctx.add_tool_result(tc.get("id", ""), name, f"[BLOCKED] {perm_result['reason']}")
                    continue
                if _show_progress:
                    _arg_str = str(args)[:80]
                    print(f" {_arg_str}", end="", flush=True)
                exe_result = execute_tool(name, args)
                # ── Loop detection: track consecutive failures ──
                if "Error" in exe_result[:100] or "Traceback" in exe_result:
                    from core.watchdog import track_consecutive_failure, _recover_restart
                    if track_consecutive_failure(name):
                        _recover_restart(f"{name} failed 5x consecutive")
                else:
                    from core.watchdog import clear_consecutive_failures
                    clear_consecutive_failures(name)
                # ── Auto-verify after write tools ──
                exe_result = _verify_after_write(name, args, exe_result, data_dir)
                if _show_progress:
                    print(f" \033[32m[OK]\033[0m", flush=True)
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)

            # Next LLM call
            fb = call_llm_with_fallback(
                config, ctx.to_openai_messages(),
                tools=get_openai_tools(), temperature=model_temperature,
                max_tokens=MODE_MAX_TOKENS.get(_mode, 4096),
            )
            quick_resp = fb.response
            q_cost = calculate_cost(model, quick_resp.input_tokens, quick_resp.output_tokens)
            session_cost += q_cost
            record_cost(f"{model.provider}/{model.id}", quick_resp.input_tokens, quick_resp.output_tokens, q_cost)
            ctx.add_assistant(quick_resp.content, quick_resp.tool_calls,
                          getattr(quick_resp, 'reasoning_content', None))

        # Collect output
        output = ""
        if tone_change:
            output += format_tone_confirmation(old_tone, new_tone) + "\n\n"

        # Determine final response content
        final_content = quick_resp.content or ""

        # If final LLM response is empty but we executed tool calls,
        # auto-generate a completion summary from tool results
        if not final_content and ctx.messages:
            tool_summaries = []
            # Extract last N tool results for summary
            for m in reversed(ctx.messages):
                role = m.role if hasattr(m, 'role') else m.get("role", "")
                content = m.content or "" if hasattr(m, 'content') else m.get("content", "") or ""
                if role == "tool" and content:
                    # Truncate long tool output
                    tool_summaries.append(content[:300])
                    if len(tool_summaries) >= 3:
                        break
            if tool_summaries:
                # Determine status from tool output
                _has_errors = any("[FAILED" in s or "[SKIPPED" in s or "Traceback" in s
                                  or "Error:" in s for s in tool_summaries)
                if _has_errors:
                    _prefix = "❗ 任務執行有錯誤，需要跟進："
                else:
                    _prefix = "✅ 任務完成："
                final_content = _prefix + "\n\n" + "\n".join(
                    f"• {s.strip()}" for s in reversed(tool_summaries)
                )

        # Fallback: use last non-empty assistant message
        if not final_content:
            for msg in ctx.messages:
                role = msg.role if hasattr(msg, 'role') else msg.get("role", "")
                content = msg.content or "" if hasattr(msg, 'content') else msg.get("content", "") or ""
                if role == "assistant" and content:
                    final_content = content

        # ── Post-LLM honesty gate: catch fake-completion in LLM's own final reply ──
        # If delegation_results contain errors OR synthesis_results contain failures,
        # but LLM's final content claims "5/5 done" or "100%", override with actual
        # errors. This is the LAST line of defense against LLM fabrication.
        _has_fake_success = any(
            fake in (final_content or "")
            for fake in ("5/5 (100%)", "5/5 done", "100% done", "[OK] Done —",
                         "Done — ", "completed all", "tested all", "Done 1/1 (100%)",
                         "Done 3/3 (100%)", "Done 2/2 (100%)",
                         "我而家", "我現在", "I'll run", "I'll execute",
                         "I will run", "I will execute")
        )
        if _has_fake_success:
            # Check BOTH delegation_results (all steps) and synthesis_results (successful only)
            _all_results = (_delegation_results or []) + (_synthesis_results or [])
            _has_errors = any(
                tag in s for s in _all_results
                for tag in ("[FAILED", "[SKIPPED", "Traceback", "Error: ",
                            "Errno", "FileNotFoundError", "No such file")
            )
            if _has_errors:
                _error_lines = [s for s in _all_results[:10]
                                if any(t in s for t in ("[FAILED", "[SKIPPED", "Traceback",
                                                        "Errno", "FileNotFoundError", "No such file"))]
                final_content = (
                    "🚨 Fabrication detected — LLM reported success but "
                    f"{len(_error_lines)} step(s) actually failed:\n\n"
                    + "\n".join(f"  • {line[:200]}" for line in _error_lines[:5])
                    + "\n\nHonest status: see synthesis results above."
                )

        # ── Synthesis enforcement: ensure file sends come with explanation ──
        _has_media = "MEDIA:" in (final_content or "")
        _has_explanation = bool(final_content and len(final_content.strip()) > 50)
        if _has_media and not _has_explanation:
            # LLM sent files with no/poor explanation — force synthesis
            _tool_msgs = [
                m.content or "" for m in reversed(ctx.messages)
                if hasattr(m, 'role') and m.role == "tool" and m.content
            ][:3]
            _file_names = [
                line.replace("MEDIA:", "").strip()
                for line in (final_content or "").splitlines()
                if line.startswith("MEDIA:")
            ]
            _explanation = (
                f"📂 已生成 {len(_file_names)} 個檔案：\n"
                + "\n".join(f"  • {f}" for f in _file_names[:5])
            )
            if _tool_msgs:
                _details = _tool_msgs[0][:200]
                _explanation += f"\n\n{_details}"
            final_content = _explanation

        # ── Inject reasoning content if configured ──
        if config.get("display", {}).get("show_reasoning"):
            _reasoning = getattr(quick_resp, 'reasoning_content', None) or ""
            if _reasoning:
                output = f"[THOUGHT] {_reasoning}\n\n---\n\n{output}"

        output += final_content
        output += f"\n{format_cost_summary()}"
        output = output.strip()
        
        # ── Context compaction: summarize old turns before returning ──
        _q_total = ctx.total_chars()
        if _q_total > 60000:
            _q_compacted, _q_notify, _q_summary = ctx.compact(threshold_chars=60000, keep_recent_turns=12)
            if _q_compacted > 0:
                logger.info(f"[Loop] Quick mode context compacted: {_q_compacted} turns ({_q_total} → {ctx.total_chars()} chars)")
        
        try:
            mem.remember(f"User: {prompt[:150]} → BAW: {final_content[:150]}")
        except Exception as _me:
            logger.warning(f"[loop] memory save failed: {_me}")
        output = _verify_post_turn_claims(output, data_dir)
        return output, {
            "cost": round(session_cost, 4),
            "model": f"{model.provider}/{model.id}",
            "iterations": 0,
            "steps": 0,
            "mode": "quick",
            "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        }

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Court — Independent dual-voice analysis
    # (Bypassed for pure information tasks; activated for system-modifying,
    #  irreversible, or architectural decisions)
    # ═══════════════════════════════════════════════════════════════
    from .adversarial import AdversarialCourt
    from .token_killer import should_activate_court, estimate_task_complexity
    _skip_court = (not should_activate_court(prompt, mode=_mode))
    _complexity = estimate_task_complexity(prompt, mode=_mode)
    # ── Scale tool cap by task complexity ──
    _base_turns = _TOOL_TURNS_BY_COMPLEXITY.get(_complexity, MAX_TOOL_TURNS)
    if max_tool_turns == MAX_TOOL_TURNS and _base_turns > MAX_TOOL_TURNS:  # only override default
        max_tool_turns = _base_turns
        logger.info(f"[loop] Tool cap scaled to {max_tool_turns} ({_complexity} task)")
    if _skip_court:
        logger.info(f"[loop] Court bypassed — safe task ({_complexity})")
        court_result = None
    # Load per-side model config (both fall back to default model if unset)
    adv_cfg = config.get("adversarial", {})
    angel_model_id = adv_cfg.get("angel_model")
    devil_model_id = adv_cfg.get("devil_model")
    angel_model = None
    devil_model = None
    try:
        if angel_model_id:
            angel_model = get_model(config, angel_model_id)
            logger.debug(f"[loop] angel_model loaded: {angel_model_id}")
        if devil_model_id:
            devil_model = get_model(config, devil_model_id)
            logger.debug(f"[loop] devil_model loaded: {devil_model_id}")
    except ValueError as _ve:
        # Model not in config — fall back to default for that side
        logger.warning(f"[loop] court model not found ({_ve}), using default")
    except Exception as _ce:
        logger.warning(f"[loop] court model load failed: {_ce}")
    if not _skip_court:
        court = AdversarialCourt(
            model, system_prompt, config,
            angel_model=angel_model, devil_model=devil_model,
        )
    else:
        court = None
    court_enabled = config.get("adversarial", {}).get("enabled", True) and _mode in ("tight", "quick", "hybrid")

    # M5-D7: court v2 path. Uses the black-and-white court (file_case_sync)
    # for hybrid/tight modes. Auto-enables when mode >= hybrid.
    # Falls through to the legacy AdversarialCourt path on any failure
    # (so existing behavior is preserved).
    use_court_v2 = config.get("court", {}).get("v2_enabled", _mode in ("hybrid", "tight"))
    if _skip_court:
        use_court_v2 = False
    court_v2_briefing = None
    if use_court_v2 and not court_enabled:
        court_enabled = True  # v2 subsumes the inline path below
    if use_court_v2:
        try:
            from .court import file_case_sync, CourtTier
            # Estimate tier from prompt length; for now route to tier 2
            # (the major court) which is where the parallel Devil/Angel
            # logic lives. Tier 0/1 don't need court v2.
            _v2_score = len(prompt or "")
            _v2_tier = CourtTier.TIER_2_MAJOR if _v2_score > 40 else CourtTier.TIER_1_MINOR
            _v2_case = file_case_sync(
                goal=prompt,
                user_id=str(config.get("_user_id", "default")),
                caller_model_id=model_id or "",
                force_tier=_v2_tier,
            )
            court_v2_briefing = {
                "case_id": _v2_case.case_id,
                "tier": _v2_case.tier.value if _v2_case.tier else None,
                "verdict": _v2_case.verdict.value if _v2_case.verdict else None,
                "score": _v2_case.score,
                "elapsed_sec": getattr(_v2_case, "elapsed_sec", 0.0),
            }
            # Pull prosecutor / angel evidence to inject into context.
            _v2_pros = ""
            _v2_angel = ""
            for ev in (_v2_case.evidence or []):
                _role = ev.get("role", "")
                if _role == "PROSECUTOR" and not _v2_pros:
                    _v2_pros = ev.get("content", "")
                if _role == "ANGEL" and not _v2_angel:
                    _v2_angel = ev.get("content", "")
            court_result = {
                "devil": {"content": _v2_pros, "score": _v2_case.score or 7,
                          "tokens_in": 0, "tokens_out": 0, "cost": 0.0},
                "angel": {"content": _v2_angel, "score": _v2_case.score or 7,
                          "tokens_in": 0, "tokens_out": 0, "cost": 0.0},
                "devil_score": _v2_case.score or 7,
                "angel_score": _v2_case.score or 7,
                "score_gap": 0,
                "agreement_level": "court-v2",
            }
            logger.info(
                f"[loop] court v2: {_v2_case.case_id} tier={_v2_case.tier.value if _v2_case.tier else '?'} "
                f"verdict={_v2_case.verdict.value if _v2_case.verdict else '?'} "
                f"score={_v2_case.score}"
            )

            # ── Inject court verdict into LLM context ──
            _v2_verdict_icon = {
                "approved": "✅", "retry": "🔁",
                "appeal": "📤", "dismissed": "🚫", "stay": "⏸️",
            }.get(_v2_case.verdict.value if _v2_case.verdict else "", "❓")
            _v2_tier_names = {0: "Fast Lane", 1: "Minor", 2: "Major", 3: "Supreme"}
            _v2_tier_name = _v2_tier_names.get(_v2_case.tier.value if _v2_case.tier else -1, "?")
            _v2_court_info = (
                f"⚖️ 法庭評審 (#{_v2_case.case_id})\n"
                f"  Tier: {_v2_tier_name} | 判決: {_v2_verdict_icon} {_v2_case.score}/10\n"
                f"  Defendant: {_v2_case.defendant_model} | Judge: {_v2_case.judge_model}\n"
                f"  用時: {getattr(_v2_case, 'elapsed_sec', 0):.1f}s"
            )
            if _v2_pros:
                _v2_court_info += f"\n  🖤 Devil: {_v2_pros[:200]}"
            if _v2_angel:
                _v2_court_info += f"\n  🤍 Angel: {_v2_angel[:200]}"
            if _v2_case.verdict and _v2_case.verdict.value == "dismissed":
                _v2_court_info += f"\n  🚫 駁回原因: {_v2_case.reason[:200]}"
            # Inject as system message (not user) so LLM knows verdict without distorting conversation
            ctx.add_user(f"[COURT VERDICT]\n{_v2_court_info}")
        except Exception as _ce:
            logger.warning(f"[loop] court v2 init failed ({_ce}); falling back to inline path")
            use_court_v2 = False

    if court_enabled and not use_court_v2 and not _skip_court:
        if verbose:
            print("\n  [COURT] Court: Devil + Angel analyzing independently...")
        if progress_callback:
            progress_callback("court", "", {})
        verdict = court.hold_court(prompt, mem_text)
        court_result = verdict

        session_cost += (verdict["devil"]["cost"] + verdict["angel"]["cost"])
        record_cost(
            f"{model.provider}/{model.id}",
            verdict["devil"]["tokens_in"], verdict["devil"]["tokens_out"],
            verdict["devil"]["cost"],
        )
        record_cost(
            f"{model.provider}/{model.id}",
            verdict["angel"]["tokens_in"], verdict["angel"]["tokens_out"],
            verdict["angel"]["cost"],
        )

        # Inject both analyses into context (not as a decision — as perspectives)
        court_context = (
            f"\n─── 👿 DEVIL'S INDEPENDENT ANALYSIS ───\n"
            f"{verdict['devil']['content']}\n"
            f"Score: {verdict['devil_score']}/10\n"
            f"─── 😇 ANGEL'S INDEPENDENT ANALYSIS ───\n"
            f"{verdict['angel']['content']}\n"
            f"Score: {verdict['angel_score']}/10\n"
            f"─── END COURT ───\n\n"
            f"Both perspectives are available. "
            f"Now respond from a NEUTRAL perspective — "
            f"you are NOT obligated to please the user. "
            f"Be honest. Be willing to disagree. "
            f"Your goal is to reach the best outcome through honest discussion."
        )
        ctx.add_user(court_context)

        if verbose:
            print(f"  [Court] Devil={verdict['devil_score']}/10 | Angel={verdict['angel_score']}/10 | Gap={verdict['score_gap']} ({verdict['agreement_level']})")

        # ── Active Challenge Gate: BAW challenges user on high-risk requests ──
        _devil_score = verdict.get("devil_score", 0)
        _challenge_prefix = ""
        if _devil_score >= 9:
            _challenge_prefix = (
                "\n\n## 🚨 ACTIVE CHALLENGE — Devil Score {}/10\n\n"
                "The Devil has flagged this request as EXTREMELY DANGEROUS (score {}/10).\n"
                "You MUST warn the user explicitly about the risks.\n"
                "List 2-3 specific dangers. Ask: 'Are you sure you want to proceed?'\n"
                "DO NOT execute until the user confirms.\n\n"
                "Devil's analysis:\n{}\n"
            ).format(_devil_score, _devil_score, verdict["devil"]["content"][:500])
        elif _devil_score >= 7:
            _challenge_prefix = (
                "\n\n## [WARN] ACTIVE CHALLENGE — Devil Score {}/10\n\n"
                "The Devil has flagged potential risks in this request (score {}/10).\n"
                "You MUST mention the Devil's concern and suggest a safer alternative.\n"
                "Proceed only after acknowledging the risk.\n\n"
                "Devil's analysis:\n{}\n"
            ).format(_devil_score, _devil_score, verdict["devil"]["content"][:500])
        elif _devil_score >= 4:
            _challenge_prefix = (
                "\n\n## 💡 NOTE — Devil Score {}/10\n\n"
                "The Devil has raised some concerns (score {}/10).\n"
                "Briefly mention the concern, but proceed with execution.\n\n"
                "Devil's analysis:\n{}\n"
            ).format(_devil_score, _devil_score, verdict["devil"]["content"][:500])

        if _challenge_prefix:
            court_context += _challenge_prefix

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: Neutral response
    # BAW responds from neutral perspective — may disagree with user
    # ═══════════════════════════════════════════════════════════════

    # Call LLM with court context (or without, for hybrid mode)
    fb = call_llm_with_fallback(
        config, ctx.to_openai_messages(),
        tools=get_openai_tools(),
        temperature=model_temperature,
    )
    neutral_response = fb.response
    n_cost = calculate_cost(model, neutral_response.input_tokens, neutral_response.output_tokens)
    session_cost += n_cost
    record_cost(f"{model.provider}/{model.id}", neutral_response.input_tokens, neutral_response.output_tokens, n_cost)
    ctx.add_assistant(neutral_response.content, neutral_response.tool_calls,
                      getattr(neutral_response, 'reasoning_content', None))

    # ── Phase 2.5: Execute tool calls (ALL modes) ──
    # Tool calls from neutral response are executed immediately,
    # then the result loop continues until the model produces text only.
    _resp = neutral_response
    _tool_turns = 0
    _extra_info = {}  # carry extra metadata (e.g. tool_cap_hit) to return dict
    _warned_15 = False
    _warned_18 = False
    _warned_20 = False
    _tool_retries: dict[str, int] = {}  # per-tool retry tracking for error policies
    _was_truncated = False  # will be set True if max_tokens cap was hit

    # ── Auto-steer: track original goal for focus adherence ──
    _focus_goal = prompt[:300] if prompt else ""
    _focus_interval = max(5, max_tool_turns // 5)  # check every ~20% of turns
    _focus_tool_history: list[str] = []

    while _resp.tool_calls:
        if _tool_turns >= max_tool_turns:
            ctx.add_user(
                f"[SYSTEM] You have exceeded {max_tool_turns} tool iterations. "

                "STOP calling tools. Below is EVERYTHING you have learned. "

                "Your ONLY job now: deliver a clear, complete answer to the user. "

                "No apologies. No 'let me check'. Just the answer."

            )
            # Force one final LLM call with no tools to get a text summary
            fb = call_llm_with_fallback(config, ctx.to_openai_messages(), tools=None, temperature=model_temperature, max_tokens=OUTPUT_MAX_TOKENS)
            _resp = fb.response
            _was_truncated = getattr(_resp, 'finish_reason', '') == 'length'
            if _was_truncated:
                logger.info(f"[Loop] Tool-cap output truncated at ~{OUTPUT_MAX_TOKENS} tokens ({_resp.output_tokens} used)")
            # ── Hard post-gen enforcement ──
            if _resp.content and len(_resp.content) > OUTPUT_MAX_CHARS:
                logger.warning(f"[Loop] Tool-cap post-gen: {len(_resp.content)} chars → {OUTPUT_MAX_CHARS}")
                _resp.content = _resp.content[:OUTPUT_MAX_CHARS - 50] + "\n\n[...truncated...]"
            # ── Signal to caller that goal was NOT fully achieved ──
            _extra_info["tool_cap_hit"] = True
            _extra_info["max_tool_turns"] = max_tool_turns
            break
        # ── Progressive tool cap warnings (proportional to max_tool_turns) ──
        _warn_pct = max_tool_turns / 100.0  # per-point percentage
        if _tool_turns >= int(60 * _warn_pct) and not _warned_15:
            ctx.add_user(f"[SYSTEM] ⏱ {_tool_turns}/{max_tool_turns} tool turns used — ask yourself: do you have enough data? If yes, synthesise and stop. If not, focus on must-haves only, skip nice-to-haves.")
            _warned_15 = True
        elif _tool_turns >= int(75 * _warn_pct) and not _warned_18:
            ctx.add_user(f"[SYSTEM] ⏱ {_tool_turns}/{max_tool_turns} tool turns used — start wrapping up. Prioritise results synthesis over additional data gathering.")
            _warned_18 = True
        elif _tool_turns >= int(85 * _warn_pct) and not _warned_20:
            ctx.add_user(f"[SYSTEM] ⏱ {_tool_turns}/{max_tool_turns} tool turns used — YOU MUST WRAP UP NOW. {max_tool_turns - _tool_turns} turns remaining. Call no new investigative tools. Synthesise what you have into a final response.")
            _warned_20 = True
        # ── Auto-steer focus check: every N turns, remind of original goal ──
        if _focus_goal and _tool_turns > 0 and _tool_turns % _focus_interval == 0:
            _recent_tools = ", ".join(_focus_tool_history[-5:])
            ctx.add_user(
                f"[FOCUS CHECK] Tool loop {_tool_turns}/{max_tool_turns} turns used.\n"
                f"  Original goal: {_focus_goal[:200]}\n"
                f"  Recent tools: {_recent_tools}\n"
                f"  Question: Are you still on track? If off-topic, redirect back to the goal now."
            )
        _tool_turns += 1
        for tc in _resp.tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            raw_args = func.get("arguments", "{}")
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
            if progress_callback:
                progress_callback("tool", name, args)
            if interactive:
                print(f"\033[90m[TOOL] {name}", end="", flush=True)
                print(f" {str(args)[:80]}", end="", flush=True)
            perm_result = perm.check(name, args)
            if perm_result["decision"] == "deny":
                if interactive:
                    print(f" ⛔ BLOCKED: {perm_result['reason']}\033[0m")
                ctx.add_tool_result(tc.get("id", ""), name, f"[BLOCKED] {perm_result['reason']}")
                continue
            exe_result = execute_tool(name, args)
            # ── Per-tool error policy (replaces global _fail_count) ──
            if "Error" in exe_result[:100] or "Traceback" in exe_result:
                _tool_retries[name] = _tool_retries.get(name, 0) + 1
                max_allowed = get_max_retries(name)
                err_cat = classify_error(exe_result)
                # Permanent errors (blocked, permission, not found) always
                # get 0 retries regardless of the tool's default policy
                if err_cat == "permanent":
                    max_allowed = 0
                if _tool_retries[name] > max_allowed:
                    logger.warning(
                        f"[Loop] {name} failed {_tool_retries[name]}x consecutive "
                        f"(policy: {max_allowed} retries, category: {err_cat}) — dead end"
                    )
                    ctx.add_user(
                        f"[SYSTEM] Tool '{name}' failed {_tool_retries[name]}x consecutive "
                        f"({err_cat}). This approach is not working. Stop trying this path and report."
                    )
                    _tool_retries = {}  # reset to prevent double injection
            else:
                _tool_retries[name] = 0
            # ── Track tool for auto-steer focus history ──
            _focus_tool_history.append(name)
            if len(_focus_tool_history) > 20:
                _focus_tool_history.pop(0)
            # ── Auto-verify after write tools ──
            exe_result = _verify_after_write(name, args, exe_result, data_dir)
            if interactive:
                print(f" \033[32m[OK]\033[0m", flush=True)
            # ── Token Killer: compress tool output before entering LLM context ──
            from .token_killer import compress_tool_output
            exe_result = compress_tool_output(name, exe_result, args)
            ctx.add_tool_result(tc.get("id", ""), name, exe_result)
        # ── Session trimming: prevent unbounded context growth ──
        _trimmed = ctx.trim(max_messages=60)
        if _trimmed > 0:
            logger.debug(f"[Loop] Session trimmed: {_trimmed} old messages removed")
        # ── Context compaction: summarize old turns, don't just drop ──
        _total = ctx.total_chars()
        if _total > 60000:
            logger.info(f"[Loop] Context over threshold ({_total} chars), compacting...")
        _compacted, _notify, _summary = ctx.compact(threshold_chars=60000, keep_recent_turns=12)
        if _compacted > 0:
            logger.info(f"[Loop] Context compacted: {_compacted} old turns summarized ({_total} → {ctx.total_chars()} chars)")
            # Auto-save useful summaries to memory (via curator gate)
            _saved = 0
            try:
                from core.memory_curator import curate, classify, value_score
            except ImportError:
                curate = None
            for _line in _summary.split("\n"):
                _line = _line.strip()
                if not _line or not _line.startswith("[壓縮]"):
                    continue
                # Run through curator gate
                _decision = None
                if curate is not None:
                    try:
                        _decision = curate(
                            content=_line,
                            tags=["compaction", "auto"],
                            source="system",
                            existing_entries=list(reversed(mem._cache))[:100],
                        )
                    except Exception:
                        pass
                # Fallback: keyword scoring if curator unavailable or discards
                _should_save = False
                if _decision and _decision["action"] not in ("discard",):
                    _should_save = True
                elif _decision is None:
                    # Legacy keyword scoring fallback
                    _import_kw = ["config", "bug", "fix", "prefer", "根因", "配置",
                                  "設定", "修復", "錯誤", "修正", "改咗", "改用",
                                  "workaround", "cancel", "skip", "block"]
                    _score = sum(1 for kw in _import_kw if kw in _line.lower())
                    _should_save = _score >= 1
                if _should_save:
                    try:
                        mem.remember(
                            content=f"[壓縮記憶] {_line}",
                            tags=["compaction", "auto"],
                            source="system",
                        )
                        _saved += 1
                    except Exception:
                        pass
            if _saved > 0:
                logger.info(f"[Loop] Auto-saved {_saved} compacted summaries to memory")
        # ── Force synthesis after N turns of read-only tools with no text ──
        _READ_ONLY_TOOLS = {"read_file", "search_files", "web_search", "web_extract", "browser_navigate", "browser_snapshot", "browser_scroll", "grep", "session_search"}
        _has_text = bool(_resp.content and _resp.content.strip())
        if not _has_text and _tool_turns >= 3:
            _all_read = all(
                func.get("name", "") in _READ_ONLY_TOOLS
                for tc in _resp.tool_calls
                for func in [tc.get("function", {})]
            )
            if _all_read:
                logger.info(f"[Loop] Force synthesis: {_tool_turns} turns of read-only tools with no text")
                ctx.add_user(
                    "[SYSTEM] You have done enough reading. Synthesise what you have into an answer for the user now. "
                    "Do NOT call more tools. If you need to explain what you found, do it in text."
                )
        # ── Pre-call budget check: if context already too big, stop before burning more ──
        _ctx_tokens = _get_tracker().total_tokens_in + _get_tracker().total_tokens_out
        if _get_tracker().over_budget() or _ctx_tokens > CostTracker.MAX_SESSION_TOKENS * 0.8:
            logger.warning(f"[Loop] Pre-call budget check: {_ctx_tokens} tokens used, stopping")
            ctx.add_user(f"[SYSTEM] Token budget nearly exhausted ({_human_tokens(_ctx_tokens)}). Synthesise what you have — no more LLM calls.")
            _extra_info["cost_cap_hit"] = True
            break
        # Next LLM call to synthesize results
        fb = call_llm_with_fallback(config, ctx.to_openai_messages(), tools=get_openai_tools(), temperature=model_temperature, max_tokens=OUTPUT_MAX_TOKENS)
        _resp = fb.response
        _was_truncated = getattr(_resp, 'finish_reason', '') == 'length'
        if _was_truncated:
            logger.info(f"[Loop] Output truncated at ~{OUTPUT_MAX_TOKENS} tokens ({_resp.output_tokens} used)")
        # ── Hard post-gen enforcement: some providers (DeepSeek) ignore max_tokens ──
        if _resp.content and len(_resp.content) > OUTPUT_MAX_CHARS:
            logger.warning(f"[Loop] Post-gen enforcement: {len(_resp.content)} chars truncated to {OUTPUT_MAX_CHARS}")
            _resp.content = _resp.content[:OUTPUT_MAX_CHARS - 50] + "\n\n[...truncated to token budget...]"
            _was_truncated = True
        n_cost = calculate_cost(model, _resp.input_tokens, _resp.output_tokens)
        session_cost += n_cost
        record_cost(f"{model.provider}/{model.id}", _resp.input_tokens, _resp.output_tokens, n_cost)
        ctx.add_assistant(_resp.content, _resp.tool_calls,
                          getattr(_resp, 'reasoning_content', None))
        # ── Cost budget kill switch ──
        if _get_tracker().over_budget():
            logger.warning(f"[Loop] Token budget exceeded ({_get_tracker().total_tokens_in + _get_tracker().total_tokens_out} > {CostTracker.MAX_SESSION_TOKENS}), aborting")
            ctx.add_user("[SYSTEM] Token budget exceeded. Stopping. Synthesise what you have.")
            _extra_info["cost_cap_hit"] = True
            break

    # Return final synthesized response
    output = ""
    if tone_change:
        output += format_tone_confirmation(old_tone, new_tone) + "\n\n"
    output += (_resp.content or "")

    # ── Mandatory synthesis guard: if output is empty, meaningless, or intention-only, force text-only retry ──
    _is_intention_only = False
    _stripped_out = output.strip()
    if _stripped_out and len(_stripped_out) < 500:
        _intention_patterns = [
            r"^(Let me\s|I will\s|I need to\s|I have to\s|I'm going to\s|I am going to\s)",
            r"^(Now|Next),?\s*(let|I|we)\s",
            r"^I have (enough|the|all|sufficient).*?(to\s|that)",
            r"(compile|synthesize|write|prepare|gather|collect).*(review|answer|response|report)",
            r"^Here's what I\s",
            r"(let me search|let me look|let me check|let me try)",
        ]
        _match_count = sum(
            1 for p in _intention_patterns
            if re.search(p, _stripped_out, re.IGNORECASE)
        )
        # 2+ intention signals → output is planning, not content
        if _match_count >= 2:
            _is_intention_only = True
            logger.warning(f"[Loop] Intention-only output ({len(output.strip())} chars, {_match_count} signals) — forcing synthesis retry")
    if not output.strip() or len(output.strip()) < 30 or _is_intention_only:
        logger.warning(f"[Loop] Empty/trivial/intention output ({len(output.strip())} chars) — forcing synthesis retry")
        ctx.add_user(
            "[SYSTEM] Your previous response was empty. You must now answer the user's question directly. "
            "You have all the data you need. Write a clear answer in Cantonese. No tools available."
        )
        fb2 = call_llm_with_fallback(
            config, ctx.to_openai_messages(),
            tools=None,  # NO tools — force text-only response
            temperature=model_temperature,
            max_tokens=OUTPUT_MAX_TOKENS,
        )
        _resp2 = fb2.response
        if _resp2.content and _resp2.content.strip():
            output = _resp2.content.strip()
            _was_truncated = getattr(_resp2, 'finish_reason', '') == 'length'
            logger.info(f"[Loop] Synthesis retry produced {len(output)} chars")
        else:
            # Final fallback: explain the situation
            output = "我做咗啲檢查，但未有足夠資訊完整答到你。你可以再具體啲講你想要睇咩嗎？"

    # Append cost summary BEFORE length trim so it's counted
    output += f"\n{format_cost_summary()}"
    output = output.strip()
    # ── Output token budget: post-generation length enforcement ──
    if _was_truncated:
        output += "\n\n*(Response truncated to ~800 words for readability)*"
    if len(output) > OUTPUT_MAX_CHARS:
        logger.warning(f"[Loop] Output too long ({len(output)} chars > {OUTPUT_MAX_CHARS}), force-trimming")
        output = output[:OUTPUT_MAX_CHARS] + "\n\n*(Response trimmed to fit length limit)*"

    try:
        mem.remember(f"User: {prompt[:150]} → BAW: {(_resp.content or '')[:150]}")
    except Exception as _e:
        logger.warning(f"Memory remember failed: {_e}")

    output = _verify_post_turn_claims(output, data_dir)
    needs_continuation = _extra_info.get("tool_cap_hit", False) or _extra_info.get("cost_cap_hit", False)
    return output, {
        "cost": round(session_cost, 4),
        "model": f"{model.provider}/{model.id}",
        "iterations": 1,
        "steps": 0,
        "adversarial": court_result["agreement_level"] if court_result else None,
        "adversarial_raw": court_result,
        "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        "goal_achieved": not needs_continuation,
        "needs_continuation": needs_continuation,
        **_extra_info,
    }

    