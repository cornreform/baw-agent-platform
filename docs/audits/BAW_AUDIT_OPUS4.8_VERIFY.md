## ✅ CORRECTLY FIXED

- **P0-2 (config.fallback)** — `fallback: deepseek-reasoner` 同 `default: deepseek-v4-flash` 唔同,死循環確實解除。直接、正確。
- **P0-3 (DEFAULT_TIER_PREFERENCES)** — 新 list 全部係真實存在 provider (`deepseek-v4-flash`/`deepseek-reasoner`/`MiniMax-M3`/`MiniMax-M2.5`)。`pick_model_for_tier()` 唔再全跌落 random last-resort。正確。
- **P1-5 (signal try/except)** — 包咗 `(ValueError, OSError)`,仲考慮埋 Windows。non-main-thread import 唔再炸。完全符合原 audit 建議。
- **P0-1 (delegate_task model_id 參數)** — 簽名 + `_get_minimax_config(model_override=)` 接駁正確,`model_override` 優先於 `_resolve_executor_model`。route decision 唔再被靜默丟棄。**API 層面**正確。

## ⚠️ PARTIALLY FIXED / BUGGY

- **P1-1 (loop 傳 model_id) — 接駁咗,但係接駁咗一條我從未驗證過存在嘅路徑。**
  原 P1-1 嘅根本問題係 **`route_task()` 喺 runtime 從不被 call**(router.py 係孤島)。今次 fix 只係喺 `_run_step()` 加 `model_id=model_id or ""`,假設 `model_id` 喺 scope 入面已經係 route decision 嘅結果。但 diff **冇任何地方顯示 `route_task()` 被接入 loop**。`model_id` 喺 `loop.py:1301` 嗰個 closure 入面究竟係邊度嚟?如果佢唔係 `RouteDecision.model_id`,呢個 fix 就係**接咗一條死線**——tier_preferences 對 sub-agent 仍然零效。
  **必須驗證 `model_id` 變數嘅來源。** 我評為 partial,因為 P1-1 嘅核心(接駁 router)在 diff 中睇唔到完成。

- **P1-2 (registry save/restore) — 機制有嚴重時序漏洞。**
  - `_import_baw()` 入面 save → `setattr(_core_tools_mod, "_pending_restore", ...)`,restore 喺 `delegate_task()` 嘅 `finally`。**save 同 restore 跨咗兩個函數，靠一個 module-global `_pending_restore` 傳遞。** 呢個本身就係 audit 一直批評嘅 module-global 反模式,而且：
  - **Reentrancy 全爆**：若 `_import_baw()` 被連續 call 兩次(或 nested),第二次 save 會用**已經被清空嘅 registry**(只剩 6 個 sub-agent tools)覆寫 `_pending_restore`,第一次嘅 15+ tools 永久丟失。原 audit P1-2 明確講過要防 nested。
  - **`_registry.clear()` vs 重新綁定**：fix 用 `_registry.clear(); _registry.update(...)` 改 in-place,假設 `core.tools` 內部所有人都 hold **同一個 dict object**。若任何地方做過 `_tools = {...}`(rebind),呢個 restore 會寫去一個 orphan dict。需要驗證 `core.tools` 從不 rebind。
  - **`getattr(..., "_tools") or getattr(..., "_TOOLS")`**：`or` 對**空 dict** 會 fall through!如果 registry 真係空(`{}`),`{} or getattr(...)` → 去攞 `_TOOLS`,攞唔到變 `None` → fallback `{}`,save 一個假嘅空 registry。應該用 `is not None` 判斷,唔好用 truthiness。**呢個係實 bug。**

- **P1-4 (verify_step in sub-loop) — 邏輯接咗但「retry」係假 retry。**
  - score<7 只係 `ctx.add_user("...try different approach")`,然後**繼續行下一個 tool call**。佢冇 re-run 失敗嗰步,只係加咗個 hint 落 context,寄望 LLM 下一 iteration 自己改。叫「retry hint」尚可,但 commit message 寫「triggers a retry」係 overstate。
  - **`verify_step` 簽名假設未驗證**:fix call `verify_step(goal=, tool_name=, tool_args=, tool_result=, config=)`。但原 audit P3-4 同 court 骨架顯示 verify_step 簽名係 `verify_step(goal, tool_result, config, model_id)` —— **冇 `tool_name`/`tool_args` 參數**。若實際簽名唔收呢兩個 kwarg,每次 call 都 `TypeError`,被 `except Exception: pass` 靜默吞掉 → **verify 100% 靜默失效**,P1-4 等於冇做但測試唔出。**高危,必須核對 verifier.py 簽名。**
  - **fail-open 未修**:原 audit P3-4 指出 verifier fail 時 `passed:True`。今次 `score = int(verdict.get("score", 7))` default 7 = 剛好過關 → 延續 fail-open 哲學。verifier 壞 = 靜默放行。

## ❌ NOT FIXED (claimed but actually broken)

- 暫無「聲稱修咗但完全冇 code」嘅項目;但 **P1-1 同 P1-4 因上述未驗證簽名/來源,有實質風險係 effectively not fixed**。需要 runtime 驗證先能定案。

## 🔍 NEW ISSUES INTRODUCED

- **NEW-1 [HIGH] `_pending_restore` module-global 引入新跨 session 污染。** 喺 Telegram 多 user 同 process(原 audit P2-3/P2-4 已警告嘅環境),兩個 user 同時 delegate → 兩次 `setattr(_pending_restore)` 互相覆寫,registry restore 攞錯 snapshot。呢個 fix **用一個 global 去補另一個 global 嘅鑊**,加深咗 audit 一直批評嘅 singleton 污染問題。
- **NEW-2 [MED] empty-registry truthiness bug**(見 P1-2):`_tools` 為空 dict 時 save 失效。
- **NEW-3 [LOW] `_verify_retries` 跨 iteration 共用,非 per-step。** Comment 寫「per-iteration verify_step retries」但變數喺 loop 外初始化 → 一旦累積到 2,**之後所有 tool 都唔再 verify**。即長 task 後半段完全冇 judge。語意同 comment 矛盾。

## 🟡 DEFERRED ITEMS ASSESSMENT

- **P1-3 (unified load_config) 延後 — 不可接受,且直接威脅 P0-1。**
  原 audit 明確指出 `delegate_task._get_minimax_config` 讀 `~/.baw/config.yaml`,而 CLI chat merge `repo + ~/.baw`。**P0-1 嘅 `model_override` 雖然繞過咗 task_rules,但 `_get_minimax_config` 之後仲要做 "verify executor model exists in providers"**(diff 中 `providers = cfg.get("providers", {})`)。若用戶 `~/.baw/config.yaml` 嘅 providers 同 repo 唔同步,router 傳落嚟嘅 `model_id` 可能**唔喺 ~/.baw providers list** → 被 reject fallback。**即 P1-3 唔修,P0-1 嘅 fix 喺 config drift 下會被消解。** 兩者有依賴關係,commit message 聲稱「P0-2/3 changes don't break them」忽略咗 P0-1 對 config 一致性嘅依賴。延後理由不成立。
- **完全冇提及 P1-1 嘅另一半(接駁 route_task)**:commit 講 P1-1 done,但只做咗「傳 model_id」,**冇做「runtime call route_task()」**。原 audit P1-1 兩部分缺一不可(否則 model_id 從何而來?)。呢個係**隱性 deferred 但聲稱 done**。

## 📊 OVERALL VERDICT

- **Conditional Pass**
- **Confidence: medium**(三個 fix 嘅正確性取決於 diff 外嘅 `verify_step` 簽名同 `model_id` 來源,無法單憑 diff 確認)
- **Top 3 things to fix next:**
  1. **核對 `verify_step` 真實簽名** — 若唔收 `tool_name`/`tool_args`,P1-4 全程 `TypeError` 被靜默吞,即假修。同時改 `except Exception: pass` 至少 log 一次,杜絕靜默失效。
  2. **拆走 `_pending_restore` module-global** — 改用 context manager / 直接喺 `delegate_task` body 內 save→try→finally restore,唔好跨函數靠 global 傳 snapshot(reentrancy + 多 user 污染)。同時修 `or` truthiness bug 改 `is not None`。
  3. **確認並完成 P1-1 後半** — 證明 `loop.py:1301` 嘅 `model_id` 確實來自 `route_task()`/`RouteDecision`;若否,接駁 router,否則整個 tier_preferences 鏈仍然係死代碼。並重新評估 P1-3 延後決定(因 P0-1 依賴 config 一致性)。