# BAW 黑白法庭 (Black & White Court) 審計報告

> 審計日期: 2026-06-12  
> 審計範圍: Tier-Based Routing + Task Dispatch + Court Architecture

---

## 1. 當前路由架構 (Current Routing Flow)

### 1.1 完整工作流圖 (Text Diagram)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     INCOMING MESSAGE                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                  TIER DETECTION (core/router.py)                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ score_complexity(prompt) → 0-10                               │   │
│  │   • tool count needed ×1                                      │   │
│  │   • reasoning depth keywords ×2 (max +6)                    │   │
│  │   • context length ×1                                         │   │
│  │   • Cantonese-language depth ×1                              │   │
│  │   • multi-step / verification ×2 (max +6)                   │   │
│  │   • external side effects ×3 (max +4)                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│  tier_of(score):                                                     │
│    0-3 → TRIVIAL    (inline, no delegation)                        │
│    4-6 → MODERATE   (inline, multi-step)                          │
│    7-9 → COMPLEX   (delegate to sub-agent)                        │
│    10   → EXPERT    (multi-tier cascade)                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                MODEL SELECTION (core/router.py)                             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ pick_model_for_tier(tier, config)                              │   │
│  │   1. config['router']['tier_preferences'][tier] (user)      │   │
│  │   2. DEFAULT_TIER_PREFERENCES (hardcoded)                    │   │
│  │   3. First available chat model (last resort)               │   │
│  └────────���────────────────────────────────────────────────────┘   │
│  DEFAULT_TIER_PREFERENCES:                                          │
│    trivial:   [step-3.5-flash-2603, step-3.5-flash, MiniMax-M2.5] │
│    moderate:  [step-3.7-flash, step-3.5-flash-2603, MiniMax-M3]   │
│    complex:   [kimi-k2.6, step-3.7-flash, MiniMax-M3]            │
│    expert:   [kimi-k2.6, MiniMax-M3, step-3.7-flash]          │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                SUBAGENT DISPATCH (tools/delegate_task.py)                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ delegate_task(goal, context, toolsets)                      │   │
│  │   • _resolve_executor_model(cfg, goal)                    │   │
│  │     - Check model.task_rules (keyword match)            │   │
│  │     - Fall back to executor.model                      │   │
│  │   • _get_minimax_config(goal)                           │   │
│  │   • max_iterations = 12                                 │   │
│  │   • Isolation: no parent conversation history         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│  Execution:                                                       │
│    • Sub-agent runs with isolated Context                        │
│    • System prompt: "EXECUTOR. Do the task."                    │
│    • Must call tools before responding                          │
│    • Failure detection: 0 tool calls → RuntimeError            │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                VERIFICATION (core/verifier.py)                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ verify_step(goal, tool_name, tool_args, tool_result, config)  │   │
│  │   • LLM scores result 0-10                                  │   │
│  │   • threshold >= 7 = pass                                │   │
│  │   • On fail: return to loop for retry                        │   │
│  │   • Returns: score, passed, reason, actionable            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│  Post-execution:                                                  │
│    • Checkpoint system (core/checkpoint.py) saves state              │
│    • On failure: restore checkpoint, try different approach   │
└─────────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          RESPONSE                                        │
│    Format: "[Sub-agent MiniMax result]\n{content}\n_(iterations: N)_"      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 關鍵模塊互動

| 模塊 | 文件 | 功能 |
|------|------|------|
| `score_complexity()` | core/router.py:67-146 | 計算複雜度 0-10 |
| `tier_of()` | core/router.py:157-164 | 分數 → tier 映射 |
| `pick_model_for_tier()` | core/router.py:203-227 | tier → model 選擇 |
| `route_task()` | core/router.py:280-316 | 頂層路由决策 |
| `delegate_task()` | tools/delegate_task.py:116-246 | sub-agent 執行 |
| `verify_step()` | core/verifier.py:17-104 | 步驟驗證 |
| `Checkpoint` | core/checkpoint.py:15-24 | 狀態快照 |

---

## 2. 發現的弱點 (Gaps & Weaknesses)

### 2.1 路由/調度相關問題

| 嚴重性 | 問題 | 位置 | 說明 |
|--------|------|------|------|
| **CRITICAL** | No budget enforcement | core/llm.py | No budget limit check before calling LLM — user could exceed quota |
| **CRITICAL** | No parallel coordination | tools/delegate_task.py | No parallel task coordination — each delegate_task runs independently |
| **HIGH** | No automatic fallback | core/router.py | If selected model fails, no automatic fallback to other tier models |
| **HIGH** | No cost control | config.yaml | No cost limits per tier or per task |
| **HIGH** | Missing tier definitions | core/router.py:149-154 | Only 4 tiers defined — no "tier 0" for trivial commands, no "tier 4" for multi-agent |
| **MEDIUM** | No court/judge/prosecutor separation | core/verifier.py | Single verifier role — no judge, prosecutor, defendant roles |
| **MEDIUM** | No fallback model retry | tools/delegate_task.py:191-205 | If sub-agent fails, no retry with different model |
| **MEDIUM** | No parallel task queue | core/task_manager.py:175-211 | Tasks spawn immediately, no queue or coordination |

### 2.2 優先級缺口

```
❌ MISSING: Budget enforcement (CRITICAL)
❌ MISSING: Parallel task coordination (CRITICAL)  
❌ MISSING: Automatic model fallback (HIGH)
❌ MISSING: Cost limits per tier (HIGH)
❌ MISSING: Court architecture (MEDIUM)
❌ MISSING: Role separation (MEDIUM)
```

---

## 3. 黑白法庭架構提議 (Proposed Black & White Court Architecture)

### 3.1 概念映射

| 傳統法庭角色 | BAW Court 角色 | 實現文件 |
|--------------|----------------|----------|
| **法官 (Judge)** | Verifier | core/verifier.py |
| **檢察官 (Prosecutor)** | Red-team Critic | core/adversarial.py (Devil) |
| **被告 (Defendant)** | Executor Sub-agent | tools/delegate_task.py |
| **證據 (Evidence)** | Tool Call Traces | core/checkpoint.py |
| **書記 (Clerk)** | Checkpoint System | core/checkpoint.py |

### 3.2 法庭流程圖

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TIER 0-3 COURT SYSTEM                                    │
└─────────────────────────────────────────────────────────────────────────────┘

  TIER 0 (TRIVIAL: 0-3)
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  DIRECT EXECUTION — No Court Needed                                     │
  │  • Fast model (step-3.5-flash-2603)                                    │
  │  • Single tool call, no delegation                                      │
  │  • No verification — proceed directly                                │
  └─────────────────────────────────────────────────────────────────────────┘

  TIER 1 (MODERATE: 4-6)
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  SINGLE VERIFIER — Judge Only                                           │
  │                                                                         │
  │  [User Input] → Model (inline) → Tool Call → [Verifier: Judge]          │
  │                                            ↓                           │
  │                                    Score ≥ 7? → Response             │
  │                                            ↓                           │
  │                                    Score < 7 → Retry                 │
  └─────────────────────────────────────────────────────────────────────────┘

  TIER 2 (COMPLEX: 7-9)
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  JUDGE + DEFENDANT — Verifier + Executor                                │
  │                                                                         │
  │  [User Input] → [Judge: Verifier] ──→ [Defendant: Delegate Task]     │
  │       │                        │                        │                │
  │       │                        ▼                        ▼                │
  │       │                  Score ≥ 7?              Tool Call              │
  │       │                        │                        │                │
  │       │                   YES/NO                   YES/NO             │
  │       │                    │                        │                │
  │       └────────────────────┴────────────────────────┘                │
  │                              │                                        │
  │                              ▼                                        │
  │                        FINAL RESPONSE                                  │
  └─────────────────────────────────────────────────────────────────────────┘

  TIER 3 (EXPERT: 10)
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  FULL COURT — Judge + Prosecutor + Defendant + Evidence               │
  │                                                                         │
  │  ┌──────────────────────────────────────────────────────────────────┐ │
  │  │                    [USER INPUT]                                 │ │
  │  │                         │                                        │ │
  │  │                         ▼                                        │ │
  │  │              ┌─────────────────────┐                            │ │
  │  │              │   JUDGE (Verifier)  │                            │ │
  │  │              │  • Scores intent    │                            │ │
  │  │              │  • Assigns tier    │                            │ │
  │  │              └──────────┬─────────┘                            │ │
  │  │                         │                                        │ │
  │  │                         ▼                                        │ │
  │  │  ┌──────────────────────┴──────────────────────┐             │ │
  │  │  │           PROSECUTOR (Devil/Red-team)           │             │ │
  │  │  │  • Critiques execution plan                   │             │ │
  │  │  │  • Finds weaknesses                          │             │ │
  │  │  │  • Challenges assumptions                   │             │ │
  │  │  └──────────────────────┬──────────────────────┘             │ │
  │  │                         │                                            │ │
  │  │                         ▼                                            │ │
  │  │  ┌──────────────────────┬──────────────────────┐             │ │
  │  │  │    DEFENDANT (Executor)                     │             │ │
  │  │  │  • Executes task                            │             │ │
  │  │  │  • Provides evidence (tool traces)        │             │ │
  │  │  │  • Responds to critique                  │             │ │
  │  │  └──────────────────────┬──────────────────────┘             │ │
  │  │                         │                                            │ │
  │  │                         ▼                                            │ │
  │  │  ┌──────────────────────┬──────────────────────┐             │ │
  │  │  │      EVIDENCE (Checkpoints)                     │             │ │
  │  │  │  • Tool call traces                              │             │ │
  │  │  │  • State snapshots                          │             │ │
  │  │  │  • Execution history                       │             │ │
  │  │  └──────────────────────┬──────────────────────┘             │ │
  │  │                         │                                            │ │
  │  │                         ▼                                            │ │
  │  │              ┌─────────────────────┐                            │ │
  │  │              │   JUDGE (Verifier)   │                            │ │
  │  │              │  • Final verdict     │                            │ │
  │  │              │  • Score ≥ 7?       │                            │ │
  │  │              └──────────┬─────────┘                            │ │
  │  │                         │                                        │ │
  │  │                         ▼                                        │ │
  │  │                   RESPONSE                                      │ │
  │  └──────────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 代碼骨架 (Code Skeleton)

```python
"""BAW Court System — Black & White Court Architecture

court.py - Main court orchestrator that coordinates:
- Judge (Verifier): Scores and validates
- Prosecutor (Red-team): Critiques execution plan  
- Defendant (Executor): Executes the task
- Evidence (Checkpoints): Records tool traces
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class CourtTier(Enum):
    """Court tier levels."""
    TIER_0_TRIVIAL = 0   # Direct execution, no court
    TIER_1_MODERATE = 1    # Judge only (verifier)
    TIER_2_COMPLEX = 2    # Judge + Defendant
    TIER_3_EXPERT = 3     # Full court: Judge + Prosecutor + Defendant + Evidence


@dataclass
class CourtRole:
    """Base class for court roles."""
    name: str
    model_id: str
    
    def act(self, context: "CourtContext") -> "CourtResult":
        raise NotImplementedError


@dataclass
class Judge(CourtRole):
    """Judge role — Verifier.
    
    Responsibilities:
    - Scores intent and plan (0-10)
    - Assigns tier based on complexity
    - Issues final verdict
    """
    name: str = "Judge"
    threshold: int = 7
    
    def act(self, context: "CourtContext") -> "CourtResult":
        # Score the execution
        from core.verifier import verify_step
        result = verify_step(
            goal=context.goal,
            tool_name=context.tool_name,
            tool_args=context.tool_args,
            tool_result=context.tool_result,
            config=context.config,
            model_id=self.model_id,
        )
        return CourtResult(
            role=self.name,
            passed=result["passed"],
            score=result["score"],
            reason=result["reason"],
            verdict="APPROVED" if result["passed"] else "RETRY",
        )


@dataclass
class Prosecutor(CourtRole):
    """Prosecutor role — Red-team Critic (Devil).
    
    Responsibilities:
    - Critiques execution plan
    - Finds weaknesses
    - Challenges assumptions
    """
    name: str = "Prosecutor"
    
    def act(self, context: "CourtContext") -> "CourtResult":
        # Use adversarial.py Devil
        from core.adversarial import call_devil
        critique = call_devil(
            prompt=context.build_prosecution_prompt(),
            model_id=self.model_id,
        )
        return CourtResult(
            role=self.name,
            passed=True,  # Always proceeds
            score=0,
            reason=critique,
            verdict="CRITIQUE_ISSUED",
        )


@dataclass
class Defendant(CourtRole):
    """Defendant role — Executor Sub-agent.
    
    Responsibilities:
    - Executes the task
    - Provides evidence (tool traces)
    - Responds to critique
    """
    name: str = "Defendant"
    max_iterations: int = 12
    
    def act(self, context: "CourtContext") -> "CourtResult":
        from tools.delegate_task import delegate_task
        result = delegate_task(
            goal=context.goal,
            context=context.context,
            toolsets=context.toolsets,
        )
        # Record evidence
        context.add_evidence(result)
        return CourtResult(
            role=self.name,
            passed=True,
            score=0,
            reason=result,
            verdict="EXECUTED",
        )


@dataclass
class Evidence:
    """Evidence — Tool call traces and checkpoints."""
    traces: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)
    
    def add_trace(self, tool_name: str, args: dict, result: str):
        self.traces.append({
            "tool": tool_name,
            "args": args,
            "result": result[:500],  # Truncate
        })
    
    def add_checkpoint(self, state: dict):
        from core.checkpoint import Checkpointer
        cp = Checkpointer()
        cp.save(
            messages=state.get("messages", []),
            tool_name=state.get("tool_name", ""),
            tool_args=state.get("tool_args", {}),
            plan=state.get("plan", []),
        )
        self.checkpoints.append(cp)


@dataclass
class CourtResult:
    """Result from a court role."""
    role: str
    passed: bool
    score: int
    reason: str
    verdict: str  # APPROVED, RETRY, CRITIQUE_ISSUED, EXECUTED


@dataclass
class CourtContext:
    """Context passed through the court."""
    goal: str
    context: str = ""
    toolsets: str = ""
    config: dict = field(default_factory=dict)
    
    # Execution state
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    
    # Court roles
    judge: Optional[Judge] = None
    prosecutor: Optional[Prosecutor] = None
    defendant: Optional[Defendant] = None
    evidence: Evidence = field(default_factory=Evidence)
    
    # Budget tracking
    budget_spent: float = 0.0
    budget_limit: float = 10.0  # Default $10
    
    def build_prosecution_prompt(self) -> str:
        return f"""[Court Case]
Goal: {self.goal}
Context: {self.context}
Tools: {self.toolsets}

Critique this execution plan. Find:
1. Weaknesses in the approach
2. Potential failure points
3. Missing considerations

Be harsh but fair. This is a Court of Law."""


class Court:
    """Black & White Court Orchestrator.
    
    Coordinates tier-based execution:
    - Tier 0: Direct execution (no court)
    - Tier 1: Judge only (verifier)
    - Tier 2: Judge + Defendant
    - Tier 3: Full court (Judge + Prosecutor + Defendant + Evidence)
    """
    
    def __init__(self, config: dict):
        self.config = config
        self.tier = CourtTier.TIER_1_MODERATE
        
    def route(self, goal: str, context: str = "") -> CourtTier:
        """Route to appropriate tier based on complexity."""
        from core.router import score_complexity, tier_of
        score = score_complexity(goal)
        tier_name = tier_of(score)
        
        tier_map = {
            "trivial": CourtTier.TIER_0_TRIVIAL,
            "moderate": CourtTier.TIER_1_MODERATE,
            "complex": CourtTier.TIER_2_COMPLEX,
            "expert": CourtTier.TIER_3_EXPERT,
        }
        return tier_map.get(tier_name, CourtTier.TIER_1_MODERATE)
    
    def check_budget(self, context: CourtContext) -> bool:
        """Check if budget allows execution."""
        if context.budget_spent >= context.budget_limit:
            return False
        return True
    
    def execute(self, context: CourtContext) -> CourtResult:
        """Execute at the appropriate tier."""
        tier = self.route(context.goal, context.context)
        
        if tier == CourtTier.TIER_0_TRIVIAL:
            # Direct execution
            return self._execute_direct(context)
        
        elif tier == CourtTier.TIER_1_MODERATE:
            # Judge only
            return self._execute_with_judge(context)
        
        elif tier == CourtTier.TIER_2_COMPLEX:
            # Judge + Defendant
            return self._execute_with_defendant(context)
        
        else:  # TIER_3_EXPERT
            # Full court
            return self._execute_full_court(context)
    
    def _execute_direct(self, context: CourtContext) -> CourtResult:
        """Tier 0: Direct execution, no court."""
        # Use fast model, single tool
        return CourtResult(
            role="DIRECT",
            passed=True,
            score=10,
            reason="Trivial task - direct execution",
            verdict="EXECUTED",
        )
    
    def _execute_with_judge(self, context: CourtContext) -> CourtResult:
        """Tier 1: Judge only (verifier)."""
        # Initialize judge
        judge = Judge(
            model_id=self.config.get("model", {}).get("default", "deepseek-v4-flash"),
            threshold=7,
        )
        context.judge = judge
        return judge.act(context)
    
    def _execute_with_defendant(self, context: CourtContext) -> CourtResult:
        """Tier 2: Judge + Defendant."""
        # Initialize roles
        judge = Judge(
            model_id=self.config.get("model", {}).get("default", "deepseek-v4-flash"),
        )
        defendant = Defendant(
            model_id=self.config.get("executor", {}).get("model", "MiniMax-M2.5"),
        )
        context.judge = judge
        context.defendant = defendant
        
        # Execute defendant first
        def_result = defendant.act(context)
        
        # Then verify with judge
        context.tool_result = def_result.reason
        return judge.act(context)
    
    def _execute_full_court(self, context: CourtContext) -> CourtResult:
        """Tier 3: Full court (Judge + Prosecutor + Defendant + Evidence)."""
        # Initialize all roles
        judge = Judge(
            model_id=self.config.get("model", {}).get("default", "deepseek-v4-flash"),
        )
        prosecutor = Prosecutor(
            model_id=self.config.get("adversarial", {}).get("devil_model", "deepseek-v4-flash"),
        )
        defendant = Defendant(
            model_id=self.config.get("executor", {}).get("model", "MiniMax-M2.5"),
        )
        
        context.judge = judge
        context.prosecutor = prosecutor
        context.defendant = defendant
        
        # Phase 1: Judge assigns tier
        tier_decision = judge.act(context)
        
        # Phase 2: Prosecutor critiques
        critique = prosecutor.act(context)
        
        # Phase 3: Defendant executes
        context.add_evidence(critique.reason)  # Add critique as evidence
        def_result = defendant.act(context)
        
        # Phase 4: Judge issues verdict
        context.tool_result = def_result.reason
        final_verdict = judge.act(context)
        
        return final_verdict
```

### 3.4 預期收益

| 功能 | 當前狀態 | 預期改善 |
|------|----------|----------|
| Budget enforcement | ❌ Missing | ✅ Check before each call |
| Parallel coordination | ❌ Missing | ✅ Court coordinates multi-agent |
| Automatic fallback | ❌ Missing | ✅ Retry with different model |
| Cost control | ❌ Missing | ✅ Per-tier cost limits |
| Role separation | ⚠️ Partial | ✅ Full court structure |
| Verification | ⚠️ Simple | ✅ Multi-phase verification |

---

## 4. 行動項目 (Action Items)

### P0 (Must Fix — 黑白法庭核心)

1. **Add budget enforcement** — Check budget before LLM calls in `call_llm_with_fallback`
2. **Implement Court class** — Create court.py with Judge/Prosecutor/Defendant roles
3. **Add automatic model fallback** — Retry with next available model on failure

### P1 (Should Fix — 路由改進)

4. **Add cost limits per tier** — Configure max cost per tier in config.yaml
5. **Add parallel coordination** — Coordinate multiple delegate_task calls
6. **Enhance verifier** — Multi-phase verification for tier 2-3

### P2 (Nice to Have)

7. **Add retry with backoff** — Exponential backoff for sub-agent failures
8. **Add per-step timing** — Track execution time per step
9. **Add cost persistence** — Save cost history across sessions

---

## 5. 總結 (Summary)

### 當前架構評估

| 類別 | 評分 | 說明 |
|------|------|------|
| Tier detection | ✅ Good | score_complexity() works well |
| Model selection | ✅ Good | Config-driven preferences |
| Subagent dispatch | ⚠️ Partial | No parallel coordination |
| Verification | ⚠️ Basic | Single verifier, no court |
| Budget control | ❌ Missing | Critical gap |
| Cost enforcement | ❌ Missing | Critical gap |

### 黑白法庭價值

- **Separation of concerns**: Judge, Prosecutor, Defendant are distinct roles
- **Accountability**: Each role has clear responsibilities
- **Verifiability**: Evidence (checkpoints) records all tool calls
- **Budget control**: Court checks budget before proceeding
- **Automatic fallback**: Court retries with different models

### 建議優先級

1. Add budget enforcement (CRITICAL)
2. Implement Court class skeleton (HIGH)
3. Add automatic model fallback (HIGH)
4. Add parallel coordination (MEDIUM)

---

> Generated by BAW System Audit — Court Architecture (2026-06-12)