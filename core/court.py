"""BAW Court — Black & White Court state machine (M1 milestone, Fable 5 spec).

The court is the user-facing metaphor for what the agent actually does:

  User message → Court.file_case()
                    │
                    ▼
  ┌──────────────────────────────────────────────────┐
  │  FILED → TRIAGE → (FAST_LANE | INDICTMENT → ... │
  │          HEARING → EXECUTION → REVIEW → VERDICT → CLOSED)
  └──────────────────────────────────────────────────┘

Roles:
  🖤  Prosecutor (Devil)      — critiques plan before execution (tier ≥ 2)
  🤍  Defendant (Executor)     — does the work via delegate_task
  👨‍⚖️  Judge (Verifier)        — scores each step 0-10, issues verdict
  📎  Evidence (Checkpoints)  — records every tool call as a trace

The 4 tiers:
  0 (trivial)   FAST_LANE only — no court, just execute
  1 (moderate)  Judge only — verify after execution
  2 (complex)   Judge + Prosecutor + Defendant — critique → execute → judge
  3 (expert)    All four roles + multi-step execution with batched verification

The court is the single entry point for the agent loop. loop.py should
call Court.file_case() instead of running the old inline path. Other
entry points (Telegram bot, CLI chat) also call into this module.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .config import load_config

import asyncio as _asyncio

logger = logging.getLogger(__name__)

# ── Sync wrapper + docket blocking ──────────────────────────────
# M5-D6: file_case() is async (uses delegate_task which may call asyncio
# internally). The agent loop (run_agent) is sync, so we expose
# file_case_sync() that wraps asyncio.run. This wrapper also handles
# the docket blocking case: if file_case_sync is called for a tier-2/3
# case and the per-user/system docket is full, it polls the docket
# until a slot opens up, then runs the case. Tier-0 always runs
# immediately (TIER0_NEVER_QUEUED in docket.py).


# ── Court tiers ─────────────────────────────────────────────────────

class CourtTier(Enum):
    TIER_0_FAST_LANE = 0  # direct execution, no court
    TIER_1_MINOR = 1       # judge only
    TIER_2_MAJOR = 2       # judge + prosecutor + defendant
    TIER_3_SUPREME = 3     # all roles, batched verification


# ── Court states (state machine) ────────────────────────────────────

class CourtState(str, Enum):
    FILED = "filed"             # case created, ID assigned
    TRIAGE = "triage"           # routing decision pending
    FAST_LANE = "fast_lane"     # tier 0: skip court
    INDICTMENT = "indictment"   # prosecutor reviewing plan
    HEARING = "hearing"         # defendant responding to critique
    EXECUTION = "execution"     # defendant doing the work
    REVIEW = "review"           # judge scoring result
    VERDICT = "verdict"         # final ruling issued
    CLOSED = "closed"           # archived


# ── Verdict types ───────────────────────────────────────────────────

class Verdict(str, Enum):
    APPROVED = "approved"   # ✅ judge said OK
    RETRY = "retry"         # 🔁 judge said redo (auto, up to N times)
    APPEAL = "appeal"       # 📤 escalate to higher tier model (up to 1 time)
    DISMISSED = "dismissed" # 🚫 unrecoverable, return error to user
    STAY = "stay"           # ⏸️ need user decision (inline keyboard)


# ── Verdict templates (Fable 5 spec, 5 templates) ──────────────────

VERDICT_TEMPLATES = {
    Verdict.APPROVED: (
        "#{case_id} │ 核准 ({score}/10)\n"
        "{summary}\n"
        "📎 證物 {evidence_count} 件 · {elapsed:.1f}s · /court {case_id} 查全卷"
    ),
    Verdict.RETRY: (
        "🔁 #{case_id} │ 第 {step} 步未達標 ({score}/10)\n"
        "{reason}\n"
        "換策略重試 ({attempt}/{max_attempts})…"
    ),
    Verdict.APPEAL: (
        "📤 #{case_id} │ 上訴受理\n"
        "原審 {original_model} 兩次未達標,\n"
        "移交上級法院 {appeal_model} 重審…"
    ),
    Verdict.DISMISSED: (
        "🚫 #{case_id} │ 駁回\n"
        "原因:{reason}\n"
        "已做:{done}\n"
        "建議:{suggestion}"
    ),
    Verdict.STAY: (
        "⏸️ #{case_id} │ 中止 — 需要你裁示\n"
        "{reason}\n"
        "[ 批准執行 ] [ 先 backup 再做 ] [ 撤案 ]"
    ),
}

# ── STAY inline keyboard (M5-D8) ──────────────────────────────────
# When a case ends with Verdict.STAY, the agent (or Telegram connector)
# renders an inline keyboard with three actions:
#   approve  → resume execution
#   backup   → snapshot first, then resume
#   dismiss  → drop the case
# Callback data is "court:{case_id}:{action}" so the bot can route it.

STAY_INLINE_KEYBOARD = {
    "approve":  ("批准執行",      "court:{case_id}:approve"),
    "backup":   ("先 backup 再做", "court:{case_id}:backup"),
    "dismiss":  ("撤案",          "court:{case_id}:dismiss"),
}


def build_stay_inline_keyboard(case_id: str) -> list[list[dict]]:
    """Build a Telegram-compatible inline_keyboard for a STAY verdict.

    Returns a list of rows; each row is a list of button dicts with
    "text" and "callback_data". Three buttons laid out in one row.

    For non-Telegram connectors (CLI, dashboard), the same shape is
    trivially adapted. The downstream handler reads the callback_data
    suffix after "court:{case_id}:" to decide what to do.
    """
    return [[{
        "text": text,
        "callback_data": cb.format(case_id=case_id),
    } for (text, cb) in STAY_INLINE_KEYBOARD.values()]]

# Emoji glossary (Fable 5 spec, single source of truth)
COURT_EMOJI = {
    "case_id": "",
    "prosecutor": "",
    "defendant": "",
    "judge": "",
    "evidence": "📎",
    "step_done": "",
    "step_running": "🔧",
    "step_pending": "⬜",
    "step_failed": "❌",
    "verdict_retry": "🔁",
    "verdict_appeal": "📤",
    "verdict_dismissed": "🚫",
    "verdict_stay": "⏸️",
}


# ── Court case context (one case = one of these) ──────────────────

@dataclass
class CourtCase:
    """All state for a single court case. Persists across the state machine."""
    case_id: str
    goal: str
    user_id: str = "default"
    created_at: float = field(default_factory=time.time)

    # Routing
    tier: CourtTier = CourtTier.TIER_1_MINOR
    state: CourtState = CourtState.FILED

    # Model selection
    defendant_model: str = ""
    judge_model: str = ""
    prosecutor_model: str = ""

    # Verdict
    verdict: Optional[Verdict] = None
    score: int = 0
    reason: str = ""
    retry_count: int = 0
    appeal_count: int = 0
    max_retries: int = 2
    max_appeals: int = 1

    # Evidence trail
    evidence: list[dict] = field(default_factory=list)
    elapsed_sec: float = 0.0

    # User-facing summary (for /court <id> full record)
    final_summary: str = ""

    def add_evidence(self, role: str, content: str) -> None:
        """Record a piece of evidence (prosecutor critique, defendant output, etc.)."""
        self.evidence.append({
            "role": role,
            "content": content[:2000],  # cap to keep case files small
            "ts": time.time(),
        })

    def transition(self, new_state: CourtState) -> None:
        """Move to a new state. Logs the transition for the audit trail."""
        logger.info(f"[court {self.case_id}] {self.state.value} → {new_state.value}")
        self.state = new_state


# ── Tier → model resolution ───────────────────────────────────────

def _resolve_models_for_tier(config: dict, tier: CourtTier, caller_model_id: str = "") -> dict:
    """Pick defendant/judge/prosecutor models for a tier.

    Respects caller_model_id if non-empty (P0-1). Otherwise falls back
    to router.DEFAULT_TIER_PREFERENCES (P0-3) and config-driven roles.
    """
    from .router import DEFAULT_TIER_PREFERENCES, TIER_TRIVIAL, TIER_MODERATE, TIER_COMPLEX, TIER_EXPERT

    tier_to_router_name = {
        CourtTier.TIER_0_FAST_LANE: TIER_TRIVIAL,
        CourtTier.TIER_1_MINOR: TIER_MODERATE,
        CourtTier.TIER_2_MAJOR: TIER_COMPLEX,
        CourtTier.TIER_3_SUPREME: TIER_EXPERT,
    }
    router_name = tier_to_router_name[tier]

    # Defendant: caller override > tier preference
    if caller_model_id:
        defendant = caller_model_id
    else:
        prefs = config.get("router", {}).get("tier_preferences", {}).get(router_name) \
                or DEFAULT_TIER_PREFERENCES.get(router_name, [])
        # Pick first available (P0-3 fix: real model IDs)
        from .config import model_exists
        defendant = next((m for m in prefs if model_exists(config, m)), prefs[0] if prefs else "deepseek-v4-flash")

    # Judge: config-driven (default = small/fast model for scoring)
    judge = config.get("court", {}).get("judge_model", defendant)

    # Prosecutor (Devil): config-driven (default = capability model)
    prosecutor = config.get("adversarial", {}).get("devil_model", defendant)

    return {
        "defendant": defendant,
        "judge": judge,
        "prosecutor": prosecutor,
    }


# ── State machine: tier 0 (fast lane) ─────────────────────────────

async def _run_fast_lane(case: CourtCase) -> CourtCase:
    """Tier 0: no court, just execute inline.

    The whole point of tier 0 is to be invisible to the user. We skip
    prosecution, skip judge review, just hand the goal to the defendant's
    model and return whatever it says.
    """
    case.transition(CourtState.FAST_LANE)
    # Inline LLM call (no delegate_task overhead)
    from .llm import call_llm_with_fallback
    config = load_config()
    messages = [{"role": "user", "content": case.goal}]
    fb = call_llm_with_fallback(config, messages, tools=[], temperature=0.5)
    response = fb.response.content or ""
    case.final_summary = response[:500]
    case.add_evidence("DEFENDANT_FAST", response)
    case.score = 10
    case.verdict = Verdict.APPROVED
    case.transition(CourtState.VERDICT)
    case.transition(CourtState.CLOSED)
    case.elapsed_sec = time.time() - case.created_at
    return case


# ── State machine: tier 1 (judge only) ────────────────────────────

async def _run_minor_court(case: CourtCase) -> CourtCase:
    """Tier 1: defendant executes, judge scores."""
    from .llm import call_llm_with_fallback
    from .verifier import verify_step
    from tools.delegate_task import delegate_task

    case.transition(CourtState.EXECUTION)
    config = load_config()
    result = await _run_defendant(case, config)
    case.add_evidence("DEFENDANT", result)

    case.transition(CourtState.REVIEW)
    verdict = verify_step(
        goal=case.goal, tool_name="chat", tool_args={"prompt": case.goal},
        tool_result=result, config=config, model_id=case.judge_model,
    )
    case.score = int(verdict.get("score", 7))
    case.reason = verdict.get("reason", "")

    if case.score >= 7:
        case.verdict = Verdict.APPROVED
        case.final_summary = result
    elif case.retry_count < case.max_retries:
        case.verdict = Verdict.RETRY
        case.retry_count += 1
    else:
        case.verdict = Verdict.DISMISSED
        case.final_summary = result
        case.reason = case.reason or "judge repeatedly scored below 7/10"

    case.add_evidence("JUDGE", f"score={case.score} verdict={case.verdict.value} reason={case.reason}")
    case.transition(CourtState.VERDICT)
    case.transition(CourtState.CLOSED)
    case.elapsed_sec = time.time() - case.created_at
    return case


# ── State machine: tier 2 (judge + prosecutor + defendant) ───────

async def _run_major_court(case: CourtCase) -> CourtCase:
    """Tier 2: prosecutor critiques → defendant executes → judge reviews.

    M3-1: prosecutor and defendant's initial plan run in PARALLEL via
    asyncio.gather, halving tier-2 latency. They are independent (Devil
    critiques the goal; defendant drafts a plan from the goal) so the
    parallelization is safe. If either fails, the other result is still
    used; the loss is a less-informed defendant.
    """
    import asyncio as _asyncio
    from .llm import call_llm_with_fallback
    from .verifier import verify_step
    from tools.delegate_task import delegate_task

    case.transition(CourtState.INDICTMENT)
    config = load_config()

    # M3-7: verdict cache — if a very similar goal was already APPROVED with
    # score >= 8 recently, skip the prosecutor LLM call and inject the
    # previous critique as context. Jaccard on character bigrams is the
    # similarity function (no embedding model needed, see verdict_cache.py).
    reusable = None
    try:
        from .verdict_cache import find_reusable_verdict
        reusable = find_reusable_verdict(case.goal, tier=2)
    except Exception:
        reusable = None

    if reusable:
        # Reuse the previous prosecutor's critique as a "precedent" hint.
        prev_critique = "(Reused from prior case — see evidence for original.)"
        for ev in reversed(reusable.get("evidence", [])):
            if ev.get("role") == "PROSECUTOR":
                prev_critique = ev.get("content", prev_critique)
                break
        case.add_evidence("PROSECUTOR", f"[cached, prior case {reusable.get('case_id')}] {prev_critique[:200]}")
        critique = prev_critique
        plan_text = ""  # cached path skips Angel plan-draft
    else:
        # M5-D9: real Angel plan-draft runs in parallel with the Devil
        # prosecutor (asyncio.gather). The plan becomes part of the
        # defendant's hearing context, so the defendant sees both:
        #   1. The Devil's critique (risks to mitigate)
        #   2. The Angel's plan (constructive path to follow)
        # The "consider X" hint is replaced by a concrete plan section.
        async def _devil() -> str:
            return await _run_prosecutor(case, config)

        async def _plan() -> str:
            return await _run_angel(case, config)

        critique, plan_text = await _asyncio.gather(_devil(), _plan())
        case.add_evidence("PROSECUTOR", critique)
        if plan_text:
            case.add_evidence("ANGEL", plan_text)

    # Phase 2: Hearing (defendant sees critique + plan)
    case.transition(CourtState.HEARING)
    _ctx_parts = [f"[Prosecutor's critique]\n{critique}"]
    if plan_text:
        _ctx_parts.append(f"[Angel's plan]\n{plan_text}")
    enhanced_context = "\n\n".join(_ctx_parts)

    # Phase 3: Execution
    case.transition(CourtState.EXECUTION)
    result = await _run_defendant(case, config, context=enhanced_context)
    case.add_evidence("DEFENDANT", result)

    # Phase 4: Review
    case.transition(CourtState.REVIEW)
    verdict = verify_step(
        goal=case.goal, tool_name="chat", tool_args={"prompt": case.goal},
        tool_result=result, config=config, model_id=case.judge_model,
    )
    case.score = int(verdict.get("score", 7))
    case.reason = verdict.get("reason", "")

    if case.score >= 7:
        case.verdict = Verdict.APPROVED
        case.final_summary = result
    elif case.retry_count < case.max_retries:
        case.verdict = Verdict.RETRY
        case.retry_count += 1
    else:
        case.verdict = Verdict.DISMISSED
        case.final_summary = result
        case.reason = case.reason or "judge repeatedly scored below 7/10 after prosecutor critique"

    case.add_evidence("JUDGE", f"score={case.score} verdict={case.verdict.value} reason={case.reason}")
    case.transition(CourtState.VERDICT)
    case.transition(CourtState.CLOSED)
    case.elapsed_sec = time.time() - case.created_at
    return case


# ── State machine: tier 3 (supreme court) ─────────────────────────

async def _run_supreme_court(case: CourtCase) -> CourtCase:
    """Tier 3: all roles + batched multi-step execution.

    For M1, this delegates to tier-2 logic with a multi-step loop. Future
    milestones (M3, M4) will add parallel sub-defendants and verdict caching.
    """
    # For now, treat as major court but with more retry budget.
    case.max_retries = 3
    return await _run_major_court(case)


# ── Helper: defendant execution ──────────────────────────────────

async def _run_defendant(case: CourtCase, config: dict, context: str = "") -> str:
    """Run the defendant (delegate_task or inline). Returns the result text."""
    # We use delegate_task if the case is complex enough to warrant it,
    # otherwise inline. For now always use delegate_task for tier >= 1
    # because it has the verify_step integration from P1-4.
    from tools.delegate_task import delegate_task
    full_context = f"[Case {case.case_id}]\n{context}" if context else f"[Case {case.case_id}]"
    return delegate_task(
        goal=case.goal,
        context=full_context,
        toolsets="",
        model_id=case.defendant_model,  # P0-1: pass router decision through
    )


# ── Helper: prosecutor (Devil) ───────────────────────────────────

async def _run_prosecutor(case: CourtCase, config: dict) -> str:
    """Run the prosecutor (Devil) to critique the plan. Returns critique text."""
    from .llm import call_llm_with_fallback
    # core.adversarial exposes Devil/Angel classes, not a flat call_devil()
    # function. Use the class API; fall back to a raw LLM call if the
    # import fails (e.g. during a partial install).
    try:
        from .adversarial import Devil  # type: ignore
        from .llm import get_model
        devil_model_def = get_model(config, case.prosecutor_model)
        devil = Devil(model=devil_model_def, angel_persona="(default)", config=config)
        out = devil.speak(case.goal)
        # Devil.speak returns a dict with 'text' and 'score'
        if isinstance(out, dict):
            return out.get("text") or out.get("response") or str(out)
        return str(out)
    except (ImportError, AttributeError, Exception) as _e:
        logger.debug(f"[court {case.case_id}] Devil class unavailable ({_e}); using fallback LLM")
        # Fallback: raw LLM call with prosecutor persona
        messages = [
            {"role": "system", "content": (
                "你係法庭嘅檢察官(Devil)。你要挑剔用戶嘅任務,搵出潛在風險、"
                "缺漏、邏輯矛盾。用繁體中文,3-5 點,每點 1 句。"
            )},
            {"role": "user", "content": f"挑剔呢個任務:\n{case.goal}"},
        ]
        fb = call_llm_with_fallback(config, messages, tools=[], temperature=1.0)
        return fb.response.content or "(no critique)"


# ── Helper: angel (M5-D9) ───────────────────────────────────────

async def _run_angel(case: CourtCase, config: dict) -> str:
    """Run the Angel voice to draft a constructive plan. Returns plan text.

    M5-D9: real Angel counterpart to the prosecutor. The Angel is
    constructive (devil is destructive). Uses AngelVoice from
    core.adversarial if available; falls back to a raw LLM call.
    """
    from .llm import call_llm_with_fallback
    try:
        from .adversarial import AngelVoice  # type: ignore
        from .llm import get_model
        # The Angel uses the same model as the judge (the case's "good
        # voice"). We could also pull a dedicated angel_model from
        # config; for now mirror the prosecutor pattern (use the
        # tier-resolved model).
        angel_model_def = get_model(config, case.prosecutor_model)
        angel = AngelVoice(
            model=angel_model_def, devil_persona="(default)", config=config,
        )
        out = angel.speak(case.goal)
        if isinstance(out, dict):
            return out.get("content") or out.get("text") or str(out)
        return str(out)
    except (ImportError, AttributeError, Exception) as _e:
        logger.debug(
            f"[court {case.case_id}] AngelVoice unavailable ({_e}); "
            "using fallback LLM"
        )
        messages = [
            {"role": "system", "content": (
                "你係法庭嘅守護天使(Angel)。你要為用戶嘅任務設計一個清晰、"
                "循序漸進嘅執行計劃。用繁體中文,3-5 個步驟,每步 1 句。"
            )},
            {"role": "user", "content": f"為呢個任務設計執行計劃:\n{case.goal}"},
        ]
        fb = call_llm_with_fallback(config, messages, tools=[], temperature=0.7)
        return fb.response.content or ""


# ── Public entry: file_case() ─────────────────────────────────────

async def file_case(
    goal: str,
    user_id: str = "default",
    caller_model_id: str = "",
    force_tier: Optional[CourtTier] = None,
) -> CourtCase:
    """File a new court case. Returns a CourtCase with verdict populated.

    The case is the unit of work visible to the user via /court. All UI
    updates (Telegram messages, dashboard) should be derived from
    CourtCase.state and CourtCase.verdict.
    """
    from .router import route_task, score_complexity, tier_of

    # ── Triage: score → tier ──
    case = CourtCase(
        case_id=f"C{uuid.uuid4().hex[:6].upper()}",
        goal=goal,
        user_id=user_id,
    )
    case.transition(CourtState.TRIAGE)

    if force_tier is not None:
        case.tier = force_tier
    else:
        score = score_complexity(goal)
        tier_name = tier_of(score)
        tier_map = {
            "trivial": CourtTier.TIER_0_FAST_LANE,
            "moderate": CourtTier.TIER_1_MINOR,
            "complex": CourtTier.TIER_2_MAJOR,
            "expert": CourtTier.TIER_3_SUPREME,
        }
        case.tier = tier_map.get(tier_name, CourtTier.TIER_1_MINOR)

    # ── Resolve models ──
    config = load_config()
    models = _resolve_models_for_tier(config, case.tier, caller_model_id)
    case.defendant_model = models["defendant"]
    case.judge_model = models["judge"]
    case.prosecutor_model = models["prosecutor"]
    case.add_evidence("TRIAGE", (
        f"tier={case.tier.value} defendant={case.defendant_model} "
        f"judge={case.judge_model} prosecutor={case.prosecutor_model}"
    ))

    # M5-D6: docket blocking — if the docket is full, wait for a slot
    # (poll every 2s, max 5 minutes). Tier-0 always short-circuits.
    _queue_id = None
    try:
        from .docket import enqueue, mark_running, get_queue_position, get_status
        from .docket import Priority as _Priority
        _priority = _Priority.CRON if force_tier is not None else _Priority.USER_INTERACTIVE
        entry = enqueue(case.case_id, user_id, case.tier.value, case.goal, _priority)
        _queue_id = entry.queue_id
        pos = get_queue_position(case.case_id)
        case.add_evidence("DOCKET", (
            f"queue_id={entry.queue_id} priority={entry.priority.value} "
            f"position={pos if pos is not None else 'running'}"
        ))

        # If the case was queued (not auto-running), poll until it's our
        # turn. Bail out after 5 min to avoid hanging the agent loop.
        if pos is not None and pos > 1:
            _poll_deadline = time.time() + 300
            while pos is not None and pos > 1 and time.time() < _poll_deadline:
                _st = get_status()
                _running_for_user = _st.get("users_currently_running", [])
                if user_id in _running_for_user:
                    break  # slot opened up for us
                await _asyncio.sleep(2.0)
                pos = get_queue_position(case.case_id)
            if pos is not None and pos > 1:
                logger.warning(
                    f"[court {case.case_id}] docket wait timed out "
                    f"(position={pos}); running anyway"
                )
        mark_running(_queue_id)
    except Exception as _de:
        logger.debug(f"[court {case.case_id}] docket enqueue failed ({_de}); proceeding inline")
        _queue_id = None

    # ── Run the appropriate court ──
    if case.tier == CourtTier.TIER_0_FAST_LANE:
        case = await _run_fast_lane(case)
    elif case.tier == CourtTier.TIER_1_MINOR:
        case = await _run_minor_court(case)
    elif case.tier == CourtTier.TIER_2_MAJOR:
        case = await _run_major_court(case)
    else:  # TIER_3_SUPREME
        case = await _run_supreme_court(case)

    # ── Archive ──
    _archive_case(case)

    # M5-D6: clean docket bookkeeping using the public mark_done() entry.
    if _queue_id is not None:
        try:
            from .docket import mark_done
            _v = case.verdict
            _success = bool(_v is not None and _v.value == "approved")
            mark_done(_queue_id, success=_success)
        except Exception as _de:
            logger.debug(f"[court {case.case_id}] docket mark_done failed: {_de}")

    return case


# ── Sync wrapper (M5-D6) ─────────────────────────────────────────

def file_case_sync(
    goal: str,
    user_id: str = "default",
    caller_model_id: str = "",
    force_tier: Optional[CourtTier] = None,
) -> CourtCase:
    """Synchronous entry point. Wraps file_case() in asyncio.run().

    Use this from sync call sites (run_agent, CLI chat). The async
    file_case() is still available for callers that already have an
    event loop (e.g. inside a coroutine).
    """
    try:
        _loop = _asyncio.get_event_loop()
        if _loop.is_running():
            # We're already inside an event loop (e.g. test). Fall back
            # to a thread-pool run so we don't block the existing loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
                return _ex.submit(
                    _asyncio.run,
                    file_case(
                        goal=goal, user_id=user_id,
                        caller_model_id=caller_model_id, force_tier=force_tier,
                    ),
                ).result()
    except RuntimeError:
        pass
    return _asyncio.run(
        file_case(
            goal=goal, user_id=user_id,
            caller_model_id=caller_model_id, force_tier=force_tier,
        )
    )


# ── Case archiving ───────────────────────────────────────────────

def _archive_case(case: CourtCase) -> None:
    """Persist the case to ~/.baw/court/cases/{id}.json for /court <id> lookup."""
    try:
        archive_dir = Path.home() / ".baw" / "court" / "cases"
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / f"{case.case_id}.json"
        path.write_text(json.dumps({
            "case_id": case.case_id,
            "goal": case.goal,
            "user_id": case.user_id,
            "created_at": case.created_at,
            "elapsed_sec": case.elapsed_sec,
            "tier": case.tier.value,
            "verdict": case.verdict.value if case.verdict else None,
            "score": case.score,
            "reason": case.reason,
            "retry_count": case.retry_count,
            "appeal_count": case.appeal_count,
            "defendant_model": case.defendant_model,
            "judge_model": case.judge_model,
            "prosecutor_model": case.prosecutor_model,
            "final_summary": case.final_summary,
            "evidence": case.evidence,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[court {case.case_id}] archive failed: {e}")


# ── /court command helpers ───────────────────────────────────────

def recent_cases(limit: int = 5) -> list[dict]:
    """Return the most recent N cases from the archive."""
    archive_dir = Path.home() / ".baw" / "court" / "cases"
    if not archive_dir.exists():
        return []
    files = sorted(archive_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "case_id": data["case_id"],
                "goal": data["goal"][:60],
                "verdict": data.get("verdict"),
                "score": data.get("score"),
                "elapsed_sec": round(data.get("elapsed_sec", 0), 1),
                "tier": data.get("tier"),
            })
        except Exception:
            continue
    return out


def get_case(case_id: str) -> Optional[dict]:
    """Return full case record by ID, or None."""
    path = Path.home() / ".baw" / "court" / "cases" / f"{case_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_verdict(case: CourtCase) -> str:
    """Format a court case's verdict for display (Telegram / TUI / CLI).

    Uses the 5 templates from Fable 5 spec.
    """
    if not case.verdict:
        return f"#{case.case_id} │ 處理中…"

    template = VERDICT_TEMPLATES[case.verdict]
    return template.format(
        case_id=case.case_id,
        score=case.score,
        summary=case.final_summary[:200],
        evidence_count=len(case.evidence),
        elapsed=case.elapsed_sec,
        step="?",  # Could be more specific per retry attempt
        reason=case.reason[:200],
        attempt=case.retry_count,
        max_attempts=case.max_retries,
        original_model=case.defendant_model,
        appeal_model=case.judge_model,
        done="見 evidence",
        suggestion="再 file_case 試過",
    )
