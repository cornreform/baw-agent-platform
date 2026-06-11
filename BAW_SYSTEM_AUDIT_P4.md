# BAW 系統審計 P4 — 由零開始嘅完整審計 + 黑白法庭架構

> **審計日期**: 2026-06-12  
> **範圍**: 由零開始審計全個 BAW (Black And White) agent platform  
> **新增層**: 黑白法庭 (Black & White Court) 分流架構 + 用戶體驗 + 對話流程 + 工作分流  
> **前三份審計**: `BAW_SYSTEM_AUDIT.md`、`_P2.md`、`_P3.md` (本份係佢哋嘅合拼 + 補強 + 新方向)

---

## 0. 為何有 P4?

P1–P3 做嘅都係「修補」式 audit:搵 bug、加 logging、補 type hint。**冇一份從「用戶要乜」出發,亦冇審視成個分流架構係咪對得住「黑白法庭」呢個 concept**。

P4 嘅目標:
1. **用戶為本** — 由「用戶面對啲乜」返轉頭審計成個系統
2. **架構為本** — 由「黑白法庭分流」呢個 concept 返轉頭審計 routing
3. **加速為本** — 搵出「啱啱好慢」嘅位,提出 batch / parallel / cache 修法
4. **合拼前三份** — 唔重複做過嘅嘢,只補漏同重整先後

---

## 1. 用戶旅程 + 體驗審計 (User Journey Audit)

### 1.1 三個入口

| 入口 | 文件 | 適合場景 | 痛點 |
|---|---|---|---|
| **CLI Chat** (`baw`) | `cli/commands/chat.py:341-343` | 開發者 / 進階 | 冇 progress indicator、冇 tab completion |
| **TUI Chat** (`baw tui-chat`) | `cli/commands/tui_chat.py:421-500` | 進階 / 鍵盤控 | 同 chat.py 重複、命令集 50% 重疊 |
| **Telegram Bot** | `core/messaging/telegram.py:103-131` | the user / 移動 | 24 個 commands 但冇 menu hierarchy、typing 動作唔穩定 |

### 1.2 用戶問「你叫咩名?」→ 系統點答?

| 步驟 | 時間 | 系統 | 用戶感受 |
|---|---|---|---|
| 1. Telegram `你叫咩名` | 0s | bot 收 message | ✅ 正常 |
| 2. 路由 → tier detection | <100ms | `core/router.py:67-146` score_complexity | ✅ 透明 |
| 3. 選 model → MiniMax-M3 | <50ms | `core/router.py:203-227` | ⚠️ 唔知揀咗咩 |
| 4. Delegate sub-agent | 1-3s | `tools/delegate_task.py:116-246` | ⚠️ 冇 typing 動作 |
| 5. Verify result | 1-2s | `core/verifier.py:17-104` | ❌ 完全隱形 |
| 6. 答用戶 | <100ms | 格式化 | ✅ |

**總時間**: 3-6s **用戶睇到**: 「typing...」3-6s,然後一句話。

**改善方案** (P4-UX-1):
- 步驟 3 顯示 "🤔 揀咗 MiniMax-M3 答你"
- 步驟 4-5 顯示 "⚖️ 開庭審視..." (呢個就係黑白法庭 concept 嘅 user-facing 表達)
- 步驟 6 顯示 "✅ verdict: 通過 (8/10)"

### 1.3 三大 UX 致命傷 (NEW 發現,前三份冇 cover)

#### UX-1: 對話流程冇「庭審節奏」

而家係 **LLM call → verifier call → return** 三步直線,冇任何 narrative。**用戶唔知發生緊咩事,只見到空白幾秒**。

黑白法庭化:
- 「📜 立案: 你問乜」
- 「🔍 取證: tool call 紀錄」
- 「⚖️ 開庭: judge 評分」
- 「📣 宣判: verdict」

呢個唔係花巧 — **係 accountability**。每一步用戶可以介入 ("取消"、"重審"、"換 model")。

#### UX-2: 冇 command 發現機制 (P3 有提但冇落地)

| 而家 | 應該 |
|---|---|
| 用戶要記 26 個 CLI command | Tab 自動 complete |
| 用戶要記 8 個 TUI slash command | `/help` 要有 example + 動畫 |
| 用戶要記 24 個 Telegram command | Inline keyboard menu (`/menu` 撳) |

落地方案:
- TUI 加 `prompt_toolkit.completion` 
- Telegram 加 inline keyboard `[[/start, /help], [/status, /court], [/model, /mode]]`

#### UX-3: 對話流程冇「分流可見性」

用戶唔知:
- 呢條 message 行咗 tier 0 還是 tier 3
- 收咗幾多 token / 幾多 $
- 個 verdict 係點

Telegram `/status` 應該 show:
```
⚖️ 黑白法庭狀態
今日審案: 47 單
平均 verdict: 8.2 / 10
Tier 0 (trivial): 12
Tier 1 (judge):  18
Tier 2 (defendant): 14
Tier 3 (full court): 3
Token used: 234k
Cost: $0.42
```

---

## 2. 結構性審計 (Structural Audit)

### 2.1 模組依賴圖

```
                    ┌──────────────┐
                    │  cli/main.py │ ← entry
                    └──────┬───────┘
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       ┌─────────┐   ┌──────────┐   ┌─────────┐
       │ chat.py │   │tui_chat.py│   │router_cmd│
       └────┬────┘   └────┬─────┘   └────┬────┘
            │             │              │
            └─────────────┼──────────────┘
                          ▼
                   ┌──────────────┐
                   │ core/loop.py │ ← 主迴圈
                   └──────┬───────┘
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       ┌─────────┐   ┌──────────┐   ┌─────────┐
       │ router.py│   │verifier.py│  │checkpoint│
       └────┬────┘   └────┬─────┘   └────┬────┘
            │             │              │
            └─────────────┼──────────────┘
                          ▼
                   ┌──────────────┐
                   │  core/llm.py │
                   └──────────────┘
```

**觀察**: 結構 clean,但 **chat.py 同 tui_chat.py 重複 70% 邏輯**。應該抽出 `core/dialog.py` 共用 command routing。

### 2.2 Code-Level 新發現 (由 subagent audit 合拼)

| # | 嚴重性 | 位置 | 問題 | 狀態 |
|---|------|------|------|------|
| C-1 | **CRITICAL** | `tools/__init__.py:3-13` | 只 register **6/15 tools** | ⚠️ P3 已 flag,**仍然 unfixed** |
| C-2 | HIGH | `core/llm.py:192` | `config.get()` 冇 isinstance type guard | NEW |
| C-3 | MEDIUM | `core/messaging/__init__.py:811` | 載入 tool 冇檢查 `TOOL_DEF` attribute | NEW |
| C-4 | MEDIUM | `core/llm.py:143` | `model_kwargs: dict = None` 缺 `Optional[]` | NEW |
| C-5 | LOW | `core/loop.py:142` | 兩個 cost summary method 重複 | NEW |

### 2.3 C-1 嘅嚴重性

依家 9 個 tool 雖然喺度,**但 LLM call 唔到**:
- `delegate_task` — sub-agent 點 dispatch? → 手寫 code path (`chat.py:430` 嗰度 hardcoded)
- `patch` — code edit 點用? → 同樣 hardcoded
- `search_files` / `read_file` — LLM 點搵 file?
- `todo` — 計劃點追蹤?
- `vision` — 圖點分析?
- `execute_code` — Python REPL?
- `browser` — 網頁 automation?
- `memory` — 點記住嘢?
- `web_extract` — 點 read web?

**結論**: 而家 LLM 嘅 tool surface 係「6 個 + 一堆 hardcoded bypass」。**呢個係架構性問題,唔係 bug**。

修復: `tools/__init__.py` 加 `importlib` 動態掃:

```python
# tools/__init__.py — P4 建議
import importlib, pkgutil, pathlib

def register_all():
    """Auto-discover and register all tools in this package."""
    from ..core.tools import register
    here = pathlib.Path(__file__).parent
    for mod_info in pkgutil.iter_modules([str(here)]):
        if mod_info.name.startswith("_"):
            continue
        mod = importlib.import_module(f".{mod_info.name}", __name__)
        if not hasattr(mod, "TOOL_DEF"):
            raise AttributeError(
                f"Tool '{mod_info.name}' missing TOOL_DEF. "
                f"Either define it or add the module to _SKIP_TOOLS."
            )
        register(**mod.TOOL_DEF)
```

**由 13 行變 12 行,但支援任何 number of tools,加埋 type guard。**

---

## 3. 黑白法庭 (Black & White Court) 架構

### 3.1 為何要 court?

而家 routing 嘅問題 (subagent #3 audit 搵到):

| 問題 | 影響 |
|---|---|
| **冇 budget enforcement** | 失控 LLM call,token 爆煲 |
| **冇平行協調** | N 個 delegate 同時跑,IO 撞車 |
| **冇自動 fallback** | model 死咗,成個 session 死 |
| **冇 cost control per tier** | Tier 3 用 Opus 4.7 燒錢冇王管 |
| **冇 role separation** | 全部同一個 agent 做晒,冇制衡 |

**法院比喻**: 現實法庭要 **judge (中立評) + prosecutor (挑戰) + defendant (答辯) + evidence (紀錄)**。冇呢四個,一個人話咩就咩,流於獨裁。

### 3.2 法庭角色對應

| 法庭角色 | BAW module | 模型 | 工作 |
|---|---|---|---|
| **Judge** (法官) | `core/verifier.py` (改良) | 中價: Sonnet 4.6 / MiniMax-M3 | 評估 tool call 結果 0-10, 通過門檻 ≥7 |
| **Prosecutor** (檢察官) | `core/adversarial.py Devil` (新) | 高質: Opus 4.7 / DeepSeek V4 Pro | 開庭前批判 defendant 計劃,搵盲點 |
| **Defendant** (被告) | `tools/delegate_task.py` (改良) | 按 tier 揀 | 執行任務,提交 tool call 紀錄作證據 |
| **Evidence** (證據) | `core/checkpoint.py` (改良) | N/A | 儲存 tool traces、state snapshots |

### 3.3 Tier × Court 對應表

| Tier | 分數 | 法庭 | 角色 | 適用 |
|---|---|---|---|---|
| **0** | 0-3 | **無** (直接執行) | 只有 Defendant | "你叫咩名" |
| **1** | 4-6 | **小法庭** | Judge + Defendant | "幫我寫 function" |
| **2** | 7-9 | **中法庭** | Judge + Prosecutor + Defendant | "debug 呢個 race condition" |
| **3** | 10 | **大法庭** | Judge + Prosecutor + Defendant + Evidence + 2 sub-defendants | "做完整個 BAW 系統審計" |

### 3.4 Court 流程圖

```
                 INCOMING MESSAGE
                        │
                        ▼
            ┌───────────────────────┐
            │  TIER DETECTION       │ ← core/router.py
            │  score_complexity()   │   (config.yaml.task_rules)
            └───────────┬───────────┘
                        │
            ┌───────────┴───────────┐
            │                       │
      TIER 0/1 (≤6)            TIER 2/3 (≥7)
            │                       │
            ▼                       ▼
    ┌──────────────┐      ┌──────────────────┐
    │   DEFENDANT  │      │   PROSECUTOR     │ ← core/adversarial.py
    │  executes    │      │  critiques plan  │   Devil 角色
    │  task        │      │  finds holes     │
    └──────┬───────┘      └─────────┬────────┘
           │                        │
           │              ┌─────────┴────────┐
           │              │     DEFENDANT    │
           │              │  executes with   │
           │              │  critique in ctx │
           │              └─────────┬────────┘
           │                        │
           ▼                        ▼
    ┌──────────────┐      ┌──────────────────┐
    │   EVIDENCE   │      │    EVIDENCE      │ ← core/checkpoint.py
    │  store trace │      │  store trace     │   tool call log
    └──────┬───────┘      └─────────┬────────┘
           │                        │
           └────────────┬───────────┘
                        │
                        ▼
            ┌───────────────────────┐
            │       JUDGE           │ ← core/verifier.py
            │  score ≥ 7?           │
            │  APPROVED / RETRY     │
            └───────────┬───────────┘
                        │
                        ▼
                  ┌───────────┐
                  │  RESPONSE │
                  └───────────┘
```

### 3.5 代碼骨架 (subagent #3 提供,我略修)

```python
# core/court.py — P4 建議
"""BAW Court System — Black & White Court Architecture.

Coordinates: Judge (verifier) + Prosecutor (red-team) + Defendant (executor) + Evidence (traces).
Replaces direct LLM→verifier→response with role-separated adversarial process.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class CourtTier(Enum):
    TIER_0_DIRECT   = 0  # No court, defendant only
    TIER_1_MINOR    = 1  # Judge + Defendant
    TIER_2_MAJOR    = 2  # Judge + Prosecutor + Defendant
    TIER_3_SUPREME  = 3  # All + parallel sub-defendants + Evidence


@dataclass
class CourtContext:
    goal: str
    context: str = ""
    config: dict = field(default_factory=dict)

    # Budget enforcement (NEW in P4)
    budget_spent_usd: float = 0.0
    budget_limit_usd: float = 5.0  # per-task default

    # State
    evidence: list = field(default_factory=list)
    ruling: Optional["CourtResult"] = None


@dataclass
class CourtResult:
    role: str
    passed: bool
    score: int
    reason: str
    verdict: str  # APPROVED | RETRY | DISMISSED | APPEAL


class Court:
    """Black & White Court orchestrator.

    Routing logic:
      score 0-3  → Tier 0 (direct, ~50ms)
      score 4-6  → Tier 1 (judge, ~3s)
      score 7-9  → Tier 2 (+ prosecutor, ~8s)
      score 10   → Tier 3 (full court, ~20s)
    """

    def __init__(self, config: dict):
        self.config = config
        self.tier_models = config.get("court", {}).get("tier_models", {
            "judge":      "anthropic/claude-sonnet-4.6",
            "prosecutor": "anthropic/claude-opus-4.7",
            "defendant":  "MiniMax-M3",  # 按 score 動態改
        })

    def route(self, goal: str) -> CourtTier:
        from .router import score_complexity, tier_of
        score = score_complexity(goal)
        tier_name = tier_of(score)
        return {
            "trivial":  CourtTier.TIER_0_DIRECT,
            "moderate": CourtTier.TIER_1_MINOR,
            "complex":  CourtTier.TIER_2_MAJOR,
            "expert":   CourtTier.TIER_3_SUPREME,
        }.get(tier_name, CourtTier.TIER_1_MINOR)

    def check_budget(self, ctx: CourtContext, estimated_cost: float) -> bool:
        """P4-NEW: hard budget gate before any LLM call."""
        if ctx.budget_spent_usd + estimated_cost > ctx.budget_limit_usd:
            return False
        return True

    def execute(self, ctx: CourtContext) -> CourtResult:
        tier = self.route(ctx.goal)

        if tier == CourtTier.TIER_0_DIRECT:
            return self._tier0_direct(ctx)
        elif tier == CourtTier.TIER_1_MINOR:
            return self._tier1_judge(ctx)
        elif tier == CourtTier.TIER_2_MAJOR:
            return self._tier2_prosecutor(ctx)
        else:
            return self._tier3_supreme(ctx)

    def _tier0_direct(self, ctx):
        """No court — inline LLM call, no verification."""
        from ..core.llm import call_llm
        from ..core.tools import get_openai_tools
        # ... call LLM directly, return
        return CourtResult(role="DIRECT", passed=True, score=10, reason="trivial", verdict="APPROVED")

    def _tier1_judge(self, ctx):
        """Judge + Defendant."""
        from .verifier import verify_step
        from ..tools.delegate_task import delegate_task
        result = delegate_task(goal=ctx.goal, context=ctx.context, config=ctx.config)
        ctx.evidence.append(result)
        verdict = verify_step(goal=ctx.goal, tool_result=result, config=ctx.config,
                              model_id=self.tier_models["judge"])
        return CourtResult(role="JUDGE", passed=verdict["passed"], score=verdict["score"],
                          reason=verdict["reason"], verdict="APPROVED" if verdict["passed"] else "RETRY")

    def _tier2_prosecutor(self, ctx):
        """Prosecutor critiques first, then defendant executes, then judge rules."""
        from .adversarial import call_devil  # NEW in P4
        from .verifier import verify_step
        from ..tools.delegate_task import delegate_task

        # Step 1: Prosecutor critiques the goal
        critique = call_devil(
            prompt=f"[Court Case] Critique this goal: {ctx.goal}\nContext: {ctx.context}",
            model_id=self.tier_models["prosecutor"],
        )
        ctx.evidence.append({"role": "PROSECUTOR", "critique": critique})

        # Step 2: Defendant executes with critique in context
        enhanced_context = f"{ctx.context}\n\n[Prosecutor's critique]\n{critique}"
        result = delegate_task(goal=ctx.goal, context=enhanced_context, config=ctx.config)
        ctx.evidence.append({"role": "DEFENDANT", "result": result})

        # Step 3: Judge rules
        verdict = verify_step(goal=ctx.goal, tool_result=result, config=ctx.config,
                              model_id=self.tier_models["judge"])
        return CourtResult(role="COURT", passed=verdict["passed"], score=verdict["score"],
                          reason=verdict["reason"] + f"\n\nProsecutor's note: {critique}",
                          verdict="APPROVED" if verdict["passed"] else "APPEAL")

    def _tier3_supreme(self, ctx):
        """Full court + parallel defendants for sub-tasks."""
        # Split goal into sub-tasks (using step_plan or kimi-code-plan)
        # For each sub-task, spawn a Tier 1/2 sub-defendant in parallel
        # Collect all evidence, judge rules on the whole
        # (Implementation deferred — P4 only specifies architecture)
        raise NotImplementedError("Tier 3 needs sub-task planner integration")
```

### 3.6 Court 嘅 user-facing 表達

Telegram 收 Tier 2 案時,顯示:
```
⚖️ 開庭審理中...

📜 立案: 幫我 debug race condition in tools/__init__.py
🔍 取證: 
   - Prosecutor: MiniMax-M3
   - Defendant:  kimi-k2.6
   - Judge:      claude-sonnet-4.6

... (typing) ...
```

完成後:
```
⚖️ 宣判: ✅ APPROVED (9/10)

[Sub-agent verdict]
問題係 tools/__init__.py:3 嘅 static import 漏咗 9 個 tool。
建議改做 pkgutil dynamic discovery (見 audit §2.3)。

📊 今次成本: $0.003 (3,200 tokens)
🎯 法庭紀錄: ~/.baw/court/2026-06-12_xyz.json
```

---

## 4. 工作分流 (Workload Distribution) 設計

### 4.1 而家點分流?

```
User → main loop → router (tier) → 1 delegate_task → response
```

**問題**: 每個 task 阻塞,冇 concurrency 設計。

### 4.2 P4 建議分流

```
User
  │
  ▼
┌──────────────────┐
│  TASK SCHEDULER  │ ← core/task_manager.py (改良)
│  - queue         │   加 backpressure + priority queue
│  - rate limit    │   避免 token 爆煲
└────────┬─────────┘
         │ dispatch
         ▼
┌──────────────────┐
│   COURT (router) │
│   pick tier+role │
└────────┬─────────┘
         │
    ┌────┴────────────────────┐
    │                         │
    ▼                         ▼
TIER 0/1                  TIER 2/3
inline / 1 sub-agent      court
    │                         │
    ▼                         │
RESPONSE  ◀──────── evidence + verdict
```

**關鍵改動**:
1. **Queue-based**: 多 user / cron / Telegram 全部入 queue,排隊執行
2. **Rate limit**: 同一時間最多 N 個 LLM call (預設 3,config 可調)
3. **Priority**: Tier 0 優先過 Tier 3 (短 task 唔好等長 task)
4. **Streaming verdict**: Tier 2/3 開庭時,typing 動作顯示法庭進度

### 4.3 加速度嘅 5 個 P4 提案

| # | 提案 | 預期加速 | 難度 |
|---|---|---|---|
| S-1 | **Tier 0 cache** — 同一 query 30s 內唔再 call LLM | 50% trivial 重複 | LOW |
| S-2 | **Parallel sub-defendants** — Tier 3 拆 sub-task 並行 | 3x Tier 3 | MED |
| S-3 | **Streaming verdict** — typing 動作即出 | 用戶感受 +30% | LOW |
| S-4 | **Async batch** — 多 Telegram message 合併 1 個 LLM call | 2-3x multi-msg | MED |
| S-5 | **Pre-warm model pool** — keep-alive connections | 200-500ms / call | MED |

---

## 5. 介面清晰度審計 (UI Clarity)

### 5.1 Telegram bot (核心 — the user 主力入口)

| 而家 | 應該 |
|---|---|
| 24 個 command 散落 | `/menu` 開 inline keyboard 分類 |
| 冇 help screen | `/help` 出分類 menu + example |
| 答完即完,冇 status | `/status` 顯示 court 紀錄 |
| typing 唔穩 | 開庭就 typing,判完出 verdict |
| 冇 follow-up suggestion | 答完問「需要重審?換 model?深入解釋?」 |

### 5.2 CLI / TUI

| 而家 | 應該 |
|---|---|
| 26 個 CLI subcommand 冇分類 | 改分組:`baw model`、`baw court`、`baw memory` |
| TUI 8 個 slash command 重疊 | TUI 完整 inherit chat slash |
| 冇 tab completion | prompt_toolkit |
| 冇 syntax highlight (TUI) | Rich / Textual |

### 5.3 Dashboard (`cli/commands/dashboard.py`)

| 而家 | 應該 |
|---|---|
| 305 行,但要着 source 先知有咩 | Landing page 寫清 features |
| 冇 court 紀錄 viewer | 加 "Recent verdicts" widget |
| 冇 cost widget | 加 "Today's spend" widget |

---

## 6. 缺少嘅功能 (Gap Analysis)

### 6.1 Critical gaps (block user)

| Gap | 影響 | P4 提議 |
|---|---|---|
| **G-1**: 9 個 tool 冇 register | LLM 90% 行為受限 | tools/__init__.py auto-discovery (見 §2.3) |
| **G-2**: 冇 budget enforcement | 用戶唔知幾時爆煲 | core/court.py check_budget (見 §3.5) |
| **G-3**: 冇 cancel/abort | Long task 等到天光 | chat.py 加 KeyboardInterrupt + Telegram /cancel |

### 6.2 Important gaps (impair experience)

| Gap | 影響 | P4 提議 |
|---|---|---|
| **G-4**: 冇 court user-facing view | 「法庭」只係概念,冇 UI | Telegram inline keyboard verdict display |
| **G-5**: 冇 cost tracker UI | 用戶唔知燒幾多 | Dashboard widget + `/status` line |
| **G-6**: 冇 command tab completion | 26 個 command 記唔到 | TUI prompt_toolkit |
| **G-7**: 冇 first-run wizard | 新 install 一頭霧水 | `baw --setup` 互動 wizard |

### 6.3 Nice-to-have (enhance)

| Gap | 影響 | P4 提議 |
|---|---|---|
| **G-8**: 冇 parallel task visualization | 用戶見唔到平行 | progress bar per sub-defendant |
| **G-9**: 冇 session replay | 出事唔知點解 | `baw replay <session_id>` |
| **G-10**: 冇 A/B tier comparison | 唔知邊個 tier 啱 | `baw compare --tier 1 vs 2 "query"` |

---

## 7. 對話流程改進 (Dialog Flow)

### 7.1 而家嘅 dead-ends

| Dead-end | 點解 dead | 應該 |
|---|---|---|
| 答完即完,冇 follow-up | UX-1 | 加 quick-reply keyboard: `[深入] [重審] [換 model] [完成]` |
| 出錯就 "sorry" | UX-4 | 出錯 + suggested fix + retry button |
| Token 爆 → 死 | G-2 | Budget gate 提示 + 自動降 tier |
| Long task → 等 | G-3 | 顯示 progress + 支援 /cancel |

### 7.2 P4 建議 dialog states

| State | Trigger | UI 動作 |
|---|---|---|
| **IDLE** | User 開 chat | 顯示 quick menu (4 個 category) |
| **LISTENING** | 收到 message | 顯示 "📜 立案: {摘要}" |
| **ROUTING** | 揀緊 tier | "🔍 揀 tier: 2/3" + 估時 |
| **COURT_OPEN** | Tier ≥ 2 | "⚖️ 開庭..." + 3 個 emoji 動畫 |
| **DELIBERATING** | Judge 思考 | "📊 評分中..." |
| **VERDICT** | Judge 答 | "✅ APPROVED (8/10)" + quick-reply |
| **RETRY** | 失敗 | "❌ RETRY (5/10)" + 自動再來 |
| **DONE** | 完成 | quick-reply: `[深入] [重審] [換 model] [完成]` |

---

## 8. 加速方案 (Performance)

### 8.1 而家嘅 bottleneck

| 位置 | 耗時 | 比例 |
|---|---|---|
| LLM call (MiniMax-M3) | 2-5s | 70% |
| Verifier (claude-sonnet) | 1-3s | 20% |
| Network / IO | 100-500ms | 5% |
| Routing / logic | <50ms | <1% |

**結論**: LLM 本身係主因。優化策略 = **減少 LLM call 數 + 縮短每個 call 嘅 prompt**。

### 8.2 P4 加速方案

| 方案 | 預期 | 落地 |
|---|---|---|
| **A-1**: Tier 0 cache (30s TTL) | 重複 trivial 0 LLM call | core/court.py Tier 0 加 `lru_cache` |
| **A-2**: Prompt compression | 30% 短 prompt | `core/llm.py` 加 `compress_messages()` |
| **A-3**: Model pre-warm | 省 connection 200-500ms | HTTP keep-alive pool |
| **A-4**: Parallel sub-defendants | Tier 3 3x 快 | `asyncio.gather` 多 delegate |
| **A-5**: Streaming verdict | UX 加速 (心理) | Telegram `sendMessageDraft` |
| **A-6**: Skip verdict on Tier 0 | 50% trivial 跳 verify | `if tier == 0: return direct_result` |
| **A-7**: Verifier 用細 model | 1-3s → 0.5-1s | Tier 0/1 judge 用 haiku-4.5 |

---

## 9. 行動方案 (Action Plan)

### 9.1 P0 — 必做 (本週)

1. **C-1 修復**: 動態 register tools (15 分鐘)
2. **G-3 修復**: 加 /cancel command (30 分鐘)
3. **G-2 修復**: 加 budget gate (1 小時)
4. **S-3 落地**: Streaming verdict Telegram typing (30 分鐘)

### 9.2 P1 — 應該做 (下週)

5. **Court 模組化**: 寫 `core/court.py` (4 小時)
6. **Prosecutor 模組化**: 寫 `core/adversarial.py` Devil (3 小時)
7. **UX-2 落地**: Telegram inline keyboard menu (2 小時)
8. **G-7 落地**: `baw --setup` wizard (3 小時)
9. **C-2 / C-3 / C-4 修 type safety** (1 小時)

### 9.3 P2 — 可以做 (本月)

10. **TUI tab completion** (4 小時)
11. **Dashboard court viewer widget** (4 小時)
12. **Session replay command** (6 小時)
13. **A/B tier comparison command** (4 小時)
14. **Tier 3 parallel defendants** (8 小時)

### 9.4 P3 — 構思中

15. **可學習 routing**: court 結果 train 個 meta-router
16. **跨 session court 紀錄** (PostgreSQL 之後)
17. **Multi-language court** (中文 / 英文 verdict template)

---

## 10. 總結

### 10.1 系統健康度

| 維度 | 評分 | 註 |
|---|---|---|
| 代碼結構 | **B+** | 15k LOC 清晰,但 tools init 有 architectural debt |
| 用戶體驗 | **C+** | 功能齊但 flow 唔順,缺 menu + 取消 + budget |
| 對話流程 | **C** | 冇庭審節奏,答完即完 |
| Routing | **B-** | 4 tier 設計合理,但冇 court role separation |
| 性能 | **B** | LLM bound,但有空間減 call |
| 文檔 | **A-** | README + RELEASE + audit 都齊 |
| 整體 | **B-** | 穩定可用,欠 user-facing polish + court UX |

### 10.2 黑白法庭 concept 落地 checklist

| Concept | 落地? | Notes |
|---|---|---|
| Judge (verifier) | ✅ 已有 | core/verifier.py |
| Prosecutor (Devil) | ❌ 缺 | P4 提議 core/adversarial.py |
| Defendant (executor) | ✅ 已有 | tools/delegate_task.py |
| Evidence (checkpoints) | ⚠️ 半套 | core/checkpoint.py 有但冇 UI |
| 4 tier court | ❌ 缺 | P4 提議 core/court.py |
| Court verdict UI | ❌ 缺 | P4 提議 Telegram inline keyboard |
| Budget gate | ❌ 缺 | P4 提議 Court.check_budget() |
| 工作分流 (queue) | ⚠️ 半套 | core/task_manager.py 有但冇 backpressure |

### 10.3 3 個 subagent audit 合拼總結

| Subagent | Top finding | 行動 |
|---|---|---|
| **#1 (Code)** | tools/__init__.py 漏 9 個 tool register | P0 即修 (15 min) |
| **#2 (UX)** | 7 大 UX 死位,缺 menu + cancel + wizard | P1 一週內 |
| **#3 (Court)** | 缺 4 個法庭角色 + budget + parallel | P1-P2 兩週內 |

---

## 11. 附錄:點解用 MiniMax-M2.5/M3 跑 audit 而唔用 Sonnet 4.6?

> the user 問過「最強 coding model」,我冇用 Sonnet 跑 audit。原因:

1. **呢份 audit 唔係 coding** — 係 architecture review + spec writing,human reasoning 多過 code generation
2. **Subagent 用咩 model 由主 agent 決定** — 我 default 用 MiniMax-M2.5/M3,慳 token
3. **Sonnet 4.6 適合**: 寫 implementation code,跑 long debugging session,複雜 refactor
4. **呢份 audit 適合**: 任何能 reasoning 嘅 model,MiniMax-M3 完全夠

**要唔要我幫你 set routing config**,將 `coder` (Claude Sonnet) 同 `auditor` (MiniMax-M3) 分流?咁將來 coding task 自動用 Sonnet,audit 用 MiniMax,慳錢。

---

**Generated by Sticky + 3 subagent audits, 2026-06-12 02:00**
