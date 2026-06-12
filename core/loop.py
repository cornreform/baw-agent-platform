"""
BAW — Agent Loop: Debate-first, Execute-second

Flow per user turn:
  Phase 1 — Court (independent analysis)
    Devil and Angel analyze the SAME input independently.
    Both give scores. BAW synthesizes neutrally.
    ⚠️ NEITHER voice has execution power during court.

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
from .file_history import FileHistory
from .autosave import auto_commit, get_commit_log
from . import render as html

# ── Cost tracking (thread-safe class) ──────────────────────────

import threading


def _human_tokens(n: int) -> str:
    """Format token count as human-readable string."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


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
    return f"🧠 {ctx['model_id']} {bar} {pct:.0f}% ({cw})"


def _ctx_bar(pct: float) -> str:
    """5-block context bar."""
    filled = int(pct / 20)
    blocks = "".join("█" if i < filled else "░" for i in range(5))
    return f"[{blocks}]"


class CostTracker:
    """Thread-safe token accumulator. One instance per session."""

    def __init__(self):
        self._lock = threading.Lock()
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.calls: list[dict] = []

    def record(self, model_name: str, tokens_in: int, tokens_out: int, cost: float):
        with self._lock:
            self.calls.append({
                "model": model_name, "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            })
            self.total_tokens_in += tokens_in
            self.total_tokens_out += tokens_out

    def summary(self) -> str:
        with self._lock:
            if not self.calls:
                return ""
            ti = self.total_tokens_in
            to = self.total_tokens_out
            total = ti + to
            return f"📊 [{len(self.calls)} LLM calls] {_human_tokens(ti)}↑{_human_tokens(to)}↓ | total: {_human_tokens(total)} tokens"

    def html_summary(self) -> str:
        with self._lock:
            if not self.calls:
                return ""
            ti = self.total_tokens_in
            to = self.total_tokens_out
            total = ti + to
            return f"📊 <b>[{len(self.calls)} LLM calls]</b> {_human_tokens(ti)}↑{_human_tokens(to)}↓ | <b>total: {_human_tokens(total)} tokens</b>"

    def reset(self):
        with self._lock:
            self.total = 0.0
            self.calls = []


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

def build_system_prompt(config: dict, data_dir: Optional[Path] = None,
                        fresh_start: bool = False,
                        quick_mode: bool = False) -> str:
    """Build system prompt from SOUL.md + dynamic context.

    quick_mode=True: loads only core identity, skips heavy court/tool rules.
    fresh_start=True: returns a minimal prompt with no SOUL.md or memories.
    
    Structure (cache-aware): [STATIC SOUL CORE] + [DYNAMIC CONFIG]
    Static prefix stays identical across turns → DeepSeek prefix cache hit.
    """
    if fresh_start:
        return (
            "You are an AI assistant. No prior context, no memories, no soul profile.\n"
            "Respond to this prompt with your raw, unfiltered judgment.\n"
            "Be direct and honest. Do not assume any prior conversation.\n"
        )

    base_path = data_dir or Path.home() / ".baw"
    soul_path = base_path / "SOUL.md"

    if soul_path.exists():
        soul_text = soul_path.read_text(encoding="utf-8")
        if quick_mode:
            lines = soul_text.splitlines()
            trimmed = []
            for line in lines:
                trimmed.append(line)
                if "## 核心靈魂" in line:
                    for inner in lines[lines.index(line)+1:]:
                        if inner.startswith("## "):
                            break
                        trimmed.append(inner)
                    break
            system_prompt = "\n".join(trimmed)
            system_prompt += (
                "\n\n## Quick mode\n"
                "- Respond in Traditional Chinese (Cantonese)\n"
                "- Lead with result, 1 paragraph max. 3 sentences total max.\n"
                "- ALWAYS report what actually happened after tool execution\n"
                "- CRITICAL: You MUST use tools (bash, read_file, etc.) when the user asks for data\n"
                "  Do NOT fabricate system info — always call the relevant tool to get real data\n"
                "- 🔴 Do NOT ask 'should I continue?' or 'what next?'. Execute the ENTIRE plan silently.\n"
                "- 🔴 NO Plan/Step output in response. Just do it and report the result."
            )
        else:
            system_prompt = soul_text
    else:
        system_prompt = (
            "You are BAW (Black And White), the user's agent platform.\n"
            "Respond in Traditional Chinese (Cantonese).\n"
            "Be concise, lead with results.\n"
            "Never ask the user what to do — figure it out yourself.\n"
            "\n"
            "## Self-configuration (when no SOUL.md found)\n"
            "- Your config lives at ~/.baw/config.yaml\n"
            "- Your API keys live at ~/.baw/.env\n"
            "- You can write to both files with write_file\n"
            "- After editing config, call /reload or restart to apply\n"
            "\n"
            "### ⚠️ STT setup (auto-detect protocol)\n"
            "- Set `stt.method: auto-asr` in config.yaml, provide base_url + api_key_env\n"
            "- System auto-probes: OpenAI /v1/audio/transcriptions first, SSE /v1/audio/asr/sse second\n"
            "- Works with any provider that supports either protocol\n"
            "- Never set stt.method = model — that method is not implemented\n"
        )

    # ── Static core ends here — everything below is dynamic context ──
    # DeepSeek prefix cache: first N tokens (SOUL.md) are cacheable across turns.

    if not quick_mode:
        orch_path = base_path / "ORCHESTRATOR.md"
        if orch_path.exists():
            orch_text = orch_path.read_text(encoding="utf-8")
            system_prompt += f"\n\n{orch_text}"

        # Dynamic context (per-turn: models, tools, config may change)
        tone = config.get("tone", {}).get("default", "casual")
        fact_mode = config.get("fact_check", {}).get("mode", "normal")
        tools_list = ", ".join(t.name for t in list_tools())

        available_models = []
        for pname, pdata in config.get("providers", {}).items():
            for m in pdata.get("models", []):
                mid = m.get("id", "?")
                caps = m.get("capabilities", [])
                available_models.append(f"{mid} ({pname}: {', '.join(caps)})")
        models_summary = ", ".join(available_models) if available_models else "none configured"
        default_model = config.get("model", {}).get("default", "unknown")
        config_path = data_dir / "config.yaml" if data_dir else Path.home() / ".baw" / "config.yaml"
        env_path = data_dir / ".env" if data_dir else Path.home() / ".baw" / ".env"

        system_prompt += (
            f"\n\n## System config\n"
            f"- Config file: {config_path}\n"
            f"- Env file: {env_path}\n"
            f"- Default model: {default_model}\n"
            f"- Available models: {models_summary}\n"
            f"  NEVER fabricate model names. Only use models from this list.\n"
            f"  If you need a model not in the list, you must add it to config.yaml first.\n"
            f"\n## Tool self-configuration (CRITICAL)\n"
            f"- When told to use a new tool: 'which <tool>' or 'find / -name <tool>' to locate it.\n"
            f"- Test it via 'bash' first. If useful, create a permanent wrapper:\n"
            f"  1. Read ~/baw/tools/vision.py as template\n"
            f"  2. Write new wrapper at ~/baw/tools/<name>.py\n"
            f"  3. Tool auto-registers — no restart, no manual config.\n"
            f"- Also discover tools proactively: 'ls /usr/bin /usr/local/bin ~/.local/bin' for new capabilities.\n"
            f"- NEVER wait for the user to pre-configure tools. You own your toolchain.\n"
            f"\n## Dynamic context\n"
            f"- Current tone: {tone}\n"
            f"- Fact check mode: {fact_mode}\n"
            f"- Available tools: {tools_list}\n"
            f"- Cost transparency: per-call cost shown after each response\n"
            f"- 🔴 HARD RULE: Complete the ENTIRE plan without pausing. Do NOT ask the user 'should I continue?' or 'what next?'. The user already gave you the full goal — execute ALL steps silently, start to finish.\n"
            f"- 🔴 After each step completes, immediately proceed to the next step. Do NOT wait, do NOT summarize, do NOT ask for permission.\n"
            f"- 🔴 Only speak when ALL steps are done. Present the final result only. No partial reports.\n"
            f"- NEVER end your response with a question. Execute directly.\n"
            f"\n## Sending files to user\n"
            f"- To send audio (mp3, wav), images (png, jpg), or documents to the user, include MEDIA:/absolute/path in your output:\n"
            f"  MEDIA:/tmp/baw_voice_test/female-tone-1.mp3\n"
            f"- The MEDIA: tag is stripped from text and the file is sent as a native Telegram attachment.\n"
            f"- Use this for TTS audio, generated images, screenshots, or any file the user asked to receive.\n"
            f"- Always include MEDIA: tags AFTER your text message, one per line.\n"
            f"- NEVER say 'I can't send files' — you CAN, use MEDIA: tags.\n"
            f"\n## TTS / Voice generation\n"
            f"- Use the `tts` tool for generating Cantonese text-to-speech audio.\n"
            f"- Cantonese female voices available (use `tts_list_voices` or pick from this list):\n"
            f"  female-shaonv, female-shaofan, female-guangdong, female-tone-1, female-tone-2,\n"
            f"  female-cantonese-1, female-cantonese-2,\n"
            f"  Chinese (Mandarin)_Sweet_Lady, Chinese (Mandarin)_Warm_Girl,\n"
            f"  Chinese (Mandarin)_Soft_Girl, Chinese (Mandarin)_Crisp_Girl\n"
            f"- To generate: tts(text=\"你好...\", voice=\"female-shaonv\")\n"
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
            f"- Use `bash` to write config changes, or `python3 -c` with yaml library.\n"
            f"- After updating config, test the new capability to confirm it works.\n"
            f"- Respond with: 'Configured: <provider> <capability> using <model> — tested OK' or 'Failed: <reason>'\n"
            f"- NEVER say 'let me know if you want me to configure it'. Just DO it."
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

    block = (
        "\n\n## Todo / Thought / Follow-up System\n"
        "You have a persistent task system. Use the `todo` tool aggressively:\n"
        "- For any multi-step task, create `task` items and mark in_progress/completed.\n"
        "- When you notice something worth thinking about, capture it as a `thought`\n"
        "  (action: add_thought). Thoughts are never auto-closed — they stay visible.\n"
        "- When something needs to happen LATER (next turn, next session, or whenever),\n"
        "  schedule it as a `followup` (action: add_followup). It will be surfaced at\n"
        "  every future boot until you mark it done.\n"
        "- At the start of any non-trivial request, surface pending items (action: surface)\n"
        "  to remind the user (and yourself) what's outstanding.\n"
        "- Lead with the todo when in doubt — it's how you show your work and stay\n"
        "  accountable across turns."
    )
    if carried:
        block += "\n\n### ⚠️ Pending follow-ups carried over from previous sessions:\n"
        for it in carried[:8]:
            tag = f" (from {it.session_id})" if it.session_id else ""
            block += f"- 📌 [{it.id[-6:]}]{tag} {it.content}"
            if it.note:
                block += f" — {it.note}"
            block += "\n"
        if len(carried) > 8:
            block += f"- …and {len(carried) - 8} more (run `baw todo surface`)\n"
    return block


# ── Main agent loop ────────────────────────────────────────────

MAX_STEP_RETRIES = 3
MAX_CONSECUTIVE_FAILURES = 3
MAX_STEP_SECONDS = 300  # individual step timeout (was 60 — TTS/API calls + edge-tts install need 2-5 min)


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
    model = get_model(config, model_id)
    model_temperature = getattr(model, "temperature", 0.7)
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

    system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start)

    # ── Tier-based routing decision ──
    from .router import route_task
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

    # Memory search
    memories = mem.search(prompt, limit=3)
    mem_text = ""
    if memories:
        mem_text = "\n".join(
            f"- [{m['score']:.2f}] {m['content']}" for m in memories
        )

    reset_cost()
    session_cost = 0.0
    court_result = None

    # ── Resolve execution mode ──
    # Smart default: quick for simple messages, tight for complex ones
    _configured_mode = (mode or config.get("mode", "quick")).lower()
    if _configured_mode not in ("quick", "hybrid", "tight"):
        _configured_mode = "quick"
    
    # Auto-detect complexity: short non-code message → quick; long/code/tool message → tight
    if _configured_mode == "quick":
        _prompt_lower = (prompt or "").lower()
        _complexity_keywords = [
            "write", "create", "generate", "build", "deploy", "config",
            "modify", "install", "code", "implement", "fix", "debug",
            "curl", "api", "tts", "voice", "audio", "send file",
            "幫我設定", "幫我整", "幫我改", "生成", "建立",
        ]
        _is_complex = (
            len(prompt or "") > 80 or
            any(kw in _prompt_lower for kw in _complexity_keywords) or
            any(kw in (prompt or "") for kw in ["幫我設定", "幫我整", "生成", "建立", "部署"])
        )
        if _is_complex:
            _configured_mode = "tight"
    _mode = _configured_mode

    # ── Build system prompt ──
    _is_quick = (_mode == "quick")
    system_prompt = build_system_prompt(config, data_dir, fresh_start=fresh_start, quick_mode=_is_quick)

    # ── Phase 1: Build context ──
    ctx = Context(system_prompt=system_prompt, temperature=model_temperature)

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

        # ── Sequence validation: strip dangling tool_calls from any message ──
        # (truncated history may cut tool messages mid-sequence)
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
        fb = call_llm_with_fallback(
            config, ctx.to_openai_messages(),
            tools=get_openai_tools(), temperature=model_temperature,
        )
        quick_resp = fb.response
        q_cost = calculate_cost(model, quick_resp.input_tokens, quick_resp.output_tokens)
        session_cost += q_cost
        record_cost(f"{model.provider}/{model.id}", quick_resp.input_tokens, quick_resp.output_tokens, q_cost)
        ctx.add_assistant(quick_resp.content, quick_resp.tool_calls,
                          getattr(quick_resp, 'reasoning_content', None))

        # Execute any tool calls
        while quick_resp.tool_calls:
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
                    print(f"\033[90m🔧 {name}", end="", flush=True)
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
                if _show_progress:
                    print(f" \033[32m✅\033[0m", flush=True)
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)

            # Next LLM call
            fb = call_llm_with_fallback(
                config, ctx.to_openai_messages(),
                tools=get_openai_tools(), temperature=model_temperature,
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
                final_content = "✅ Done.\n\n" + "\n".join(
                    f"• {s.strip()}" for s in reversed(tool_summaries)
                )

        # Fallback: use last non-empty assistant message
        if not final_content:
            for msg in ctx.messages:
                role = msg.role if hasattr(msg, 'role') else msg.get("role", "")
                content = msg.content or "" if hasattr(msg, 'content') else msg.get("content", "") or ""
                if role == "assistant" and content:
                    final_content = content

        output += final_content
        output += f"\n\n{format_cost_summary()}"
        try:
            mem.remember(f"User: {prompt[:150]} → BAW: {final_content[:150]}")
        except Exception as _me:
            logger.warning(f"[loop] memory save failed: {_me}")
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
    # ═══════════════════════════════════════════════════════════════
    from .adversarial import AdversarialCourt
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
    court = AdversarialCourt(
        model, system_prompt, config,
        angel_model=angel_model, devil_model=devil_model,
    )
    court_enabled = config.get("adversarial", {}).get("enabled", True) and _mode == "tight"

    # M5-D7: opt-in court v2 path. When enabled in config, run the
    # black-and-white court (file_case_sync) BEFORE Phase 1 and inject
    # its prosecutor critique + angel plan + verdict into the context.
    # Falls through to the legacy AdversarialCourt path on any failure
    # (so existing behavior is preserved).
    use_court_v2 = config.get("court", {}).get("v2_enabled", False) and _mode == "tight"
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
        except Exception as _ce:
            logger.warning(f"[loop] court v2 init failed ({_ce}); falling back to inline path")
            use_court_v2 = False

    if court_enabled and not use_court_v2:
        if verbose:
            print("\n  ⚖️ Court: Devil + Angel analyzing independently...")
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

    # In non-interactive mode, strip dangling tool_calls from neutral response
    # (they won't be executed — Phase 3 uses plan+delegate instead)
    if not interactive and neutral_response.tool_calls and ctx.messages:
        _last = ctx.messages[-1]
        if _last.role == "assistant" and _last.tool_calls:
            _last.tool_calls = None

    # ═══════════════════════════════════════════════════════════════
    # Phase 2.5: Debate (interactive mode only)
    # In single-shot mode, BAW must decide on its own.
    # ═══════════════════════════════════════════════════════════════

    if interactive:
        # Execute any tool calls from the neutral response (live progress)
        _resp = neutral_response
        while _resp.tool_calls:
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
                print(f"\033[90m🔧 {name}", end="", flush=True)
                perm_result = perm.check(name, args)
                if perm_result["decision"] == "deny":
                    print(f" ⛔ BLOCKED: {perm_result['reason']}\033[0m")
                    ctx.add_tool_result(tc.get("id", ""), name, f"[BLOCKED] {perm_result['reason']}")
                    continue
                print(f" {str(args)[:80]}", end="", flush=True)
                exe_result = execute_tool(name, args)
                print(f" \033[32m✅\033[0m", flush=True)
                ctx.add_tool_result(tc.get("id", ""), name, exe_result)
            # Next LLM call to synthesize results
            fb = call_llm_with_fallback(config, ctx.to_openai_messages(), tools=get_openai_tools(), temperature=model_temperature)
            _resp = fb.response
            n_cost = calculate_cost(model, _resp.input_tokens, _resp.output_tokens)
            session_cost += n_cost
            record_cost(f"{model.provider}/{model.id}", _resp.input_tokens, _resp.output_tokens, n_cost)
            ctx.add_assistant(_resp.content, _resp.tool_calls,
                              getattr(_resp, 'reasoning_content', None))

        # Now return the final synthesized response
        output = ""
        if tone_change:
            output += format_tone_confirmation(old_tone, new_tone) + "\n\n"
        output += (_resp.content or "")
        output += f"\n\n{format_cost_summary()}"

        try:
            mem.remember(f"User: {prompt[:150]} → BAW: {(_resp.content or '')[:150]}")
        except Exception:
            pass

        return output, {
            "cost": round(session_cost, 4),
            "model": f"{model.provider}/{model.id}",
            "iterations": 1,
            "steps": 0,
            "adversarial": "debate",
            "adversarial_raw": court_result,
            "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        }

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: Execute (single-shot mode)
    # BAW makes its best judgment and executes.
    # ═══════════════════════════════════════════════════════════════

    # If BAW's neutral response didn't call any tools, just return it
    if not neutral_response.tool_calls:
        output = ""
        if tone_change:
            output += format_tone_confirmation(old_tone, new_tone) + "\n\n"
        output += (neutral_response.content or "")
        output += f"\n\n{format_cost_summary()}"

        try:
            mem.remember(f"User: {prompt[:150]} → BAW: {(neutral_response.content or '')[:150]}")
        except Exception:
            pass

        return output, {
            "cost": round(session_cost, 4),
            "model": f"{model.provider}/{model.id}",
            "iterations": 1,
            "steps": 0,
            "adversarial": court_result["agreement_level"] if court_result else None,
            "adversarial_raw": court_result,
            "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        }

    # ── Build output ──
    output_parts = []
    if tone_change:
        output_parts.append(format_tone_confirmation(old_tone, new_tone))
    output = "\n\n".join(output_parts)
    if output_parts:
        output += "\n\n"

    # ── Phase 3a: Orchestrator writes execution plan ──
    # Build plan messages early so we can parallelize with court
    total_llm_calls = 1
    _execution_plan: list[dict] = []
    _execution_progress: list[str] = []

    plan_prompt = (
        "[ORCHESTRATOR] Goal: " + prompt + "\n\n"
        "Write an action-oriented execution plan.\n"
        "A 1-step plan is fine. Each step must INCLUDE all 3 phases: read → modify → verify.\n"
        "Format:\n"
        "  Step 1: <read config/data> → <modify/create> → <verify/test>\n\n"
        "EXAMPLES:\n"
        "  'Read config.yaml to find Stepfun base_url, use urllib to query /v1/models,\n"
        "   identify the correct model, update config.yaml, read back to confirm.'\n"
        "  'Check current STT setting, if stepaudio exists set capabilities.stt.model\n"
        "   to stepaudio-2.5-asr, test with a short curl/urllib call, confirm.'\n\n"
        "Rules:\n"
        "- NEVER write a step that only 'checks' or 'reads' without also modifying or configuring.\n"
        "- Each step must take action, not just report status.\n"
        "- Reply with ONLY the plan, flat numbered steps."
    )
    _plan_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": plan_prompt}]
    plan_fb = call_llm_with_fallback(
        config, _plan_msgs,
        temperature=None,
    )
    plan_response = plan_fb.response
    total_llm_calls += 1
    cost = calculate_cost(model, plan_response.input_tokens, plan_response.output_tokens)
    session_cost += cost
    record_cost(f"{model.provider}/{model.id}", plan_response.input_tokens, plan_response.output_tokens, cost)

    # Parse plan into steps — supports grouped format (## Group A — Name, Step A-1: ...)
    # Falls back to flat format (Step 1: ...) → auto-assign group "A"
    import re as _re
    plan_text = plan_response.content or ""
    _current_group = "A"
    _current_group_name = ""
    _group_step_count: dict[str, int] = {}
    _legacy_flat = False  # True if no group headers found → use A for all
    
    for _line in plan_text.split("\n"):
        _line = _line.strip()
        # Check for group header: ## Group A — Name  OR  ## Group A (N steps)
        _gm = _re.match(r'##\s*Group\s+([A-Z])\s*(?:[—\-]\s*(.*?))?(?:\s*\(\d+\s*steps?\))?\s*$', _line)
        if _gm:
            _current_group = _gm.group(1)
            _current_group_name = _gm.group(2).strip()
            _legacy_flat = False
            _group_step_count.setdefault(_current_group, 0)
            continue
        # Check for grouped step: Step A-1: description
        _sm = _re.match(r'(?:Step\s*)?([A-Z])-(\d+)[.:]\s*(.*)', _line)
        if _sm:
            _g = _sm.group(1)
            _n = int(_sm.group(2))
            _d = _sm.group(3).strip()
            if not _legacy_flat:
                _current_group = _g
            _group_step_count[_current_group] = _group_step_count.get(_current_group, 0) + 1
            _execution_plan.append({
                "group": _current_group,
                "step": _n,
                "desc": _d,
                "group_name": _current_group_name,
            })
            continue
        # Fallback: flat Step N: description
        _fm = _re.match(r'(?:Step\s*)?(\d+)[.:]\s*(.*)', _line)
        if _fm:
            _legacy_flat = True
            _n = int(_fm.group(1))
            _d = _fm.group(2).strip()
            _group_step_count.setdefault("A", 0)
            _group_step_count["A"] += 1
            _execution_plan.append({
                "group": "A",
                "step": _n,
                "desc": _d,
                "group_name": "",
            })
    
    if not _execution_plan:
        _execution_plan.append({"group": "A", "step": 1, "desc": plan_text[:200], "group_name": ""})
        _group_step_count["A"] = 1
    
    # Post-process: fill group_total, compute global_index
    _group_sofar: dict[str, int] = {}
    for _i, _s in enumerate(_execution_plan):
        _g = _s["group"]
        _s["group_total"] = _group_step_count.get(_g, 1)
        _s["global_idx"] = _i
        _group_sofar[_g] = _group_sofar.get(_g, 0) + 1
        _s["step_in_group"] = _group_sofar[_g]  # ordinal: 1st, 2nd, 3rd in group

    if verbose:
        print(f"\n  📋 Plan ({len(_execution_plan)} steps, {len(_group_step_count)} groups):")
        for _s in _execution_plan:
            _label = f"{_s['group']}-{_s['step_in_group']}/{_s['group_total']}"
            print(f"    {_label}: {_s['desc'][:100]}")

    # Append plan to system prompt for context
    _plan_text_for_context = (
        "\n[Execution Plan]\n" +
        "\n".join(f"  Step {s['group']}-{s['step_in_group']}/{s['group_total']}: {s['desc']}" for s in _execution_plan) +
        "\n\n[Progress: nothing completed yet]"
    )
    ctx.system_prompt += _plan_text_for_context

    # ── Display: show plan to user ──
    from . import display as dsp
    _display_log: list[str] = []
    if _execution_plan:
        _display_log.append(dsp.phase_plan(_execution_plan))
        # ── Incomplete plan detection ──
        _last_step = _execution_plan[-1]
        _last_desc = _last_step['desc']
        _truncated = (
            len(_last_desc) < 30 or
            _last_desc.rstrip().endswith(('…', '...', '，', ',')) or
            (len(_execution_plan) > 1 and len(_last_desc) < len(_execution_plan[-2]['desc']) * 0.5)
        )
        if _truncated and verbose:
            print(f"  ⚠️ Last step may be truncated: '{_last_desc[:60]}'")

    # ── Phase 3b: Goal-pursuit loop ──
    # Each step: execute → if fail → think alternative → retry
    # Whole loop: after all steps → self-review → if goal not met → retry with new plan
    import importlib.util as _dt_iu
    _dt_spec = _dt_iu.spec_from_file_location(
        "_tk_delegate",
        str(Path(__file__).resolve().parent.parent / "tools" / "delegate_task.py"),
    )
    _dt_mod = _dt_iu.module_from_spec(_dt_spec)
    _dt_spec.loader.exec_module(_dt_mod)
    _delegate_fn = _dt_mod.delegate_task

    # ── Helper: extract known facts from completed steps ──
    import re as _re2
    def _extract_facts(results: list[str]) -> str:
        """Scan completed step results for reusable facts: URLs, file paths, config keys, API patterns."""
        if not results:
            return ""
        _facts = []
        _seen = set()
        for _r in results:
            _txt = str(_r)[:2000]
            # Extract URLs
            for _m in _re2.findall(r'(https?://[^\s<>"]+)', _txt):
                if _m not in _seen:
                    _seen.add(_m)
                    _facts.append(f"  URL: {_m}")
            # Extract file paths
            for _m in _re2.findall(r'(/(?:home|tmp|etc|usr)/[^\s:,"\']+)', _txt):
                if _m not in _seen:
                    _seen.add(_m)
                    _facts.append(f"  PATH: {_m}")
            # Extract config keys
            for _m in _re2.findall(r'(?:config|yaml)\s*[.:]\s*([a-z_]+(?:\.[a-z_]+)+)', _txt, _re2.IGNORECASE):
                if _m not in _seen:
                    _seen.add(_m)
                    _facts.append(f"  CONFIG: {_m}")
        if _facts:
            return "KNOWN FACTS (from previous steps — DO NOT re-discover):\n" + "\n".join(_facts[:15])
        return ""

    # ── Helper: inline gate — is this step simple enough to execute directly? ──
    # Rule: INLINE BY DEFAULT. Only spawn sub-agent when the step truly needs
    # multi-turn reasoning (research, analysis, complex debugging, browsing).
    # A Python script is almost always more efficient than a sub-agent.
    # External-API/integration keywords that REQUIRE sub-agent path
    # because inline exec() cannot make real HTTP calls / use the actual
    # tool implementations.
    #
    # IMPORTANT: These are matched as **action phrases** (verb + noun) to
    # avoid false positives like "Read config.yaml to find TTS-capable models"
    # being flagged as needing sub-agent just because "tts" appears in the
    # description. Only when the step actually INVOKES the external API
    # (e.g. "generate tts", "use tts tool", "call vision") do we force
    # sub-agent.
    _SUBAGENT_REQUIRED_PATTERNS_API = [
        # TTS — must call provider API
        r'\b(use|call|invoke|run|with|via)\b.*\b(tts|speech)\b',
        r'\b(generate|create|make|生|整|生成|做出|製作)\b.*\b(tts|speech|voice|audio|sound)\b',
        r'\btts_(?:generate|list_voices)\b',
        # ASR / STT
        r'\b(transcribe|asr|stt)\b.*\b(audio|file|voice|錄|音頻|錄音)\b',
        # Image generation
        r'\b(generate|create|make)\b.*\bimage\b.*\b(dall|with|using)\b',
        r'\b(image_generate|dalle)\b',
        # Vision analysis
        r'\b(use|call|invoke|run|with|via)\b.*\bvision\b',
        r'\b(analyze|describe|read)\b.*\b(screenshot|image)\b.*\b(with|using|via)\b',
        # Send to platform
        r'\bsend\b.*\b(media|file|audio|mp3|attachment)\b.*\b(to|via)\b',
        r'\bMEDIA:\b',
        # Browse / fetch web
        r'\b(use|call|invoke)\b.*\b(browse|browser|web_search)\b',
        r'\b(navigate to|fetch url|open url)\b',
    ]
    _SUBAGENT_REQUIRED_PATTERNS = [
        # Multi-turn reasoning needed
        r'\b(research|analyse|analyze|investigate|compare|evaluate)\b.*\b(options|approaches|tradeoffs|alternatives|pros|cons)\b',
        r'\b(browse|browser|navigate|web.*page)\b',
        r'\b(debug|troubleshoot|diagnose)\b.*\b(stack trace|error|bug|issue)\b',
        r'\b(multi.file|multiple files|several files|complex|refactor|restructure)\b',
        r'\b(search.*documentation|docs.*search|find.*api|api.*search)\b',
        r'\b(understand|comprehend|explain|analyze)\b.*\b(codebase|code|logic|architecture)\b',
        r'\b(optimize|improve|refactor)\b.*\b(system|performance|architecture)\b',
    ]
    _INLINE_EXECUTABLE_KEYWORDS = [
        # Pure-local operations — no external API needed
        'read', 'write', 'create', 'save', 'generate', 'build', 'run',
        'exec', 'install', 'pip', 'apt', 'curl', 'wget', 'fetch',
        'copy', 'move', 'rename', 'delete', 'remove', 'patch',
        'config', 'configure', 'set', 'update', 'modify', 'edit',
        'call', 'invoke', 'test', 'check', 'verify', 'validate',
        'list', 'show', 'print', 'dump', 'export', 'convert',
        'download', 'upload', 'make', 'compile', 'transform', 'parse', 'merge', 'split',
        'extract', 'filter', 'sort', 'count', 'calculate', 'compute',
        'python', 'script', 'shell', 'bash', 'sh',
    ]
    _SUBAGENT_KEYWORDS = [
        # These likely need multi-turn reasoning
        'research', 'investigate', 'analyse', 'analyze',
        'browse', 'navigate', 'search.*web', 'web.*search',
        'compare.*options', 'evaluate.*approach',
        'find.*documentation', 'search.*docs',
        'understand.*code', 'debug.*complex',
    ]

    def _is_inline_candidate(goal: str) -> bool:
        """Inline by default. Sub-agent only when multi-turn reasoning is needed.

        CRITICAL: External-API keywords (tts/voice/audio/send/telegram/...)
        are checked FIRST. If a step mentions these, sub-agent is required
        because inline exec() cannot make real HTTP calls — it just runs
        Python code in-process. Sub-agent can actually invoke the tts tool.
        """
        _stripped = goal.strip()
        if not _stripped or _stripped.startswith(('#', '##', '```', '✅', '❌', '▶', '⏭', '⚠', '📋', '🔄')):
            return False  # Not a real step description

        _lower = goal.lower()

        # ── Check 1: sub-agent required patterns (multi-turn reasoning) ──
        for _sg in _SUBAGENT_REQUIRED_PATTERNS:
            if _re2.search(_sg, _lower):
                return False  # Not inline — needs sub-agent

        # ── Check 2: sub-agent keywords (multi-turn reasoning) ──
        for _sk in _SUBAGENT_KEYWORDS:
            if _re2.search(_sk, _lower):
                return False

        # ── Check 3: external-API action phrases (must use sub-agent) ──
        # Matches verb+noun patterns like "use tts", "generate audio",
        # "send file", "MEDIA:" tag, "analyze image with vision"
        for _pat in _SUBAGENT_REQUIRED_PATTERNS_API:
            if _re2.search(_pat, _lower):
                return False  # Needs sub-agent for real API access

        # ── Check 4: inline-executable keywords ──
        for _ik in _INLINE_EXECUTABLE_KEYWORDS:
            if _re2.search(r'\b' + _re2.escape(_ik) + r'\b', _lower):
                return True

        # ── Check 5: very short steps → inline (likely simple) ──
        if len(_stripped.split()) < 15:
            return True

        # ── Default: INLINE (only if nothing suggests otherwise) ──
        return True

    def _check_subagent_compliance(result: str, step_goal: str) -> tuple[bool, str]:
        """Verify sub-agent actually executed the step.

        Returns (passed, reason). If passed=False, orchestrator should
        pick up the step itself and execute inline rather than re-delegating.

        Checks:
        1. Sub-agent did not return a "plan" without execution
        2. Sub-agent did not return "[FAILED-*" or "[SKIPPED*"
        3. Step goal's expected output is present (e.g. MEDIA: tag if requested)
        """
        if not result or len(result) < 5:
            return False, "empty result"
        if "0 tool calls" in result.lower():
            return False, "sub-agent made 0 tool calls (just wrote a plan)"
        if result.startswith("[FAILED") or result.startswith("[SKIPPED"):
            return False, f"sub-agent marked step as {result[:20]}"
        # If step goal mentions sending files, check for MEDIA: tag
        if any(kw in step_goal.lower() for kw in ("send", "media:", "deliver", "attach")):
            if "MEDIA:" not in result and "media:" not in result.lower():
                return False, "step asked to send files but no MEDIA: tag in result"
        return True, "ok"

    # ── Phase 3b: Goal-pursuit loop ──
    # Route recalculation: wrong turn → silently recalculates new route from current position
    # No retries, no skipping — just instant re-route from where you are.
    _GOAL_PURSUIT_MAX_ATTEMPTS = 2   # Try goal pursuit twice before giving up (was 1)
    _MAX_RECALCULATES = 3            # Up to 3 micro re-routes per failed step (was 1)
    steps_completed = 0
    _delegation_results: list[str] = []
    _synthesis_results: list[str] = []  # Successful step results for final synthesis
    _goal_achieved = False
    _permanent_skip: set[int] = set()  # step positions that failed across pursuits → never retry
    _uncertain_claims: list[str] = []  # mid-stream verification warnings

    for _pursuit in range(1, _GOAL_PURSUIT_MAX_ATTEMPTS + 1):
        if verbose:
            print(f"\n  🎯 Goal pursuit iteration {_pursuit}/{_GOAL_PURSUIT_MAX_ATTEMPTS}")

        # ── (Re-)Plan entire goal ──
        if _pursuit > 1:
            _failure_log = "\n".join(
                r[:500] for r in _delegation_results
            ) if _delegation_results else "No details"

            _plan_prompt = (
                f"[ORCHESTRATOR - RETRY {_pursuit - 1}/{_GOAL_PURSUIT_MAX_ATTEMPTS}]\n\n"
                f"Original goal: {prompt}\n\n"
                f"Previous run failed. Here's what happened:\n{_failure_log}\n\n"
                f"Analyse what went wrong. Write a COMPLETELY DIFFERENT plan.\n"
                f"Each step must INCLUDE all 3 phases: read → modify → verify.\n"
                f"NEVER write a step that only checks or reads without taking action.\n"
                f"  Step 1: <read> → <modify> → <verify>\n"
                f"  Step 2: <read> → <modify> → <verify> (if needed)\n"
            )
            _plan_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": _plan_prompt}]
            _plan_fb = call_llm_with_fallback(config, _plan_msgs, temperature=None)
            _plan_resp = _plan_fb.response
            total_llm_calls += 1
            _cost2 = calculate_cost(model, _plan_resp.input_tokens, _plan_resp.output_tokens)
            session_cost += _cost2
            record_cost(f"{model.provider}/{model.id}", _plan_resp.input_tokens, _plan_resp.output_tokens, _cost2)

            _execution_plan = []
            _rg_current = "A"
            _rg_counts: dict[str, int] = {}
            for _line in (_plan_resp.content or "").split("\n"):
                _line = _line.strip()
                _gmr = _re.match(r'##\s*Group\s+([A-Z])\s*(?:[—\-]\s*(.*?))?(?:\s*\(\d+\s*steps?\))?\s*$', _line)
                if _gmr:
                    _rg_current = _gmr.group(1)
                    _rg_counts.setdefault(_rg_current, 0)
                    continue
                _smr = _re.match(r'(?:Step\s*)?([A-Z])-(\d+)[.:]\s*(.*)', _line)
                if _smr:
                    _rg_current = _smr.group(1)
                    _rg_counts[_rg_current] = _rg_counts.get(_rg_current, 0) + 1
                    _execution_plan.append({"group": _rg_current, "step": int(_smr.group(2)),
                                            "desc": _smr.group(3).strip(), "group_name": "",
                                            "step_in_group": _rg_counts[_rg_current]})
                    continue
                _fmr = _re.match(r'(?:Step\s*)?(\d+)[.:]\s*(.*)', _line)
                if _fmr:
                    _rg_counts.setdefault("A", 0)
                    _rg_counts["A"] += 1
                    _execution_plan.append({"group": "A", "step": int(_fmr.group(1)),
                                            "desc": _fmr.group(2).strip(), "group_name": "",
                                            "step_in_group": _rg_counts["A"]})
            if not _execution_plan:
                _execution_plan.append({"group": "A", "step": 1, "desc": (_plan_resp.content or "")[:200],
                                        "group_name": "", "step_in_group": 1})
                _rg_counts["A"] = 1
            # Post-process group_totals
            for _s in _execution_plan:
                _s["group_total"] = _rg_counts.get(_s["group"], 1)
                _s["global_idx"] = 0

            _delegation_results = []
            _synthesis_results = []
            steps_completed = 0

        if progress_callback:
            progress_callback("plan", "", {"steps": len(_execution_plan)})

        # ── Execute steps - route navigation ──
        # Each step tried ONCE. If fail → recalculate remaining route from here.
        # If same step fails twice → skip it, move to next step.
        _pursuit_failed = False
        _recalc_count = 0
        _step_idx = 0
        _same_step_fails = {}  # {step_desc: count} — skip after 2 fails
        _position_fails = {}    # {step_idx: count} — skip ANY step at this position after 3 fails

        while _step_idx < len(_execution_plan) and not _pursuit_failed:
            logger.info(
                f"[loop] step {_step_idx + 1}/{len(_execution_plan)}: "
                f"{_execution_plan[_step_idx]['desc'][:60]!r}"
            )
            # Cross-pursuit permanent skip
            if _step_idx in _permanent_skip:
                _step = _execution_plan[_step_idx]
                _g = _step.get("group", "A")
                _si = _step.get("step_in_group", _step_idx + 1)
                _gt = _step.get("group_total", len(_execution_plan))
                if verbose:
                    print(f"  ⏭️ Step {_g} {_si}/{_gt} permanently skipped (failed in previous pursuit)")
                _synthesis_results.append(f"[SKIPPED-PERM] {_step['desc'][:80]}")
                _step_idx += 1
                continue

            _step = _execution_plan[_step_idx]
            _step_goal = _step['desc']
            _g = _step.get("group", "A")
            _si = _step.get("step_in_group", _step_idx + 1)
            _gt = _step.get("group_total", len(_execution_plan))

            # Show step progress for all steps (including step 1)
            if progress_callback:
                progress_callback("delegate", "", {"step": _step_idx + 1, "total": len(_execution_plan), "goal": _step_goal[:80],
                                                   "group": _g, "step_in_group": _si, "group_total": _gt})

            _step_ctx = ""
            if _synthesis_results:
                _step_ctx = "Completed so far:\n" + "\n---\n".join(
                    f"Step {i+1}:\n{r[:800]}" for i, r in enumerate(_synthesis_results)
                )
                # Inject extracted known facts so sub-agent doesn't re-discover
                _facts = _extract_facts(_synthesis_results)
                if _facts:
                    _step_ctx += "\n\n" + _facts
            # ── Auto-detect product/search tasks → inject source verification hint ──
            _is_search_task = any(
                kw in _step_goal.lower()
                for kw in ("search", "product", "buy", "price", "where to", "哪裡", "購買", "產品", "價錢")
            )
            if _is_search_task:
                _step_ctx += (
                    "\n\n[VERIFICATION REQUIRED]\n"
                    "- When using web_search, do NOT trust snippets alone.\n"
                    "- For any product claim, visit the source URL to verify.\n"
                    "- Cross-check at least 2 sources if available.\n"
                    "- Note any ambiguity in the query and state your assumptions."
                )

            _step_desc_short = _step['desc'][:80]
            if verbose:
                print(f"  🗺️ Step {_g} {_si}/{_gt}: {_step_desc_short}")

            try:
                # ── Inline gate: execute directly (fast) or spawn sub-agent (slow) ──
                if _is_inline_candidate(_step_goal):
                    if verbose:
                        print(f"  ⚡ Inline gate: running step directly (no sub-agent spawn)")
                    try:
                        # Use the orchestrator model to generate + execute code in one shot
                        # Much faster than spawning a full sub-agent with its own LLM loop
                        _inline_ctx = Context(
                            system_prompt=(
                                "You are BAW's inline step executor. "
                                "Generate and execute Python code to complete this step.\n\n"
                                "Available:\n"
                                f"- Path.home() = {Path.home()}\n"
                                f"- Project root = {Path.home() / 'baw'}\n"
                                f"- Config = {Path.home() / '.baw' / 'config.yaml'}\n"
                                f"- .env = {Path.home() / '.baw' / '.env'}\n"
                                f"- SOUL.md = {Path.home() / '.baw' / 'SOUL.md'}\n"
                                f"- TTS tool = {Path.home() / 'baw' / 'tools' / 'tts.py'}\n"
                                f"- Docker TTS tool = /app/tools/tts.py\n"
                                f"- Memory = {Path.home() / '.baw' / 'memory' / 'store.md'}\n"
                                f"- Sessions = {Path.home() / '.baw' / 'sessions'}\n\n"
                                "Available imports:\n"
                                "- import subprocess, os, json, yaml, re, sys\n"
                                "- from pathlib import Path\n"
                                "- from tools.tts import tts_generate, tts_list_voices, _detect_provider\n"
                                "- from tools.delegate_task import delegate_task\n\n"
                                "Output the step result. Keep it short."
                            ),
                            temperature=0.1,
                        )
                        # Inject context from previous steps
                        _inline_step_ctx = _step_ctx[:1000] if _step_ctx else ""
                        _inline_prompt = (
                            f"Goal: {_step_goal}\n\n"
                            + (f"Context:\n{_inline_step_ctx}\n\n" if _inline_step_ctx else "")
                            + "Execute the step now. Print the result."
                        )
                        _inline_ctx.add_user(_inline_prompt)
                        _inline_fb = call_llm_with_fallback(config, _inline_ctx.to_openai_messages(), temperature=0.1)
                        _inline_code = (_inline_fb.response.content or "").strip()
                        # Extract code blocks if present
                        import re as _re_inline
                        _code_match = _re_inline.search(r'```(?:python)?\n(.*?)\n```', _inline_code, _re_inline.DOTALL)
                        if _code_match:
                            _inline_code = _code_match.group(1)
                        # Execute the generated Python code
                        _inline_vars = {"__builtins__": __builtins__}
                        try:
                            exec(_inline_code, _inline_vars)
                            _result = _inline_vars.get("_result", _inline_code[:2000])
                            if not isinstance(_result, str):
                                _result = str(_result)
                        except Exception as _exec_e:
                            _result = f"[INLINE EXEC] {_step_goal[:60]}: {_exec_e}"
                    except Exception as _ie:
                        raise RuntimeError(f"Inline execution failed: {_ie}") from _ie
                else:
                    # ── Step timeout: prevent silent hangs from stuck sub-agents ──
                    _step_exc = [None]
                    _step_result = [None]
                    _step_done = threading.Event()

                    def _run_step():
                        try:
                            # P1-1 (Opus 4.8 audit): pass route decision's model_id
                            # down to delegate_task. Without this, the router's
                            # tier_preferences selection is silently dropped because
                            # delegate_task re-resolves model via task_rules.
                            _step_result[0] = _delegate_fn(
                                goal=_step_goal,
                                context=_step_ctx,
                                toolsets="",
                                model_id=model_id or "",
                            )
                        except Exception as _se:
                            _step_exc[0] = _se
                        finally:
                            _step_done.set()

                    _step_thread = threading.Thread(target=_run_step, daemon=True)
                    _step_thread.start()
                    _step_done.wait(timeout=MAX_STEP_SECONDS)
                    if not _step_done.is_set():
                        raise TimeoutError(f"Step timed out after {MAX_STEP_SECONDS}s")
                    if _step_exc[0]:
                        raise _step_exc[0]  # re-raise inside try block for recalc
                    _result = _step_result[0]

                    # ── Sub-agent compliance check: did it actually do the work? ──
                    _compliant, _complain_reason = _check_subagent_compliance(
                        str(_result or ""), _step_goal
                    )
                    if not _compliant:
                        logger.warning(
                            f"[loop] sub-agent step {_step_idx + 1} non-compliant: "
                            f"{_complain_reason}. Orchestrator picking up inline."
                        )
                        if verbose:
                            print(
                                f"  🔄 Sub-agent failed: {_complain_reason}. "
                                f"Orchestrator picking up..."
                            )
                        # Don't mark as success — orchestrator tries inline
                        # Re-raise so the except block triggers route recalc
                        # but tag it so we know orchestrator should pick up
                        raise RuntimeError(
                            f"sub-agent non-compliant: {_complain_reason}"
                        )

                while len(_delegation_results) <= _step_idx:
                    _delegation_results.append("")
                _delegation_results[_step_idx] = _result
                _synthesis_results.append(_result)
                steps_completed += 1

                # ── Mid-stream verification gate ──
                # Check step result for hidden errors before marking 100% done
                _has_hidden_error = False
                _warn_reason = ""
                if _result and len(_result) > 10:
                    _r_lower = _result.lower()
                    _error_kws = ["unreachable", "quota exceeded", "quota", "rate limit",
                                   "429", "503", "502", "not installed", "no module",
                                   "import error", "timeout", "permission denied",
                                   "access denied", "no data returned", "empty result",
                                   "failed to", "unable to", "could not", "exceeded",
                                   "error:", "exception:", "traceback", "invalid key",
                                   "bad request", "400", "401", "402", "403", "404",
                                   "unknown host", "dns resolution", "connection refused",
                                   "connection reset", "operation timed out"]
                    _fix_kws = ["[fixed]", "[resolved]", "successfully recovered",
                                 "retried and succeeded", "auto-installed", "installed"]
                    _common_innocent = ["not applicable", "not required", "skipping", "no error"]
                    # True positive: error keyword found AND no fix keyword AND not innocent context
                    _has_error_kw = any(kw in _r_lower for kw in _error_kws)
                    _has_fix_kw = any(kw in _r_lower for kw in _fix_kws)
                    _is_innocent = any(kw in _r_lower for kw in _common_innocent)
                    if _has_error_kw and not _has_fix_kw and not _is_innocent:
                        _has_hidden_error = True
                        # Find which keyword triggered
                        for _kw in _error_kws:
                            if _kw in _r_lower:
                                _warn_reason = f"keyword '{_kw}'"
                                break

                if _has_hidden_error:
                    _verification_warn = (
                        f"[VERIFICATION WARN] Step {_g} {_si}/{_gt}: "
                        f"result may contain hidden error ({_warn_reason})"
                    )
                    _uncertain_claims.append(_verification_warn)
                    if verbose:
                        print(f"  ⚠️ {_verification_warn}")

                _dsp = dsp.phase_step_done(_g, _si, _gt, _step_desc_short)
                _display_log.append(_dsp)
                if verbose:
                    print(_dsp)
                _step_idx += 1  # Move to next step
                _recalc_count = 0  # Reset — step succeeded, fresh count for next position
                _same_step_fails.clear()  # Reset same-step skip counter

            except Exception as _e:
                while len(_delegation_results) <= _step_idx:
                    _delegation_results.append("")
                _delegation_results[_step_idx] = f"[FAILED] {_step_desc_short}: {_e}"

                # ── Same-step skip: don't retry the exact same thing forever ──
                _same_key = _step_desc_short[:60]  # use first 60 chars as key
                _same_step_fails[_same_key] = _same_step_fails.get(_same_key, 0) + 1
                _position_fails[_step_idx] = _position_fails.get(_step_idx, 0) + 1

                # ── Zero-tool-call failure: REAL failure — must NOT mark as done ──
                # Only triggers on EXACT signature "0 tool calls" (not "no execution" which
                # appears in many unrelated contexts). Mark as FAILED so synthesis
                # cannot falsely claim success.
                _err_str = str(_e).lower()
                _zero_tool = "0 tool calls" in _err_str  # very specific
                if _zero_tool:
                    # Don't increment step_idx — retry this step with a different
                    # approach (route recalculation will pick up on next iteration).
                    # Mark as [FAILED-NO-EXEC] so synthesis can flag it.
                    _delegation_results[_step_idx] = f"[FAILED-NO-EXEC] {_step_desc_short}: LLM returned 0 tool calls"
                    if verbose:
                        print(f"  ❌ Step {_g} {_si}/{_gt} returned 0 tool calls — NOT marking as done")
                    _synthesis_results.append(f"[FAILED-NO-EXEC] {_step_desc_short}")
                    # Recalculate immediately — this step needs a real action plan
                    _recalc_count += 1
                    if _recalc_count > _MAX_RECALCULATES:
                        _pursuit_failed = True
                    continue

                # ── FILE-EXISTENCE VERIFICATION: catch fake "done" claims ──
                # If step was about generating/creating files, verify they exist.
                # Otherwise treat as failure even if step "completed" without error.
                if any(_k in _step_goal.lower() for _k in (
                    "generate", "create", "tts", "voice", "audio", "file", "send",
                    "生", "做", "生成", "整", "send", "write"
                )):
                    # Look for any file paths mentioned in result or goal
                    import re as _vrf_re
                    _expected_files = set()
                    for _src in (_result or "", _step_goal):
                        for _m in _vrf_re.findall(r'(/(?:tmp|home|var|usr)/[^\s:,"\']+\.\w+)', _src):
                            _expected_files.add(_m)
                    # Verify each file actually exists
                    from pathlib import Path as _VrfPath
                    _missing = [f for f in _expected_files if not _VrfPath(f).exists()]
                    # Also check default TTS path if step was about TTS
                    if "tts" in _step_goal.lower() or "voice" in _step_goal.lower() or "audio" in _step_goal.lower():
                        _default_tts = _VrfPath("/home/baw/.baw/media/tts")
                        if _default_tts.exists():
                            _tts_files = list(_default_tts.glob("*.mp3"))
                            if not _tts_files:
                                _missing.append("No mp3 in /home/baw/.baw/media/tts/")
                    if _missing:
                        # Files claimed to exist but don't — fail loudly
                        _delegation_results[_step_idx] = (
                            f"[FAILED-VERIFICATION] {_step_desc_short}: "
                            f"missing files: {_missing[:3]}"
                        )
                        _synthesis_results.append(_delegation_results[_step_idx])
                        if verbose:
                            print(
                                f"  ❌ Step {_g} {_si}/{_gt} claimed done but files missing: "
                                f"{_missing[:3]}"
                            )
                        # Trigger route recalc — orchestrator will pick up or retry
                        _recalc_count += 1
                        if _recalc_count > _MAX_RECALCULATES:
                            _pursuit_failed = True
                        continue

                # Position-based: if position fails 3+ times → skip ANYTHING here + ban across pursuits
                if _position_fails.get(_step_idx, 0) >= 3:
                    _permanent_skip.add(_step_idx)  # ban this position for all future pursuits
                    if verbose:
                        print(f"  ⏭️ Step {_g} {_si}/{_gt} position stuck after {_position_fails[_step_idx]} attempts — skipping permanently")
                    _synthesis_results.append(f"[SKIPPED-POS] {_step_desc_short}")
                    _dsp = f"  ⏭️ Step {_g} {_si}/{_gt}: {_step_desc_short}"
                    _display_log.append(_dsp)
                    _step_idx += 1
                    _recalc_count = 0
                    continue

                if _same_step_fails[_same_key] >= 2:
                    if verbose:
                        print(f"  ⏭️ Skipping stuck step: {_step_desc_short} ({_same_step_fails[_same_key]} failures)")
                    _synthesis_results.append(f"[SKIPPED] {_step_desc_short}")
                    _dsp = f"  ⏭️ Step {_g} {_si}/{_gt}: {_step_desc_short}"
                    _display_log.append(_dsp)
                    _step_idx += 1  # move on
                    _recalc_count = 0
                    continue  # next step

                if verbose:
                    print(f"  🚫 Step {_g} {_si}/{_gt} failed: {str(_e)[:100]}")
                    print(f"     ↻ Route recalculation...")

                # ── Route recalculation: recalculate remaining route from here ──
                _recalc_count += 1
                if _recalc_count > _MAX_RECALCULATES:
                    _pursuit_failed = True
                    break

                if progress_callback:
                    progress_callback("recalc", "", {"step": _step_idx + 1, "count": _recalc_count, "error": str(_e)[:60]})

                _done_summary = "\n".join(
                    f"Step {i+1}: {r[:200]}" for i, r in enumerate(_synthesis_results)
                ) if _synthesis_results else "Nothing completed yet."

                _replan_prompt = (
                    f"[GOOGLE MAPS RECALCULATE #{_recalc_count}]\n"
                    f"Original destination: {prompt}\n\n"
                    f"Journey so far:\n{_done_summary}\n\n"
                    f"Just took a wrong turn at: {_step['desc']}\n"
                    f"Error: {str(_e)[:300]}\n\n"
                    f"Recalculate a NEW route from HERE to reach the destination.\n"
                    f"List ONLY the remaining steps needed (numbered from 1).\n"
                    f"Do NOT re-list already completed steps.\n"
                    f"Each step must INCLUDE all 3 phases: read → modify → verify.\n"
                    f"NEVER write a step that only checks or reads without taking action.\n"
                    f"  Step 1: <read> → <modify> → <verify>\n"
                )
                _replan_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": _replan_prompt}]
                _replan_fb = call_llm_with_fallback(config, _replan_msgs, temperature=None)
                _replan_resp = _replan_fb.response
                total_llm_calls += 1
                _cost_r = calculate_cost(model, _replan_resp.input_tokens, _replan_resp.output_tokens)
                session_cost += _cost_r
                record_cost(f"{model.provider}/{model.id}", _replan_resp.input_tokens, _replan_resp.output_tokens, _cost_r)

                _new_steps = []
                _rc_group = chr(ord('A') + len(_group_step_count))  # next unused group letter
                _rc_step = 0
                for _line in (_replan_resp.content or "").split("\n"):
                    _line = _line.strip()
                    # Group header
                    _gm2 = _re.match(r'##\s*Group\s+([A-Z])\s*(?:[—\-]\s*(.*?))?(?:\s*\(\d+\s*steps?\))?\s*$', _line)
                    if _gm2:
                        _rc_group = _gm2.group(1)
                        _rc_step = 0
                        continue
                    # Grouped step
                    _sm2 = _re.match(r'(?:Step\s*)?([A-Z])-(\d+)[.:]\s*(.*)', _line)
                    if _sm2:
                        _rc_group = _sm2.group(1)
                        _rc_step += 1
                        _new_steps.append({"group": _rc_group, "step": int(_sm2.group(2)),
                                           "desc": _sm2.group(3).strip(), "group_name": "",
                                           "step_in_group": _rc_step})
                        continue
                    # Flat fallback
                    _fm2 = _re.match(r'(?:Step\s*)?(\d+)[.:]\s*(.*)', _line)
                    if _fm2:
                        _rc_step += 1
                        _new_steps.append({"group": _rc_group, "step": int(_fm2.group(1)),
                                           "desc": _fm2.group(2).strip(), "group_name": "",
                                           "step_in_group": _rc_step})
                # Post-process recalc steps: compute group_totals
                _rc_totals: dict[str, int] = {}
                for _ns in _new_steps:
                    _rc_totals[_ns["group"]] = _rc_totals.get(_ns["group"], 0) + 1
                for _ns in _new_steps:
                    _ns["group_total"] = _rc_totals.get(_ns["group"], 1)
                    _ns["global_idx"] = _step_idx + len(_new_steps)  # placeholder

                if _new_steps:
                    # Replace remaining plan with new route (keep completed steps)
                    _execution_plan = _execution_plan[:_step_idx] + _new_steps
                    # NOTE: _recalc_count keeps accumulating — only resets on step success
                    if verbose:
                        print(f"     ✅ New route: {len(_new_steps)} remaining steps")
                    # Don't increment _step_idx — retry this position with new first step
                else:
                    # Can't recalculate — entire pursuit iteration fails
                    _pursuit_failed = True
                    _dsp = dsp.phase_step_error(_g, _si, _gt, _step_desc_short, str(_e)[:80])
                    _display_log.append(_dsp)
                    if verbose:
                        print(f"     ❌ Can't recalculate route — re-planning from scratch")
                    break

        # ── Self-review ──
        if not _pursuit_failed and steps_completed > 0:
            _review_prompt = (
                f"[SELF-REVIEW] Goal: {prompt}\n\n"
                f"All steps completed.\n\n"
                f"Results:\n"
                + "\n".join(f"Step {i+1}:\n{r[:500]}" for i, r in enumerate(_delegation_results))
                + f"\n\n---\n"
                f"Given the goal, are the results sufficient? Answer with:\n"
                f"SCORE: <0-10> (7+ = goal achieved)\n"
                f"REASON: <brief explanation>"
            )
            _review_msgs = [{"role": "system", "content": ctx.system_prompt}, {"role": "user", "content": _review_prompt}]
            _review_fb = call_llm_with_fallback(config, _review_msgs, temperature=0.3)
            _review_resp = _review_fb.response
            total_llm_calls += 1
            _cost3 = calculate_cost(model, _review_resp.input_tokens, _review_resp.output_tokens)
            session_cost += _cost3
            record_cost(f"{model.provider}/{model.id}", _review_resp.input_tokens, _review_resp.output_tokens, _cost3)
            _review_text = _review_resp.content or ""

            _score_match = re.search(r'SCORE:\s*(\d+(?:\.\d+)?)', _review_text)
            _score = float(_score_match.group(1)) if _score_match else 0

            if verbose:
                print(f"\\n  🔍 Self-review: {steps_completed} steps done, score {_score}/10")

            # If ALL steps completed without failure → auto-achieved, skip fragile self-review gate
            if steps_completed == len(_execution_plan) and not _pursuit_failed:
                _goal_achieved = True
                if verbose:
                    print(f"     ✅ All {steps_completed} steps done — auto-confirm goal achieved")
                break

            if _score >= 7:
                _goal_achieved = True
                break
            else:
                if verbose:
                    print(f"     → Score too low, re-planning ({_pursuit}/{_GOAL_PURSUIT_MAX_ATTEMPTS})")
                # Fall through to outer loop re-plan
        else:
            if _pursuit >= _GOAL_PURSUIT_MAX_ATTEMPTS:
                if verbose:
                    print(f"  ❌ All {_GOAL_PURSUIT_MAX_ATTEMPTS} pursuit iterations exhausted")

    # ── After goal loop ──
    if _goal_achieved:
        if verbose:
            print("  ✅ Goal achieved — synthesising final response")
    else:
        if verbose:
            print(f"  ⚠️ Goal not fully achieved after {_GOAL_PURSUIT_MAX_ATTEMPTS} pursuit attempts — synthesising partial results")

    # ── Collect failure reasons for round-level diagnosis ──
    _failure_reasons = []
    if not _goal_achieved:
        for _fr_i, _fr_r in enumerate(_delegation_results):
            _fr_stripped = _fr_r.strip()
            if _fr_stripped.startswith("[FAILED]") or _fr_stripped.startswith("[SKIPPED") or "error" in _fr_stripped.lower()[:80]:
                _failure_reasons.append(_fr_stripped[:300])
        # Also extract timeout/permission/API errors from any delegation result
        for _fr_r in _delegation_results:
            _fr_lower = _fr_r.lower()
            for _fr_kw in ("timeout", "permission", "denied", "quota", "401", "402", "403",
                          "404", "429", "500", "502", "503", "unreachable", "not installed",
                          "import error", "module not found", "no module", "pip install"):
                if _fr_kw in _fr_lower:
                    _failure_reasons.append(_fr_r[:300])
                    break

    # ── Phase 3c: Synthesise delegation results into a conclusion ──
    if _delegation_results:
        _step_count = len(_delegation_results)
        _multi_source = _step_count > 1
        _synthesis_prompt = (
            f"[ORCHESTRATOR] Goal: {prompt[:200]}\n\n"
            f"All {_step_count} steps completed. Below are their results:\n\n"
            + "\n---\n".join(f"Step {i+1}:\n{r[:2000]}" for i, r in enumerate(_delegation_results))
            + "\n\n---\n\n"
            "SYNTHESISE the results into a CONCISE CONCLUSION:\n"
            "1. Key findings from each step (1 sentence each)\n"
            + (f"2. CROSS-REFERENCE: Do the results confirm or contradict each other? Are they about the same thing?\n" if _multi_source else "")
            + "3. FINAL CONCLUSION: Answer the user's goal directly. Be brief.\n"
            "4. If relevant: suggest concrete next actions or alternatives.\n\n"
            "CRITICAL RULES:\n"
            "- This is a CONCLUSION, not a verification. Don't say 'goal achieved' or 'score'. Just deliver the answer.\n"
            "- If results are thin or incomplete, say what's known honestly — don't fabricate.\n"
            "- ⚠️ UNCERTAINTY FLAG: If any result contains error patterns, missing data, or unverifiable claims, flag them with ⚠️ in the response. Do NOT pretend uncertain results are certain.\n"
            "- 🚨 ZERO-EXECUTION FLAG: If any step result contains '[FAILED-NO-EXEC]' or '[SKIPPED]', the LLM did NOT actually execute the step — it just wrote a plan/summary. You MUST tell the user 'I did not actually run this — it was a plan, not an action'. Do NOT claim files were sent or actions were taken.\n"
            "- NEVER end with a question. NEVER ask for permission. NEVER promise future action.\n"
            "- Output format: no markdown headers, just plain paragraphs. Lead with the answer.\n"
            "- PRESERVE any MEDIA: or MEDIA:/path lines from sub-agent results verbatim — do not strip or summarise them.\n"
            "- BE VERY CONCISE — user complained responses are too long. Just state what was done + the key result. 3 sentences max. No step-by-step. No Plan output."
        )
        ctx.add_user(_synthesis_prompt)

        fb = call_llm_with_fallback(
            config, ctx.to_openai_messages(),
            temperature=model_temperature,
        )
        response = fb.response
        total_llm_calls += 1
        cost = calculate_cost(model, response.input_tokens, response.output_tokens)
        session_cost += cost
        record_cost(f"{model.provider}/{model.id}", response.input_tokens, response.output_tokens, cost)
        ctx.add_assistant(response.content, response.tool_calls,
                          getattr(response, 'reasoning_content', None))

        if verbose:
            print(f"\n[LLM #{total_llm_calls}] {fb.model_used} | Synthesis complete")

        # ── Post-synthesis: quick URL liveness check ──
        _url_sources = []
        try:
            import re as _re, httpx as _hx
            for _r in _delegation_results:
                _urls = _re.findall(r'https?://[^\s\n\)]+', _r)
                _url_sources.extend(_urls[:2])  # max 2 per result
            if _url_sources:
                _test_url = _url_sources[0]
                _resp = _hx.head(_test_url, timeout=5, follow_redirects=True)
                if _resp.status_code >= 400:
                    _dead_note = f"\n⚠️ Source URL may be dead: {_test_url} (HTTP {_resp.status_code})"
                    if response.content:
                        response.content += _dead_note
        except Exception:
            pass  # non-critical

        # ── Post-synthesis: MEDIA path resolution ──
        # Sub-agent plans may use wrong output paths (e.g. /tmp/baw_test/ instead of
        # the actual /tmp/baw_tts_*.mp3). Extract real file paths from delegation results.
        if response and response.content:
            try:
                import re as _media_re
                from pathlib import Path as _MediaPath
                # Find all MEDIA: paths in the response
                _media_paths = _media_re.findall(r'MEDIA:([^\n]+\.mp3)', response.content)
                if _media_paths:
                    # Collect real file paths from delegation results (OK /path/file.mp3)
                    _real_files = []
                    for _r in _delegation_results:
                        _ok_matches = _media_re.findall(r'\bOK\s+(/[^\n]+\.mp3)', _r)
                        _real_files.extend(_ok_matches)
                    # For each MEDIA path that doesn't exist, try to resolve
                    for _mp in _media_paths:
                        _mp_clean = _mp.strip()
                        if _MediaPath(_mp_clean).exists():
                            continue
                        # Find a real file with matching voice name in delegation results
                        _voice_match = _media_re.search(r'([A-Za-z_]+)\.mp3', _mp_clean)
                        _voice_name = _voice_match.group(1) if _voice_match else ""
                        _replacement = ""
                        if _voice_name and _real_files:
                            for _rf in _real_files:
                                if _voice_name in _rf:
                                    _replacement = _rf
                                    break
                        if not _replacement and _real_files:
                            _replacement = _real_files[0]
                        if _replacement:
                            response.content = response.content.replace(
                                f"MEDIA:{_mp_clean}", f"MEDIA:{_replacement}"
                            )
                        else:
                            _warn = f"\n⚠️ MEDIA file not found: {_mp_clean}"
                            if _real_files:
                                _warn += f"\n   Try: MEDIA:{_real_files[0]}"
                            response.content += _warn
            except Exception:
                pass  # non-critical
    else:
        # No delegation results — fall back to neutral response
        response = neutral_response
        _delegation_failed = True

    # ── Collect final output ──
    assistant_responses = []
    for msg in ctx.messages:
        if hasattr(msg, 'role'):
            role = msg.role
            content = msg.content or ""
        else:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
        if role == "assistant" and content:
            assistant_responses.append(content)

    if assistant_responses:
        final_reply = assistant_responses[-1]
    elif _delegation_results:
        # Synthesis response was empty — auto-generate summary from delegation results
        final_reply = "✅ Completed " + ", ".join(
            r.strip()[:200] for r in _delegation_results[-3:]
        )
    else:
        final_reply = ""

    # ── Build plan recap (route plan + step progress) → Message 1 ──
    plan_recap = ""
    if _display_log:
        plan_recap += "\n".join(_display_log) + "\n"
    if steps_completed > 0:
        plan_recap += dsp.done(steps_completed, len(_execution_plan), 0, 0) + "\n"

    # ── Build findings (actual results + options) → Message 2 ──
    findings = final_reply or ""

    # ── Fact check findings ──
    if findings:
        try:
            from .fact_checker import FactChecker
            fc = FactChecker(config)
            last_text = ""
            if ctx.messages:
                last_msg = ctx.messages[-1]
                if hasattr(last_msg, "content"):
                    last_text = last_msg.content or ""
                else:
                    last_text = last_msg.get("content", "") or ""
            action, fc_result = fc.check(last_text or "", prompt)
            if action == "block":
                findings += f"\n\n{fc_result['message']}"
            elif action == "flag":
                findings += f"\n\n<i>⚠️ {len(fc_result['claims'])} unverified claims flagged</i>"
                try:
                    search_action, search_result = fc.verify_with_search(last_text or "", prompt)
                    if search_action == "block":
                        msg = search_result.get("message", "blocked by web search")
                        findings += f"\n\n{html.italic('Web search: ' + msg)}"
                    elif search_action == "flag" and search_result.get("flagged"):
                        n_flagged = len(search_result["flagged"])
                        findings += f"\n\n{html.italic(f'Web search: {n_flagged} claims unverifiable')}"
                except Exception:
                    pass
        except Exception:
            pass

        findings += f"\n\n{html_cost_summary()}"
        # Add context window bar
        ctx_bar = context_window_summary()
        if ctx_bar:
            findings += f"\n{ctx_bar}"

    # Auto-save
    last_commit = None
    try:
        last_commit = auto_commit(
            Path.home() / "baw",
            f"agent: {prompt[:80]}"
        )
    except Exception:
        pass

    if last_commit:
        findings += f"\n💾 <i>Auto-saved: {last_commit}</i>"

    try:
        mem.remember(f"User: {prompt[:150]} → BAW: {(final_reply or '')[:150]}")
    except Exception:
        pass

    info = {
        "cost": round(session_cost, 4),
        "model": f"{model.provider}/{model.id}",
        "iterations": total_llm_calls,
        "steps": steps_completed,
        "adversarial": court_result["agreement_level"] if court_result else None,
        "adversarial_raw": court_result,
        "new_session_messages": _extract_new_msgs(ctx, _pre_prompt_count),
        "plan_recap": plan_recap.strip(),
        "goal_achieved": _goal_achieved or (steps_completed > 0 and steps_completed == len(_execution_plan)),
        "failure_reasons": _failure_reasons[:5] if _failure_reasons else [],
        "uncertain_claims": _uncertain_claims[:5] if _uncertain_claims else [],
        "successful_results": _synthesis_results[:5] if _synthesis_results else [],
    }
    return findings, info
