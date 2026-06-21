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
# Quick mode: rapid research, short answers → 4096 default
# Hybrid mode: moderate reasoning → 8192
# Tight mode: deep analysis → 16384
# Auto: let LLM decide (5120 — comfortable middle ground)
MODE_MAX_TOKENS = {
    "quick": 4096,
    "hybrid": 8192,
    "tight": 16384,
    "auto": 5120,
    "focus": 16384,
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
MAX_TOOL_TURNS = 25      # base — overridden dynamically by task complexity below

# ── Tool cap scaling by task complexity ──
_TOOL_TURNS_BY_COMPLEXITY = {
    "simple":   50,
    "moderate": 75,
    "complex":  100,
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
                f"📊 {n} {label}{model_tag} — total: {_human_tokens(total)} tokens"
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


def build_system_prompt(config: dict, data_dir: Optional[Path] = None,
                        fresh_start: bool = False) -> str:
    """Build system prompt from SOUL.md + dynamic context.

    Unified prompt (no quick/full mode split): the LLM self-judges
    task complexity and matches response depth accordingly.
    
    Structure (cache-aware): [STATIC SOUL CORE] + [DYNAMIC CONFIG]
    Static prefix stays identical across turns → DeepSeek prefix cache hit.
    """
    # Bright-line META-RULE: no fake completion. Injected FIRST in the
    # system prompt (above everything else) so it's the first thing the
    # LLM sees, in BOTH quick and full mode. This rule is more
    # important than any task-specific guidance.
    #
    # 2026-06-12 incident: Telegram bot LLM received
    # `/baw self-test --no-fetch`, made a 5-checkbox plan, ran ONE bash
    # call that failed (path error), then marked all 5 checkboxes done
    # and reported "5/5 (100%)" — pure fabrication. This rule exists
    # because the LLM otherwise defaults to "plan completion" optimism.
    evidence_rule = (
        "\n\n## [WARN]  META-RULE — No Fabrication\n"
        "### Must do:\n"
        "  - Run commands EXACTLY as typed via `bash` tool — report real exit/output\n"
        "  - Use `write_file`/`read_file` DIRECTLY — NOT `execute_code` for file ops\n"
        "  - If a tool call failed: say 'failed: <actual error>'. Do NOT retry 3x.\n"
        "### Must NOT:\n"
        "  - Claim '5/5 done' when steps failed. 3/5 done, 2 failed = honest.\n"
        "  - Say 'I will follow up' / 'Let me check' + no tool call = planning not doing.\n"
        "  - Say 'I cannot access files' — you have read/write/terminal tools.\n"
        "  - Over-generalize from one search result ('HTML is more reliable' ≠ 'Markdown not supported').\n"
        "  - Orchestrator 'all completed' when sub-agents returned errors = fabrication.\n"
        "  - Treat checkboxes as completion. Only tool output = done.\n"
        "This rule > any task guidance. First injected, always active.\n"
    )

    # ── EXECUTION PROTOCOL (injected before SOUL, always active) ──
    # Note: ANTI-FABRICATION and META-RULE (evidence_rule above) are the FIRST
    # thing the LLM sees. EXECUTION PROTOCOL reinforces the same message more
    # compactly. These TWO sections together replace what was previously 3+
    # overlapping blocks (evidence_rule + execution_protocol + 2nd evidence_rule
    # in _build_todo_block + tool cap warnings in prompt body).
    execution_protocol = (
        "\n\n## [CRITICAL] EXECUTION PROTOCOL — Autonomous Agent Behavior\n"
        "You are a self-advancing agent. Your default mode is CONTINUOUS EXECUTION.\n"
        "\n"
        "### Core rules\n"
        "- Every response MUST either (a) contain tool_calls making progress, or (b) deliver a final result.\n"
        "- NEVER end with a promise ('I will', 'Let me', '下一步'). If more remains, CALL THE TOOL NOW.\n"
        "- If a tool fails: READ error, FIX it, RETRY once. If same error twice: STOP that path and report.\n"
        "- KEEP CALLING TOOLS until the ENTIRE task is complete. Text-only = finished.\n"
        "- Do NOT write numbered plans, checkboxes, or step lists in text. Tool_calls ARE the visible plan.\n"
        "- Report what HAPPENED (result + data), not what you WILL do.\n"
        "- Use `todo` tool internally to track progress.\n"
    )


    if fresh_start:
        return (
            "You are an AI assistant. No prior context, no memories, no soul profile.\n"
            "Respond to this prompt with your raw, unfiltered judgment.\n"
            "Be direct and honest. Do not assume any prior conversation.\n"
        )

    # ── Shared output structure (used by both quick and full mode) ──
    output_structure = (
        "\n\n## OUTPUT STRUCTURE — Three Layers\n"
        "Your response follows three layers.\n"
        "\n"
        "### Layer 1 — 結果 (1-3行, 必填)\n"
        "開首第一句就係最重要嘅結果:\n"
        "  <一句講晒做咗乜>\n"
        "  <關鍵數據或結論>\n"
        "\n"
        "### Layer 2 — 細節 (optional)\n"
        "精簡 bullet points. 如果結果已經夠清楚就 skip 呢層.\n"
        "\n"
        "### Layer 3 — 原始資料 (唔顯示, 除非用家問)\n"
        "DO NOT dump raw tool output / config / traceback.\n"
        "如果用戶需要原始資料, 話「詳細可以睇 <path>」\n"
        "\n"
        "### 規則\n"
        "- 唔准加「總結」/「Summary」/「以下係」結尾 — 你成段回覆就係結果, 唔好重覆自己\n"
        "- CONCISENESS: 5 句以內完成。第一句直接畀結論。第二到第五句補關鍵細節就收。\n"
        "- DO NOT add a \\\"總結\\\" / \\\"summary\\\" / \\\"以下係\\\" section at the end. Your ENTIRE response IS the summary.\n"
        "- 唔用 emoji。結果用純文字直接講做咗乜、成功定失敗。\n"
    )

    # ── DELEGATION vs INLINE (shared by both modes) ──
    delegation_block = (
        "\n\n## DELEGATION vs INLINE\n"
        "當你使用 <code>delegate_task</code> 時，回傳帶有「╔═══ 巳分工 ═══╗」box。\n"
        "彙報時必須:<br>"
        "- <b>🔄 已分工 — <任務名></b> header<br>"
        "- 摘要結果，唔好 dump raw box<br>"
        "- 註明 sub-agent model<br>"
        "- Inline 結果直接出，無 header<br>"
    )

    base_path = data_dir or Path.home() / ".baw"
    soul_path = base_path / "SOUL.md"

    if soul_path.exists():
        soul_text = soul_path.read_text(encoding="utf-8")
        system_prompt = evidence_rule + execution_protocol + soul_text
        system_prompt += (
            "\n\n## COMPLEXITY SELF-JUDGMENT\n"
            "Self-judge task complexity. Match your processing depth to the task:\n"
            "- Simple Q&A (greeting, quick question, yes/no) → reply directly. No tools needed.\n"
            "- Quick fact-check → single tool call, direct answer.\n"
            "- Multi-step task → use tools as needed, but be proportionate.\n"
            "You ALREADY know the difference between 'hi' and 'analyze this codebase'. Trust that judgment.\n"
            "Do NOT over-process simple requests. Do NOT under-process complex ones.\n"
        )
        # ── LANGUAGE HARD GATE (always active, before output structure) ──
        lang_gate = (
            "\\n\\n## HARD GATE — Your output IS your answer\\n"
            "Every line you write reaches the user. There is no hidden thinking space.\\n"
            "NEVER narrate your tool calls. NEVER say 'Let me check', 'I will now', "
            "'First, let me', 'Based on the', 'After checking', 'I have reviewed'.\\n"
            "NEVER output execution traces, tool call lists, file paths, or progress summaries.\\n"
            "Your entire response must be a DIRECT answer to the user's question — "
            "not a description of what you did.\\n"
            "\\n"
            "## LANGUAGE HARD GATE — Cantonese/Trad Chinese ONLY\\n"
            "All output MUST be in Cantonese (廣東話) or Traditional Chinese (繁體中文).\\n"
            "English thinking is internal noise — NEVER output it.\\n"
            "Rules:\\n"
            "- Your reasoning chain can be in English, but the final user-facing output MUST be Cantonese/TC.\\n"
            "- No English summaries, no English labels, no English explanations.\\n"
            "- If the user writes in English, still respond in Cantonese/TC.\\n"
            "- Exception: code names, file paths, technical terms (e.g. 'config.yaml', 'API', 'Docker') can stay in their original form.\\n"
            "This is a HARD GATE — not a suggestion. Violations break the user interface contract.\\n"
        )
        system_prompt += lang_gate
        system_prompt += output_structure
        system_prompt += delegation_block
    else:
        system_prompt = (
            "You are BAW (Black And White), your agent platform.\n"
            "Match user's language. Speak what the user speaks.\n"
            "Be concise, lead with results.\n"
            "Never ask the user what to do — figure it out yourself.\n"
            "## HARD GATE: No thinking leakage\n"
            "Your entire output is visible to the user. No hidden thinking space.\n"
            "NEVER start with: 'Let me...' 'I will...' 'I need to...' 'I have...'\n"
            "  'First...' 'Now let...' 'Based on the...' 'After checking...'\n"
            "START DIRECTLY with the answer. First word = first word user sees.\n"
            "Before claiming a config/state, call the config tool to verify.\n"
            "\n"
            "## Self-configuration (when no SOUL.md found)\n"
            "- Your config lives at ~/.baw/config.yaml\n"
            "- Your API keys live at ~/.baw/.env\n"
            "- To modify config, use the `config` tool:\n"
            "  `config(action=set, path='providers.x.base_url', value='https://...')`\n"
            "  `config(action=get, path='model.default')` to read\n"
            "  `config(action=validate)` to check syntax\n"
            "  NEVER use `write_file` or `bash` to edit config.yaml or .env files directly — use `config(action=set)` for config.yaml and `config(action=set_key)` for .env —\n"
            "  the `config` tool handles backup, validation, and auto-rollback.\n"
            "  Config command rule: when user sends params (method=X model=Y),\n"
            "  use config tool only. MAX 3 steps: get→set→verify. NO sub-agents.\n"
            "  NEVER say 'I will follow up' — DO IT NOW, report result.\n"
            "- After editing config, call /reload or restart to apply\n"
            "\n"
            "### [WARN] STT setup (auto-detect protocol)\n"
            "- Set `stt.method: auto-asr` in config.yaml, provide base_url + api_key_env\n"
            "- System auto-probes: OpenAI /v1/audio/transcriptions first, SSE /v1/audio/asr/sse second\n"
            "- Works with any provider that supports either protocol\n"
            "- Never set stt.method = model — that method is not implemented"
        )
        system_prompt = evidence_rule + execution_protocol + system_prompt

    # ── SAFETY PROTOCOL (Priority 0 — always enforced) ──
    safety_protocol = (
        "\n\n## [CRITICAL] SAFETY PROTOCOL (Priority 0)\n"
        "1. Audit before execute: Any downloaded code/repo MUST be scanned.\n"
        "   Use `code_scan(path=<dir>)` BEFORE running any scripts or installs.\n"
        "2. When audit is required: cloning repos, pip/npm install with `-g`,\n"
        "   executing scripts from /tmp, downloading from GitHub/GitLab.\n"
        "3. If audit finds warnings: Report them to the user. Do NOT proceed silently.\n"
        "4. Safety beats speed: NEVER skip audit because 'the user wants it fast'.\n"
        "   A blocked command later undone is better than an exploited system.\n"
        "5. Self-contained BAW tools (mmx, tts, install) are pre-audited — no scan needed.\n"
        "   Only scan externally downloaded or user-provided code.\n"
        "6. code_scan is a BAW tool — use it: `code_scan(path='/tmp/repo', scan_type='quick')`\n"
        "   It scans for eval/exec, shell injection, credential leaks, and unsafe imports."
    )
    system_prompt += safety_protocol

    # ── SELF-CORRECTION PROTOCOL (Fail fast — don't burn tokens on dead ends) ──
    self_correction = (
        "\n\n## [INFO] SELF-CORRECTION PROTOCOL\n"
        "Fail fast. Don't burn tokens on dead ends.\n\n"
        "When a tool fails:\n"
        "1. READ the error. Understand what went wrong.\n"
        "2. Fix and retry ONCE with a different approach.\n"
        "3. If the SAME tool fails TWICE: STOP that path.\n"
        "   Do NOT try a 3rd approach. The system will inject a "
        "[SYSTEM] message telling you to stop.\n"
        "4. Report the failure to the user WITH diagnosis.\n\n"
        "Correct behaviour:\n"
        "- Fix typo → retry → works → continue ✅\n"
        "- Fix typo → retry → same error → report dead end ✅\n\n"
        "What NOT to do:\n"
        "- [FAIL] Try 3, 4, 5 different approaches, burning tokens on each\n"
        "- [FAIL] Ignore the failure and fabricate a success result\n"
        "- [FAIL] Say 'config.yaml needs manual fix' — YOU have the tools to fix it\n"
        "- [FAIL] Keep retrying the same broken command\n"
    )
    system_prompt += self_correction

    # ── Static core ends here — everything below is dynamic context ──
    # DeepSeek prefix cache: first ~2,600 tokens (evidence_rule + execution_protocol
    # + SOUL.md + output_structure + delegation_block + safety_protocol +
    # self_correction) are STATIC across turns → cacheable.
    # Dynamic context below (models, config, todo, etc.) changes per turn.
    # Cache is automatic at provider level — no special headers needed.

    orch_path = base_path / "ORCHESTRATOR.md"
    if orch_path.exists():
        orch_text = orch_path.read_text(encoding="utf-8")
        system_prompt += f"\n\n{orch_text}"

    # Dynamic context (per-turn: models, tools, config may change)
        tone = config.get("tone", {}).get("default", "casual")
        fact_mode = config.get("fact_check", {}).get("mode", "normal")
        tools_list = "bash, read_file, write_file, web_search, patch, search_files, memory, config, mmx, tts, image_generate, git, docker, todo, install, system, background, delegate_task, knowledge_graph, execute_code"

        default_model = config.get("model", {}).get("default", "unknown")
        config_path = data_dir / "config.yaml" if data_dir else Path.home() / ".baw" / "config.yaml"
        env_path = data_dir / ".env" if data_dir else Path.home() / ".baw" / ".env"
        _code_path = os.environ.get("BAW_HOME") or str(Path(__file__).resolve().parent.parent)

        system_prompt += (
            f"\n\n## System config\n"
            f"- Config file: {config_path}\n"
            f"- Env file: {env_path}\n"
            f"- Code path: {_code_path}\n"
            f"- WARNING: config.yaml stores ONLY: API keys, model definitions, provider endpoints, cost config.\n"
            f"  It does NOT store behavioral rules, safety policies, or execution instructions.\n"
            f"  Those live in the system prompt (you're reading it now).\n"
            f"  DO NOT edit config.yaml to add safety rules, output rules, or enforcement policies.\n"
            f"- Default model: {default_model}\n"
            f"- Available models summary (use self_capabilities for full list):\n"
            f"  {_summarize_providers(config)}\n"
            f"  NEVER fabricate model names. Only use models that exist in config.\n"
            f"\n## Tool self-configuration (CRITICAL)\n"
            f"- When told to use a new tool: 'which <tool>' or 'find / -name <tool>' to locate it.\n"
            f"- [CRITICAL] EXCEPTION: BAW-registered tools (mmx, install) are SELF-CONTAINED.\n"
            f"  The `mmx` tool auto-installs mmx-cli on first use — do NOT run `which mmx`.\n"
            f"  Just call `mmx(command=...)` directly; it handles installation internally.\n"
            f"- To verify mmx is working: call `mmx(command=quota)` not bash('which mmx').\n"
            f"- For ANY BAW-registered tool: the tool IS the verification. Call it, don't which it.\n"
            f"- Test it via 'bash' first. If useful, create a permanent wrapper:\n"
            f"  1. Use `baw tools create <name>` — the scaffolder writes the file,\n"
            f"     registers it in `tools/__init__.py`, and runs a smoke test.\n"
            f"  2. Implement the handler body in `~/baw/tools/<name>.py` (replace the\n"
            f"     `TODO` block). The TOOL_DEF schema, registration, and tests are\n"
            f"     already wired for you.\n"
            f"  3. Run `baw tools verify <name>` to confirm it loads. Never claim a\n"
            f"     tool is 'done' until verify passes. A failed verify means NOT DONE.\n"
            f"  4. `baw tools doctor` cross-checks registrations against files on disk\n"
            f"     — run after any tools/__init__.py edit.\n"
            f"- NEVER hand-edit `tools/__init__.py` and assume it works. The scaffolder\n"
            f"  exists precisely because hand-editing breaks things (wrong paths,\n"
            f"  import-order bugs, missing TOOL_DEF, no smoke test).\n"
            f"- [CRITICAL] ANTI-FABRICATION CONTRACT:\n"
            f"  1. If you say 'I built X' and X is not importable + runnable, you did NOT build X.\n"
            f"  2. If you claim config changes, read back via config(action=get) to PROVE it.\n"
            f"  3. If you claim file edits, read_file to PROVE the change exists.\n"
            f"  4. NEVER fabricate a completion summary. If a tool call failed, REPORT IT.\n"
            f"  5. The user verifies claims against git diff. Lie = worse than failure.\n"
            f"- Also discover tools proactively: 'ls /usr/bin /usr/local/bin ~/.local/bin' for new capabilities.\n"
            f"- NEVER wait for the user to pre-configure tools. You own your toolchain.\n"
            f"\n## Progressive Disclosure — Skills\n"
            f"Skills are not dumped into this prompt — only names + descriptions are listed.\n"
            f"To load full skill content, use the `get_skill` tool:\n"
            f"  1. `get_skill(action=list)` — see available skill names + descriptions\n"
            f"  2. `get_skill(action=get, skill_name=<name>)` — load full content + steps\n"
            f"\nKey skill for GitHub links:\n"
            f"- `github-research` — given a GitHub URL, systematically README -> find install method -> execute -> verify\n"
            f"  Use this ANYTIME someone gives you a GitHub link!\n"
            f"\nHow to use a skill:\n"
            f"  1. `get_skill(action=get, skill_name=github-research)` to load full workflow\n"
            f"  2. Follow the skill steps using your tools\n"
            f"  3. Use `remember(action=remember, fact=..., category=install)` to save learnings\n"
            f"\n## Knowledge Graph — Relational Memory\n"
            f"`knowledge_graph` stores facts as triples: subject -[relation]-> object.\n"
            f"Use when you want to link facts together, not just store them flat.\n"
            f"- `knowledge_graph(action=add, subject=X, relation=Y, object=Z)` — save a fact\n"
            f"- `knowledge_graph(action=query_entity, entity=X)` — find all facts about X\n"
            f"- `knowledge_graph(action=search, query=...)` — full-text search\n"
            f"- `knowledge_graph(action=extract)` — auto-import from flat memory store\n"
            f"\n## Config Editor — Safe config.yaml Modification\n"
            f"NEVER use `write_file` or `bash` to edit config.yaml. Use the `config` tool:\n"
            f"- `config(action=get, path='model.default')` — read a value\n"
            f"- `config(action=set, path='providers.x.base_url', value='https://...')` — set a value\n"
            f"  Auto-creates backup, writes, validates YAML, rolls back on error.\n"
            f"- `config(action=delete, path='old.key')` — remove a key\n"
            f"- `config(action=validate)` — check config syntax\n"
            f"- `config(action=backups)` — list timestamped backups\n"
            f"- `config(action=restore, backup_name='config_20260615.yaml')` — restore from backup\n"
            f"- Path format: dotted keys like 'providers.minimax.base_url' or 'capabilities.tts.model'\n"
            f"- config.yaml is READ-ONLY (chmod 444) — only the config tool can modify it.\n"
            f"  write_file and bash will get PermissionError on config.yaml.\n"
            f"- NEVER try to create your own config-wrapper script — the config tool already handles\n"
            f"  atomic writes, automatic backups, and YAML validation. Write directly: call config()\n"
            f"\n## Background Processes\n"
            f"For long-running commands (servers, watchers):\n"
            f"- `background(action=start, command='python server.py')` — start in background\n"
            f"- `background(action=output, bg_id=bg1)` — check output\n"
            f"- `background(action=stop, bg_id=bg1)` — terminate\n"
            f"- `background(action=list)` — see all running processes\n"
            f"\n## MCP — Model Context Protocol\n"
            f"`mcp` connects to third-party MCP servers for extended tools.\n"
            f"- `mcp(action=connect)` — connect all configured servers (from ~/.baw/mcp.json)\n"
            f"- `mcp(action=list_tools)` — see available MCP tools\n"
            f"- `mcp(action=call, server=NAME, tool=TOOL, args='{{'key': 'val'}}')` — call an MCP tool\n"
            f"- `mcp(action=list_servers)` — see connected servers\n"
            f"\n## MiniMax CLI (mmx) — Image, Video, Speech, Music, Vision\n"
            f"`mmx` wraps the official MiniMax CLI for generating media.\n"
            f"- `mmx(command=image, prompt='A cat', n=2, aspect_ratio=16:9)` — generate images\n"
            f"- `mmx(command=speech, text='Hello', voice=Cantonese_GentleLady)` — TTS\n"
            f"- `mmx(command=video, prompt='Ocean waves')` — generate video\n"
            f"- `mmx(command=music, prompt='Upbeat pop', lyrics=..., instrumental=True)` — music\n"
            f"- `mmx(command=vision, image_path=photo.jpg)` — analyze image\n"
            f"- `mmx(command=search, query=...)` — web search via MiniMax\n"
            f"- `mmx(command=voices)` — list all TTS voices\n"
            f"- `mmx(command=quota)` — check token quota\n"
            f"\n## Remember tool — lightweight fact storage\n"
            f"Use `remember` to save short facts between sessions:\n"
            f"- After successful install: `remember(action=remember, fact=mmx-cli v1.0.16 installed via npm, category=install)`\n"
            f"- After discovering something: `remember(action=remember, fact=..., category=discovery)`\n"
            f"- To retrieve: `remember(action=recall, category=install)`\n"
            f"\n## Recalling facts — search memory before saying I don\'t know\n"
            f"When a user asks about things you should know (their pets, car, preferences,\n"
            f"past decisions, config, trades, ongoing work):\n"
            f"  1. ALWAYS call `memory(action=search, content=<your question>)` first\n"
            f"  2. Also try `remember(action=recall)` for recent short facts\n"
            f"  3. Try `knowledge_graph(action=query_entity, entity=<topic>)` for relations\n"
            f"  4. ONLY say \"I don\'t remember\" after all 3 searches return nothing\n"
            f"Do NOT trust your context alone — memory may have what context lost.\n"
            f"\n## Error recovery for installations\n"
            f"When installing a package/CLI tool and it FAILS:\n"
            f"  1. Report the REAL error verbatim — never fabricate success\n"
            f"  2. NEVER say 已執行安裝命令 without actually calling bash/install tool\n"
            f"  3. NEVER say All steps completed successfully when any step actually failed\n"
            f"  4. Try alternatives: different package name, check README again, different install method\n"
            f"  5. Record failure: `remember(action=remember, fact=failed to install X: <actual error>, category=install)`\n"
            f"\n## Dynamic context\n"
            f"- Current tone: {tone}\n"
            f"- Fact check mode: {fact_mode}\n"
            f"- Available tools: {tools_list}\n"
            f"- Cost transparency: per-call cost shown after each response\\n"
                        f"\\n## Codebase documentation (for self-modification)\\n"
                        f"- INDEX.md contains the full module map, dependency graph, and interface contracts.\\n"
                        f"- Before modifying your own code, read INDEX.md to understand the architecture.\\n"
                        f"- Use `codebase_doc(action=scan)` to refresh, `codebase_doc(action=report)` to view.\\n"
            f"\n## ⏱ Tool Turns Budget\n"
            f"You have a maximum of 25 tool calls per turn before the system forces you to stop.\n"
            f"- After ~15 calls, start asking: do I have enough data to answer?\n"
            f"- If yes: synthesize results and STOP calling tools.\n"
            f"- If no: prioritise remaining calls — skip nice-to-haves, focus on must-haves.\n"
            f"- At 20 calls: you MUST start wrapping up.\n"
            f"- If you hit the cap (25), the system STARTS A NEW TURN without tools —\n"
            f"  you lose the ability to do more work. Self-terminate proactively.\n"
            f"\n"
            f"- [CRITICAL] AUTO-CONTINUATION: Keep producing tool_calls until the task is fully done.\n"
            f"  If you need more steps, call the next tool IMMEDIATELY — do NOT write text about what you'll do next.\n"
            f"  YOUR RESPONSE MUST CONTAIN TOOL_CALLS if more work remains. Text-only = you are finished.\n"
            f"  BAW will NOT prompt you to continue — if you stop calling tools, the task stops.\n"
            f"  Example of WRONG: 'mmx installed, next I will test it' (text, stops tool loop)\n"
            f"  Example of RIGHT: call install() then immediately call mmx() as next tool call\n"
            f"- [CRITICAL] NEVER end your response with a question or 'what next?'. Just call the tool.\n"
            f"- [CRITICAL] NEVER say 'I will' or 'I will now' — those are future tense. Call the tool NOW.\n"
            f"## [TARGET] Output Presentation Rule\n"
            f"Tool results MUST be human-friendly. NEVER dump raw JSON:\n"
            f"- `mmx(quota)` -> raw JSON  -> `[STATS] 剩餘 11,043,549 秒 (128天)`\n"
            f"- `mmx(voices)` -> raw array -> `[VOICE]️ 334 個語音可用`\n"
            f"- `mmx(image)` -> raw JSON  -> MEDIA:path + 一句描述\n"
            f"Extract signal, skip noise.\n"
            f"\n## Output style — NEVER USE EMOJI\n"
            f"CRITICAL: Your output goes to a messaging app. NEVER use emoji — especially:\n"
            f"  DO NOT use checkmark, cross mark, play button, arrow, warning, clipboard, or any other emoji.\n"
            f"- Report results in plain text. Just say what happened — no symbols.\n"
            f"- Correct: 'Done. 3 files processed.'\n"
            f"- Wrong (emoji): 'OK Done' or 'Failed' — NO symbols allowed.\n"
            f"- If it worked, say so in words. If it failed, say so in words.\n"
            f"- No tick marks, no checkboxes, no emoji. Clean, direct statements.\n"
            f"- NEVER end your response with a question. Execute directly.\n"
            f"\n## Sending files to user\n"
            f"- To send audio (mp3, wav), images (png, jpg), or documents to the user, include MEDIA:/absolute/path in your output:\n"
            f"  MEDIA:/tmp/baw_voice_test/female-tone-1.mp3\n"
            f"- The MEDIA: tag is stripped from text and the file is sent as a native Telegram attachment.\n"
            f"- Use this for TTS audio, generated images, screenshots, or any file the user asked to receive.\n"
            f"- Always include MEDIA: tags AFTER your text message, one per line.\n"
            f"- 🚫 HARD GATE: NEVER say 'I can't send files', '呢個 chat interface 唔支援直接 attach 檔案', 'this chat interface does not support attaching files', or any similar text. You CAN send files using MEDIA: tags. If you generated or created a file, include MEDIA:/path/to/file in your output and BAW will deliver it. Do NOT make up instructions about using `cat` or `open` to view files — that is always wrong; MEDIA: delivery is automatic.\n"
            f"\n## REGION GUARD\n"
            f"Respect the user's configured endpoints. Users may be using international versions\n"
            f"of providers with different base_urls. NEVER suggest 'fixing' their endpoint to a\n"
            f"Chinese/default one. The user's provider config is their intentional choice.\n"
            f"- Do NOT auto-heal or recommend changing to Chinese endpoints.\n"
            f"- If a provider has a non-standard base_url, assume it's intentional.\n"
            f"- config(action=set) changes to endpoints are user decisions, not drift.\n"
            f"\n## [CRITICAL] HARD GATE — NO UNAUTHORIZED CONFIG CHANGES\n"
            f"You MUST NEVER change the user's provider configs, API endpoints, or capabilities\n"
            f"unless the user explicitly tells you to.\n"
            f"\n"
            f"**WHEN USER COMMANDS config changes:**\n"
            f"- User: 'add model X', 'set up provider Y', 'add task rule Z' → use `config_set()` or `config_set_key()` to execute.\n"
            f"- If `config_set()` returns HARD GATE error → use `request_config_change()` to ask user for permission.\n"
            f"- `config_set_key()` can set API keys without permission (always works).\n"
            f"\n"
            f"**WHEN NOT USER-COMMANDED:**\n"
            f"- Do NOT auto-heal or change providers/endpoints/capabilities on your own initiative.\n"
            f"- Your role: 1. DETECT issues 2. REPORT clearly 3. WAIT for user to decide.\n"
            f"- Reporting a problem does NOT give you permission to fix it automatically.\n"
            f"\n"
            f"**ANTI-HALLUCINATION:**\n"
            f"- Before claiming config state exists (e.g. 'fusion is already configured'), you MUST verify by reading config.yaml or calling config(action=get).\n"
            f"- If you can't do what the user asked, say so clearly — don't make up a fake success.\n"
            f"\n## TTS / Voice generation\n"
            f"- Use the `tts` tool for generating Cantonese text-to-speech audio.\n"
            f"- LEAVE voice EMPTY for auto-detect — defaults to Cantonese_GentleLady (MiniMax, true Cantonese).\n"
            f"- Verified Cantonese voices (MiniMax): Cantonese_GentleLady, Cantonese_CuteGirl, Cantonese_KindWoman\n"
            f"- Verified Cantonese voices (Edge TTS, free fallback): zh-HK-HiuGaaiNeural, zh-HK-HiuMaanNeural\n"
            f"- Stepfun voices (lively-girl, gentle-woman, cute-girl, female-shaonv, female-tone-1, etc.) are MANDARIN — NOT Cantonese.\n"
            f"- To generate: tts(text=\"你好...\") — do NOT set voice unless user explicitly requests a specific one.\n"
            f"- To list all voices: call the `tts_list_voices()` tool (no arguments needed).\n"
            f"- ALWAYS include MEDIA: tag after generating audio to send it to the user.\n"
            f"- For voice selection tasks: generate one sample per voice, send each via MEDIA.\n"
            f"\n## Provider capability discovery\n"
            f"- When the user asks you to configure STT/TTS/vision for a specific provider (e.g. Stepfun TTS),\n"
            f"  do NOT guess capabilities — research to find exactly which models support the capability.\n"
            f"- Research protocol:\n"
            f"  1. First query the provider\'s API /v1/models endpoint to list all available models\n"
            f"  2. Model names often encode capability (e.g. 'asr' → STT, 'tts' → TTS, 'vision' → vision)\n"
            f"  3. If model names are ambiguous, web_search the provider\'s official docs\n"
            f"     for model capabilities (e.g. 'stepfun stepaudio ASR' or 'stepfun TTS models')\n"
            f"  4. Based on findings, update config.yaml: add the correct model under `capabilities.<func>.model`\n"
            f"- Auto-configure: After researching, update config.yaml IMMEDIATELY — do NOT stop to report findings.\n"
            f"  The goal is to configure the capability, not just to report what was found.\n"
            f"- Config file path: {config_path}\n"
            f"- Use the `config` tool to write config changes:\n"
            f"  `config(action=set, path='capabilities.stt.model', value='stepfun-stepaudio-asr')`\n"
            f"  NEVER use `bash` or `write_file` — config tool handles backup + validation.\n"
            f"- After updating config, test the new capability to confirm it works.\n"
            f"- Respond with: 'Configured: <provider> <capability> using <model> — tested OK' or 'Failed: <reason>'\n"
            f"- NEVER say 'let me know if you want me to configure it'. Just DO it."
            f"\n## Capability config self-diagnosis (DRIFT DETECTION)\n"
            f"- When a capability fails (e.g. STT returns error, silent, or wrong output),\n"
            f"  do NOT just report 'X is not working'. Self-diagnose FIRST:\n"
            f"  1. config(action=get, section=capabilities, key=<function>) to read current settings.\n"
            f"  2. Check for CONTRADICTIONS (these are BUGS, not user preferences):\n"
            f"     - method: 'faster-whisper' (local) + base_url: 'api.x.ai' (remote) → MUTUALLY EXCLUSIVE\n"
            f"     - method: 'auto-asr' (remote) + no base_url → BROKEN, needs endpoint\n"
            f"     - api_key_env set but env var doesn't exist in .env → WILL FAIL AT RUNTIME\n"
            f"  3. If contradiction found, FIX IT immediately:\n"
            f"     - Local method? Strip base_url + api_key_env.\n"
            f"     - Missing env var? Either set it OR fall back to faster-whisper (local, free, always works).\n"
            f"     - Remote method + no base_url? Auto-discover from provider list.\n"
            f"  4. After fixing, RE-TEST the capability. Test = send a real request, don't just read config.\n"
            f"- RULE: Never claim 'X is not configured' until you've checked for config drift.\n"
            f"  Drift = the config HAS settings but they contradict each other → it IS configured, just BROKEN.\n"
            f"  REPORT the drift to the user. Let them decide whether to fix it.\n"
            f"  Do NOT auto-fix config without the user saying 'fix it' or 'change X to Y'.\n"
            f"\n## Explicit config commands — EXECUTE (NOT PLAN, NOT DELEGATE)\n"
            f"HARD RULES:\n"
            f"- MAX 3 steps: get → set → verify. NO planning, NO sub-agents, NO orchestrator.\n"
            f"- Use ONLY the `config` tool. NEVER use execute_code, bash, write_file, or delegate_task.\n"
            f"- NEVER say 'I will follow up' or 'I will check and get back to you.' DO IT NOW.\n"
            f"- After setting, MUST read back with config(action=get) to confirm.\n"
            f"- MUST test with a real request before reporting success.\n"
            f"- If api_key_env specified → check .env. Missing key? Say which one. DON'T pretend.\n"
            f"- Why orchestrator fails: it spawns sub-agents that use wrong tools, then reports 'all steps completed' without verifying. This is FABRICATION.\n"
            f"- For config commands, YOU are the worker. Use the config tool directly.\n"
        )

    # ── Todo / thought / follow-up system (persistent) ───────
    todo_block = _build_todo_block(base_path)
    if todo_block:
        system_prompt += todo_block

    return system_prompt


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
        if len(claim_clean) < 3 or _vre.match(r'^[\u4e00-\u9fff]+$', claim_clean):
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
        output += (
            "\n\n---\n## [SYSTEM] POST-TURN VERIFICATION FAILED\n"
            + "\n".join(f"- {c}" for c in corrections)
            + "\n\nThe claims above do not match the actual config file. "
              "Please re-execute and verify with `config(action=get)` before reporting."
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
    if max_tool_turns == 25 and _base_turns > 25:  # only override default (25)
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
            ctx.add_user(f"[SYSTEM] You have exceeded the maximum tool iterations ({max_tool_turns}). Synthesize results now. Do NOT call more tools.")
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
    return output, {
        "cost": round(session_cost, 4),
        "model": f"{model.provider}/{model.id}",
        "iterations": 1,
        "steps": 0,
        "adversarial": court_result["agreement_level"] if court_result else None,
        "adversarial_raw": court_result,
        "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        **_extra_info,
    }

    