# BAW 第四審計報告 — 子代理遺漏嘅跨模組問題

> 三個子代理覆蓋咗單模組嘅 logic/security/UX/court。佢哋全部遺漏咗**模組之間嘅接縫**。以下係接縫嘅問題。

---

## Critical (P0)

### P0-1 — Router 同 delegate_task 用**兩套完全唔同**嘅模型選擇邏輯,從不交匯
- `core/router.py:280` `route_task()` → `pick_model_for_tier()` 用 `router.tier_preferences`
- `tools/delegate_task.py:48` `_resolve_executor_model()` 用 `model.task_rules` + `executor.model`
- **問題**: `route_task()` 計出 `model_id` 同 `delegate=True`,但 `delegate_task()` **完全忽略** route decision 嘅 `model_id`,自己重新用 task_rules 揀過。tier routing 嘅整套 `tier_preferences` 對 sub-agent **零作用**。
- 結果: `baw router set expert kimi-k2.6` 設定咗,但 expert task delegate 落去之後,執行嘅係 `config.yaml:25 executor.model: MiniMax-M2.5`。用戶設定被靜默丟棄。
- **Fix**: `delegate_task` 應接收 caller 傳入嘅 `model_id`:
```python
def delegate_task(goal, context="", toolsets="", model_id: str = ""):
    config = _get_minimax_config(goal)
    if model_id:  # respect router decision
        config["model"]["default"] = model_id
```
並喺 loop 調用 delegate 時把 `RouteDecision.model_id` 傳落去。

### P0-2 — `config.yaml` 完全缺失 `model.fallback`,但成個 fallback 鏈靠佢
- `config.yaml:5-19` `model:` 只有 `default`,**冇 `fallback`**
- `config.sample.yaml:5` 有 `fallback: deepseek-v4-flash`
- `core/llm.py call_llm_with_fallback` (truncated) + `delegate_task.py:74` `model_cfg.get("fallback", executor_model)` 都依賴 `model.fallback`
- **問題**: 真正用嘅 config 冇 fallback,sample 有。Config drift。primary 一掛,fallback resolve 返自己 → 死循環/即時報錯,circuit breaker 都救唔到(因為兩個 id 一樣)。
- **Fix**: `config.yaml` 補 `model.fallback: deepseek-reasoner`(必須係**另一個** model)。

### P0-3 — `router.py` 默認 tier 模型全部唔存在於 `config.yaml`
- `core/router.py:166-171` `DEFAULT_TIER_PREFERENCES` 全部係 `step-3.5-flash-2603` / `step-3.7-flash` / `kimi-k2.6`
- `config.yaml:73-105` providers 只有 `deepseek-v4-flash`, `deepseek-reasoner`, `MiniMax-M3`, `MiniMax-M2.5`
- **問題**: `pick_model_for_tier()` 嘅 preference list **零個 match**,每次都跌落 `core/router.py:222` "last resort: any chat model" → `next(iter(available))`,即 set 嘅**隨機**一個。tier routing 表面 work,實際每 tier 都揀同一個任意模型。仲衰過 hardcode。
- **Fix**: `DEFAULT_TIER_PREFERENCES` 應由 config providers **動態 derive**,或者 `config.yaml` 補 `router.tier_preferences` 指向真實存在嘅 model id。

---

## High (P1)

### P1-1 — `should_re_delegate()` 寫咗但**冇人 call**,multi-tier cascade 係死代碼
- `core/router.py:330` `should_re_delegate()` + `needs_multi_tier()` 定義齊
- grep 全 codebase: loop / delegate_task **都冇 import router 任何嘢**
- **問題**: 整個 tier-based router(router.py 全 file)根本**冇被 agent loop 接駁**。delegate_task 自己有套 task_rules,loop 有自己嘅 court。router.py 係孤島 module,只有 `router_cmd.py` CLI 讀寫佢嘅 config,但 runtime 從不執行 `route_task()`。
- **驗證**: `delegate_task.py` import 嘅係 `core.llm`, `core.tools`, `core.context` — 冇 `core.router`。
- **Fix**: 喺 loop.py 嘅 turn 入口 call `route_task()`,根據 `decision.delegate` 決定 inline vs delegate,並把 `decision.model_id` 傳入。否則刪走 router.py 免得誤導。

### P1-2 — `delegate_task._import_baw()` 喺 module load 時 `clear()` 全 registry,有 race
- `tools/delegate_task.py:42` `_clear()` 然後逐個 re-register 6 個 tool
- **問題**: `core/tools._tools` 係 global(P3 已指出 global,但無人指出呢個後果)。若主 loop 同 delegate_task 喺同一 process(CLI inline 模式),sub-agent `_clear()` 會**清走主 agent 已註冊嘅 tool**(尤其 P3 fix 後註冊咗 16 個)。sub-agent 跑完冇 restore,主 agent 之後 `execute_tool` 就揾唔到 tool。
- 仲有: delegate_task 只 re-register 6 個(`bash,read_file,write_file,web_search,vision,tts`),連自己 `delegate_task` 都冇 → 即係**禁止 nested delegate**(P3 想要嘅 max_depth=1 意外達成),但同時**主 agent 嘅 patch/search_files/memory/todo 全部蒸發**。
- **Fix**: sub-agent 用**獨立 registry instance**,唔好掂 global。或者 save/restore:
```python
_saved = dict(core.tools._tools)
try: ... finally: core.tools._tools = _saved
```

### P1-3 — `_get_minimax_config` reads `~/.baw/config.yaml`,但 CLI chat reads `BAW_ROOT/config.yaml` merged — **兩個 config 來源**
- `delegate_task.py:65` `Path.home()/".baw"/"config.yaml"`
- `cli/commands/chat.py:48` `_cfg()` merge `BAW_ROOT/config.yaml` + `BAW_HOME/config.yaml`
- `core/llm.py load_config` 又只讀 `~/.baw/config.yaml`(repo config 唔讀)
- **問題**: 三條路徑三種 merge 策略。repo `config.yaml`(有 task_rules, executor, capabilities)**只有 CLI chat 會讀**;delegate_task 同 core.llm **只讀 ~/.baw**。若用戶冇 copy repo config 落 ~/.baw,delegate_task 嘅 `executor.model` / `task_rules` **全部攞唔到** → 跌落 `model.default` fallback,per-task routing 失效。
- **Fix**: 統一一個 `load_config()`,所有入口共用,明確 merge order。

### P1-4 — Checkpoint / Verifier 喺 delegate_task **完全冇用**
- `core/checkpoint.py` + `core/verifier.py` 存在
- `delegate_task.py:178-208` sub-agent loop **裸跑**:無 checkpoint、無 verify_step、無 retry
- **問題**: P3/Court 報告講 verifier 係 "judge",但 delegate(complex/expert tier 嘅執行路徑)根本冇行 verifier。verifier 只可能喺 loop.py inline path 用。即係**越複雜嘅 task(delegate)越冇 verification**,完全倒轉。
- **Fix**: delegate_task loop 每個 tool result 後 call `verify_step()`,fail 就 retry(配 P1-2 嘅 isolated registry)。

### P1-5 — `signal.signal()` 喺 module import 時無條件註冊 — sub-process / thread 會炸
- `core/llm.py:108-109` import 時 `signal.signal(SIGTERM/SIGINT, _on_shutdown)`
- **問題**: `signal.signal` 只可喺 **main thread** call。`guards.py execute_tool_with_timeout` 用 ThreadPoolExecutor、`task_manager` spawn subprocess、TUI(textual)跑喺非 main thread。任何喺非 main-thread import `core.llm` 都會 `ValueError: signal only works in main thread`。
- **Fix**:
```python
try:
    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)
except ValueError:
    pass  # not main thread
```

---

## Medium (P2)

### P2-1 — `_HTTPX_CLIENT` singleton 永不關閉 + `_shutdown_requested` 純 global,subprocess 唔知
- `core/llm.py:18` 全局 client,`_on_shutdown` set flag,但 subprocess(task_manager spawn)有獨立 memory space,parent set flag **subprocess 完全唔知** → cancel 靠 SIGTERM(task_manager.py:75)而非 flag。兩套 shutdown 機制不一致。

### P2-2 — `permission.py` auto-approve `~/.baw/*` 但 delegate_task 寫 config 繞過 PermissionEngine
- `delegate_task` 嘅 sub-agent 用 `execute_tool` 直接行 write_file,**冇行 PermissionEngine.check()**(loop.py 先有 permission gate)。sub-agent 可以無限制寫任何路徑。P2 報告講 path traversal 喺 permission engine,但**冇人發現 sub-agent 根本 bypass 咗成個 engine**。

### P2-3 — `CostTracker._TRACKER` 係 module-global singleton,跨 session 累加
- `core/loop.py` `_TRACKER` global。Telegram bot 一個 process 服務多個 user,**所有 user 嘅 cost / token 共用一個 tracker**,context_window % 顯示亂晒。`reset()` 仲有 bug:set `self.total` 但 record 用 `self.total_tokens_in`(field 名唔 match,reset 後 summary 即 crash 或 唔清)。

### P2-4 — `set_context_window` 用 module-global `_CONTEXT_TRACKER`,多 session 互相覆寫
- 同 P2-3 同類: 全局單例喺多 user/多 session 環境下 state 污染。Telegram + CLI 共用 import 時尤甚。

### P2-5 — `router_cmd.py` 寫 config 用 `yaml.dump` 重寫成個 file
- `cli/commands/router_cmd.py:50` `_save_config` 整個 dump → **comment 全失、字段順序亂、`config.yaml` 嘅大量註解(plan 說明等)永久消失**。下次用戶開 config 見唔到任何指引。

---

## Low (P3)

### P3-1 — `diagnostics.py:88` memory 計數寫死 `print("Entries: ?")`,從未 implement
### P3-2 — `search.py:_auto_discover()` 註解話 "called explicitly during startup",但 grep 冇人 call → DuckDuckGo provider 可能從未註冊,web_search 靜默 fail
### P3-3 — `tui_chat.py:_key()` 揀 provider 邏輯同 `chat.py:_resolve_provider()` 唔同(一個靠 model.provider 字段,一個 scan models),config 冇 `model.provider` 字段時 TUI 會揀錯 provider key
### P3-4 — `verifier.py:74` `get_model()` 攞咗但從未用(dead var),且 verify 失敗時 `passed: True`(fail-open),靜默放行錯誤結果

---

## Architecture diagram(實際 vs 文檔聲稱)

```
文檔聲稱 (BAW_AUDIT_COURT.md):
  msg → route_task() → tier → pick_model_for_tier() → delegate → verify → resp

實際 runtime:
  ┌─ CLI chat ─────────────────────────────────────────┐
  │ chat.py._cfg() [merge repo+~/.baw]                   │
  │   → OpenAI client 直連 → 5-turn tool loop            │
  │   (router.py 完全冇 import, court 冇, verifier 冇)    │
  └──────────────────────────────────────────────────────┘

  ┌─ loop.py run_agent (Telegram path) ────────────────┐
  │ load_config [~/.baw only]                           │
  │   → court(adversarial) → plan → execute → verify    │
  │   → (可能) execute_tool delegate_task               │
  └──────────────────────────────────────────────────────┘
                         │
                         ▼ (新 process / 同 process)
  ┌─ delegate_task ────────────────────────────────────┐
  │ _get_minimax_config [~/.baw only]                   │
  │   → _clear() global registry ⚠️                     │
  │   → re-register 6 tools (主 agent tool 蒸發)        │
  │   → task_rules 揀 model (router decision 被丟)      │
  │   → 12-turn 裸 loop (無 checkpoint/verify/permission)│
  └──────────────────────────────────────────────────────┘

  router.py ──────── 孤島,只被 router_cmd.py (CLI) 讀寫
                     runtime 從不執行 route_task()
```

**核心發現**: router.py、court、verifier、checkpoint 四個 module 喺**主執行路徑上互相唔接駁**。每個子代理審單一 module 都話「呢個 module OK / 有小問題」,但**冇人發現佢哋根本冇連起嚟**。

---

## Hidden coupling & state propagation

| 隱藏耦合 | 機制 | 後果 |
|---------|------|------|
| `core.tools._tools` global | delegate `_clear()` | sub-agent 抹走主 agent registry (P1-2) |
| `_TRACKER` / `_CONTEXT_TRACKER` / `_CIRCUIT_STATE` / `_FALLBACK_LOG` 全 module-global | import-time singleton | 多 user/session 跨污染 (P2-3,4) |
| `signal.signal` import-time | 副作用 | 非 main-thread import 即炸 (P1-5) |
| 三個 `load_config` 變體 | 各自 read 唔同 path | task_rules/executor 喺 delegate 失效 (P1-3) |
| `model.fallback` 缺失 | config drift | fallback resolve 返自己 (P0-2) |
| router decision 同 delegate 模型選擇 | 兩套獨立邏輯 | tier_preferences 對 sub-agent 零效 (P0-1) |
| sub-agent 繞過 PermissionEngine | delegate 直 call execute_tool | 無權限管制寫檔 (P2-2) |

---

## Recommended fix sequence(細粒度,依賴順序)

1. **P1-5** 先修(最易炸 import):`signal.signal` 包 try/except ValueError。
2. **P0-2**:`config.yaml` 補 `model.fallback: deepseek-reasoner`。
3. **P1-3**:抽一個 `core/config.py:load_config()` 單一來源,三個入口全部改用。
4. **P0-3**:`DEFAULT_TIER_PREFERENCES` 改為由 providers 動態 derive(或 config.yaml 補真實 tier_preferences)。
5. **P1-2**:delegate_task 改用 save/restore registry(或獨立 instance),停止掂 global。
6. **P0-1**:`delegate_task(model_id=...)` 加參數,接收 caller 模型決定。
7. **P1-1**:loop.py 入口接駁 `route_task()`,真正使用 tier routing;否則刪 router.py。
8. **P1-4**:delegate loop 加 `verify_step()` + retry。
9. **P2-2**:sub-agent execute_tool 前過 PermissionEngine。
10. **P2-3/P2-4**:tracker 改 per-session instance(由 caller 傳入,唔好 module-global)。修 `reset()` field 名 bug。
11. **P2-5**:`router_cmd._save_config` 改用 ruamel.yaml 保留 comment。
12. **P3** 收尾:diagnostics 計數、search auto_discover 接駁、verifier fail-open 改 fail-closed。

---

**一句總結**: 三個子代理各自審一塊牆都話砌得好,但**冇人企遠啲睇 — 啲牆之間根本冇灰泥**。router/court/verifier/checkpoint 喺 runtime 主路徑上係斷開嘅,而 config 有三套讀法、tracker 有跨 session 污染、global registry 會被 sub-agent 抹走。呢啲全部係**接縫問題**,單 module 審計永遠睇唔到。