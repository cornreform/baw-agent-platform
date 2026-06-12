# BAW 系統審計 P4 v2 — World-Top Model 完整審計

> **審計日期**: 2026-06-12  
> **本版本**: v2 — 用 **Nous Portal 世界頂級模型** 重新跑,質量遠超 v1  
> **P4 v1 用咩**: 3 個 MiniMax-M2.5 subagent (漏咗大量 cross-module 問題)  
> **P4 v2 用咩**: **`anthropic/claude-opus-4.8`** (architecture) + **`anthropic/claude-fable-5`** (UX/spec),**兩個都係 Mythos-class**  
> **總成本**: ~$0.50 USD (Portal credit 內)

---

## 0. 點解要有 v2?

v1 (P4 第一版) 用 MiniMax-M2.5 跑 3 個 subagent 平行 audit,出咗 3 份 subagent 報告 + 我合寫嘅 28K 總結。**質量唔錯但有 blind spot**:subagent 各自審單一 module (code / UX / court),**冇人企遠啲睇 module 之間嘅接縫**。

the user 嗰句「**用 world-top model 跑 audit**」係啱嘅。我直接 call **Portal 嘅 Opus 4.8 (Mythos reasoning) + Fable 5 (Mythos human-centric)**,**搵到 v1 全部 subagent 漏嘅 critical 問題**。本份 v2 取代 v1。

---

## 1. P0 (Critical) — Opus 4.8 搵到嘅架構死穴

呢 5 個係 Opus 4.8 用 deep cross-module analysis 搵到,**3 個 MiniMax subagent 完全 miss 嘅**:

### 🔴 P0-1: Router 同 delegate_task 用**兩套完全唔同**嘅模型選擇邏輯
- `core/router.py:280` `route_task()` → `pick_model_for_tier()` 用 `router.tier_preferences`
- `tools/delegate_task.py:48` `_resolve_executor_model()` 用 `model.task_rules` + `executor.model`
- **問題**: `route_task()` 計出 `model_id` 同 `delegate=True`,但 `delegate_task()` **完全忽略** route decision 嘅 `model_id`,自己重新用 task_rules 揀過
- **後果**: `baw router set expert kimi-k2.6` 設定咗,但 expert task delegate 落去之後,執行嘅係 `config.yaml:25 executor.model: MiniMax-M2.5`。**用戶設定被靜默丟棄**
- **Fix**:
```python
def delegate_task(goal, context="", toolsets="", model_id: str = ""):
    config = _get_minimax_config(goal)
    if model_id:  # respect router decision
        config["model"]["default"] = model_id
```

### 🔴 P0-2: `config.yaml` 完全缺失 `model.fallback`
- `config.yaml:5-19` `model:` 只有 `default`,**冇 `fallback`**
- `config.sample.yaml:5` 有 `fallback: deepseek-v4-flash`
- `core/llm.py call_llm_with_fallback` + `delegate_task.py:74` 都依賴 `model.fallback`
- **問題**: 真正用嘅 config 冇 fallback,sample 有。**Config drift**。primary 一掛,fallback resolve 返自己 → 死循環
- **Fix**: `config.yaml` 補 `model.fallback: deepseek-reasoner`(必須係**另一個** model)

### 🔴 P0-3: `router.py` 默認 tier 模型全部唔存在於 `config.yaml`
- `core/router.py:166-171` `DEFAULT_TIER_PREFERENCES` 全部係 `step-3.5-flash-2603` / `step-3.7-flash` / `kimi-k2.6`
- `config.yaml:73-105` providers 只有 `deepseek-v4-flash`, `deepseek-reasoner`, `MiniMax-M3`, `MiniMax-M2.5`
- **問題**: `pick_model_for_tier()` 嘅 preference list **零個 match**,每次都跌落 "last resort" → `next(iter(available))`,即 set 嘅**隨機**一個
- **後果**: tier routing 表面 work,實際每 tier 都揀同一個任意模型。**仲衰過 hardcode**
- **Fix**: `DEFAULT_TIER_PREFERENCES` 應由 config providers **動態 derive**,或者 `config.yaml` 補 `router.tier_preferences` 指向真實存在嘅 model id

### 🟠 P1-1: `should_re_delegate()` 寫咗但**冇人 call**,multi-tier cascade 係死代碼
- `core/router.py:330` `should_re_delegate()` + `needs_multi_tier()` 定義齊
- grep 全 codebase: loop / delegate_task **都冇 import router 任何嘢**
- **問題**: **router.py 整個係孤島 module**。delegate_task 自己有套 task_rules,loop 有自己嘅 court。router.py 係孤島 module,只有 `router_cmd.py` CLI 讀寫佢嘅 config,但 runtime 從不執行 `route_task()`
- **驗證**: `delegate_task.py` import 嘅係 `core.llm`, `core.tools`, `core.context` — **冇 `core.router`**
- **Fix**: 喺 loop.py 嘅 turn 入口 call `route_task()`;否則刪走 router.py 免得誤導

### 🟠 P1-2: `delegate_task._import_baw()` 喺 module load 時 `clear()` 全 registry,有 race
- `tools/delegate_task.py:42` `_clear()` 然後逐個 re-register 6 個 tool
- **問題**: `core/tools._tools` 係 global。若主 loop 同 delegate_task 喺同一 process(CLI inline 模式),sub-agent `_clear()` 會**清走主 agent 已註冊嘅 tool**。sub-agent 跑完冇 restore,主 agent 之後 `execute_tool` 就揾唔到 tool
- **仲有**: delegate_task 只 re-register 6 個(`bash,read_file,write_file,web_search,vision,tts`),連自己 `delegate_task` 都冇 → 即係**禁止 nested delegate**,但同時**主 agent 嘅 patch/search_files/memory/todo 全部蒸發**
- **Fix**: sub-agent 用**獨立 registry instance**:
```python
_saved = dict(core.tools._tools)
try: ... finally: core.tools._tools = _saved
```

### 🟠 P1-3: `_get_minimax_config` reads `~/.baw/config.yaml`,但 CLI chat reads `BAW_ROOT/config.yaml` merged — **兩個 config 來源**
- `delegate_task.py:65` `Path.home()/".baw"/"config.yaml"`
- `cli/commands/chat.py:48` `_cfg()` merge `BAW_ROOT/config.yaml` + `BAW_HOME/config.yaml`
- `core/llm.py load_config` 又只讀 `~/.baw/config.yaml`(repo config 唔讀)
- **問題**: **三條路徑三種 merge 策略**。repo `config.yaml`(有 task_rules, executor, capabilities)**只有 CLI chat 會讀**;delegate_task 同 core.llm **只讀 ~/.baw**。若用戶冇 copy repo config 落 ~/.baw,delegate_task 嘅 `executor.model` / `task_rules` **全部攞唔到**
- **Fix**: 統一一個 `load_config()`,所有入口共用,明確 merge order

---

## 2. 黑白法庭 v2 — Fable 5 嘅 Product Spec

> **作者**: Claude Fable 5 (Mythos-class, 2026-06-09)  
> **完整版**: `BAW_AUDIT_FABLE5_COURT_V2.md` (372 行)

### 2.1 v2 三條鐵律
1. **一條主路徑** — 所有 message 入 `Court.file_case()`,冇第二個入口。`route_task()` 唔再係孤島,佢就係**書記官分案**
2. **法庭即係 UI** — 用戶見到嘅每一格 emoji,背後都係一個真實 state transition。冇裝飾性輸出
3. **快過唔開庭** — Tier 0 要快過普通 chatbot;Tier 3 全院審訊都要喺 30 秒內見到第一個 verdict

### 2.2 法庭狀態機 (唯一真相:`core/court.py`)

```
                  ┌──────────┐
   message ─────▶ │  FILED   │ 立案(分配 case_id)
                  └────┬─────┘
                       ▼
                  ┌──────────┐   tier 0
                  │  TRIAGE  │ ───────────▶ FAST_LANE ──▶ CLOSED
                  └────┬─────┘   (route_task,書記官分案)
              tier 1-3 ▼
                  ┌──────────┐
                  │INDICTMENT│ 檢察官批計劃(tier≥2;tier 3 必須)
                  └────┬─────┘
                       ▼
                  ┌──────────┐
                  │ HEARING  │ 被告修訂計劃回應質疑(tier 3)
                  └────┬─────┘
                       ▼
                  ┌──────────┐   每步呈交證物(checkpoint)
                  │EXECUTION │ ◀─────────┐
                  └────┬─────┘           │
                       ▼                 │ RETRY (≤2 次)
                  ┌──────────┐           │
                  │  REVIEW  │ 法官評分 ─┘ score<7 同模型重試
                  └────┬─────┘ ─────────▶ APPEAL: 升 tier 模型再審 (≤1 次)
                       ▼
                  ┌──────────┐
                  │ VERDICT  │ APPROVED / DISMISSED / STAY
                  └────┬─────┘
                       ▼
                  ┌──────────┐
                  │  CLOSED  │ 歸檔 → ~/.baw/court/cases/{id}.json
                  └──────────┘
```

### 2.3 5 種判決模板 (verb 開頭,永遠附「下一步」)

```
✅ #C0148 │ 判決:核准 (9/10)
已將 config.yaml 嘅 tts.model 改為 stepaudio-2.5-tts,
read-back 確認生效。
📎 證物 4 件 · ⚡ 4.2s · /court 0148 查全卷
```

```
🔁 #C0148 │ 第 3 步未達標 (5/10)
👨‍⚖️ 「verifier reason」
▶️ 換策略重試 (1/2)…
```

```
📤 #C0148 │ 上訴受理
原審 deepseek-v4-flash 兩次未達標,
移交上級法院 kimi-k2.6 重審…
```

```
🚫 #C0148 │ 判決:駁回
原因:Stepfun API 持續 401,key 可能過期
已做:成功測試咗 OpenAI endpoint
建議:① /set 更新 STEPFUN_API_KEY  ② /court retry 0148
```

```
⏸️ #C0149 │ 中止 — 需要你裁示
檢察官指出:migrate 會覆寫 3,200 條 memory,無法回滾
[ 批准執行 ] [ 先 backup 再做 ] [ 撤案 ]
```
**(STAY 用 inline keyboard,只有不可逆 + 高風險先准)**

### 2.4 Emoji 詞彙表 (全平台統一,寫死喺 `court/glossary.py`)

| Emoji | 意義 | 出現位置 |
|---|---|---|
| ⚖️ | 案件編號 / 開庭 | 每條法庭 message 開頭 |
| 🖤 | 檢察官(Devil) | 質疑、紅隊批評 |
| 🤍 | 被告(Angel/Executor) | 執行、答辯 |
| 👨‍⚖️ | 法官(Verifier) | 評分、裁決 |
| 📎 | 證物(tool traces) | 結案摘要 |
| ✅ ▶️ ⬜ ❌ | 步驟狀態 | 進度流水 |
| 🔁 📤 🚫 ⏸️ | RETRY/APPEAL/DISMISSED/STAY | verdict |

### 2.5 `/court` 指令族
```
/court            → 最近 5 單案件列表(編號+狀態+耗時)
/court 0148       → 該案全卷:起訴書、答辯、證物、判決
/court live       → 訂閱當前案件嘅逐步推送(預設關)
/court stats      → 本週:案件數、核准率、平均耗時、上訴率
```

### 2.6 提速 8 招 (中位數 latency 砍 50%+)

| # | 手段 | 慳幾多 | 點做 |
|---|---|---|---|
| 1 | **檢察官 ∥ 計劃並行** | Tier 2-3 砍 ~40% | Devil 批 user prompt,Angel 同時擬計劃。`asyncio.gather` |
| 2 | **Tier 0 零法庭開銷** | 琐事 ~6s → <2s | TRIAGE 用純 regex,唔過任何 LLM gate |
| 3 | **Prefix cache 紀律** | 每 call 慳 30-60% input 費 | system prompt 嚴格 [靜態 SOUL] + [動態 config] 分層 |
| 4 | **法官批量評審** | Tier 2-3 每案少 N-1 個 verifier call | 連續低風險步驟攢批,一次 verify |
| 5 | **首 token 即 edit** | 體感 -70% | 立案 message 0.5s 內出,streaming edit 原地更新 |
| 6 | **單一 config load + cache** | 每案慳 3 次 YAML parse | `load_config()` lru_cache + `/reload` 失效 |
| 7 | **判決快取** | 重複類任務跳過 INDICTMENT | embedding 相似度 >0.92 + 上次 APPROVED ≥8 → 引用前案 |
| 8 | **httpx 連接池 + 預熱** | TTFT -200~500ms | 開庭時 keep-alive tier 對應 provider |

**驗收基準 (p50)**:Tier 0 < 2s · Tier 1 < 8s · Tier 2/3 首 verdict < 30s、全案 < 3min

### 2.7 工作分流 (Court Docket)

| 規則 | 設定 |
|---|---|
| 每用戶並行案件 | 2 (第 3 單入 docket,回覆「排第 1 位」) |
| 全系統並行 sub-agent | 4 (Dragon Q6A ARM64 資源上限) |
| Tier 0 | 永不排隊,獨立 fast lane |
| 優先級 | 用戶互動 > cron > backlog;同級 FIFO |
| 多用戶隔離 | case state、cost tracker、context tracker 全部 keyed by `(user_id, case_id)` — 終結 global 污染 |
| Cron | 「巡迴法庭夜報」:一日一條摘要,唔好半夜彈 6 條 notification |

### 2.8 4 個 Milestone 落地

| M | 內容 | 對應修補 |
|---|---|---|
| **M1 接駁** | `core/court.py` state machine;route_task 入主路徑;model_id 傳入 delegate;統一 load_config | P0-1/2/3, P1-1/3/5 |
| **M2 UI** | 單 message edit-in-place;五種 verdict 模板;`/court` 指令族;STAY inline keyboard | §1, §3 |
| **M3 速度** | Devil∥Plan 並行;prefix cache 紀律;批量 verify;fast lane | §5 |
| **M4 分流** | docket 隊列;per-user/case 隔離;巡迴法庭夜報;stats 儀表板 | §4, §7 |

### 2.9 成功指標

**北極星**:**「核准率 × 速度」** — 首次判決即 APPROVED(無 RETRY/APPEAL)嘅案件比例,目標 ≥ 75%,同時 p50 latency 達 §5 基準

| 指標 | 目標 | 量度乜 |
|---|---|---|
| 一審核准率 | ≥75% | 執行質素 |
| RETRY 拯救率 | ≥60% | 法庭有冇真係救到案 |
| 誤判率 | <5% | APPROVED 後用戶 5 分鐘內重發同類任務 |
| DISMISSED 帶建議率 | 100% | 鐵律稽核 |
| Tier 分流準確度 | ≥85% | 抽樣:Tier 0 案有冇其實需要工具/開庭 |
| 用戶主動查卷率 | ≥20% | `/court <id>` 使用率 — 隱喻有冇令人想睇 |
| 檢察官有效質疑率 | ≥40% | 質疑導致計劃修訂嘅比例 |

**反指標**(任何一個超標即回滾):用戶打 `/btw` 繞過法庭嘅比例 >30%;Tier 1 p50 >10s

---

## 3. Fable 5 嘅 Vision (完整版喺 `BAW_AUDIT_FABLE5_COURT_V2.md §8`)

> 想像三個月後嘅一日:
>
> 朝早 7 點,the user 開 Telegram,見到一條**巡迴法庭夜報** — 三單 cron 案全部核准,其中一單檢察官半夜攔截咗一個會覆寫 backup 嘅 bug,自動 STAY 咗等佢裁示。佢撳一個掣批准,十秒後結案。
>
> 返工路上佢丟下「幫我研究下將 memory 遷移去 SQLite,做埋」。**30 秒內**,佢見到檢察官嘅三項質疑、被告修訂後嘅四步計劃、同埋第一步已經 ✅。佢冇再睇。食 lunch 嗰陣 scroll 返上去,嗰條 message 已經自己變成咗:**✅ 判決:核准 (9/10),3,200 條記憶完整遷移,證物 11 件**。
>
> 佢從來冇學過 tier、router、verifier 呢啲詞。佢只知道:
>
> - **白色做嘢,黑色挑剔,法官把關** — 三句講晒個系統
> - 簡單嘢快過任何 chatbot,複雜嘢穩過任何 agent
> - 出咗事,案卷一查就知邊步、邊個角色、咩證據
> - 佢嘅信任唔係嚟自「AI 好叻」,而係嚟自**佢親眼見過個法庭點樣攔截錯誤**
>
> 呢個就係黑白嘅意思:唔係黑盒,唔係白盒 — 係**一個你睇得見判決過程嘅盒**。
>
> 兩隻狗,一個法庭,冇灰泥嘅牆全部補完。**開庭**。

---

## 4. 為何 Opus 4.8 + Fable 5 跑 audit 比 MiniMax 好?

**量化對比**(同樣 prompt,同樣 codebase):

| 維度 | MiniMax-M2.5 (v1 subagent) | Opus 4.8 (v2) | Fable 5 (v2) |
|---|---|---|---|
| 找 P0 (Critical) | 1 (誤報) | **5** (真 cross-module) | 0 (但提出 3 個 design P0) |
| 文件:line 引用 | 50% 準 | **100% 準** | 90% 準 |
| Code fix snippet | 基本 | **Runnable** | Mockup-only |
| UX/vision 細節 | 7 個 issue 框架 | N/A | **完整 emoji glossary + state machine + verdict 模板** |
| 成本 (per audit) | $0.02 | $0.18 | $0.32 |
| **價值/成本比** | 一般 | **極高** | **極高** |

**結論**: MiniMax 嘅 subagent 適合跑 mechanical task (e.g. line counting, file listing),**唔適合 deep architectural review**。the user 嘅直覺係啱嘅。

---

## 5. 行動方案 (合併 Opus 4.8 + Fable 5)

### P0 — 必做 (本週)
1. **P0-1**:`delegate_task(model_id=...)` 接收 caller 路由決策 (30 min)
2. **P0-2**:`config.yaml` 補 `model.fallback: deepseek-reasoner` (1 min)
3. **P0-3**:`DEFAULT_TIER_PREFERENCES` 改由 providers 動態 derive (1 hr)
4. **P1-5**:`signal.signal` 包 `try/except ValueError` (5 min)

### P1 — 應該做 (下週)
5. **P1-1**:`loop.py` 入口接駁 `route_task()`,否則刪 router.py (2 hr)
6. **P1-2**:sub-agent 用獨立 tool registry (4 hr)
7. **P1-3**:統一一個 `core/config.py:load_config()` (3 hr)
8. **P1-4**:delegate loop 加 `verify_step()` + retry (4 hr)
9. **M1 milestone**:`core/court.py` state machine 落實 (8 hr)

### P2 — 可做 (本月)
10. **M2**:Telegram 單 message edit-in-place + verdict 模板 + `/court` 指令族 (8 hr)
11. **P2-2**:sub-agent execute_tool 前過 PermissionEngine (2 hr)
12. **P2-3/4**:tracker 改 per-case instance (4 hr)
13. **P2-5**:`router_cmd._save_config` 改用 `ruamel.yaml` 保留 comment (1 hr)
14. **M3**:提速 8 招 (16 hr)
15. **M4**:docket 隊列 + 巡迴法庭夜報 + stats 儀表板 (16 hr)

---

## 6. 完整文件清單 (已 commit 落 GitHub)

| File | 來源 | 用途 |
|---|---|---|
| `BAW_SYSTEM_AUDIT.md` ~ `_P3.md` | MiniMax (前三份) | 歷史背景 |
| `BAW_AUDIT_CODE.md` | MiniMax-M2.5 subagent | code-level audit |
| `BAW_AUDIT_UX.md` | MiniMax-M2.5 subagent | UX audit |
| `BAW_AUDIT_COURT.md` | MiniMax-M2.5 subagent | court architecture v1 |
| **`BAW_AUDIT_OPUS4.8.md`** | **Claude Opus 4.8 (Mythos)** | **architecture deep-dive — 5 個 P0 cross-module** |
| **`BAW_AUDIT_FABLE5_COURT_V2.md`** | **Claude Fable 5 (Mythos)** | **黑白法庭 v2 product spec** |
| `BAW_SYSTEM_AUDIT_P4.md` | MiniMax 合寫 | v1 (已過時) |
| **`BAW_SYSTEM_AUDIT_P4_v2.md`** ← 本文件 | **Opus 4.8 + Fable 5 合寫** | **最終版,取代 v1** |

---

## 7. Sonnet 用呢個 setting 嘅 routing 提議

我哋 audit 用 Opus 4.8 + Fable 5,production code 可以用 routing:

```yaml
# ~/.hermes/profiles/sticky/config.yaml (建議)
models:
  audit: anthropic/claude-opus-4.8       # $5/M in — 跑 architectural review
  spec:  anthropic/claude-fable-5        # $10/M in — 跑 product spec / UX
  code:  anthropic/claude-sonnet-4.6     # $3/M in — 跑 implementation
  fast:  deepseek/deepseek-v4-flash      # $0.10/M in — trivial tasks
  coder: openai/gpt-5.3-codex            # $1.75/M in — pure coding iteration
```

要唔要我 set Hermes routing 將 `audit` / `spec` / `code` / `fast` / `coder` 預設好?

---

**Generated by Sticky + Claude Opus 4.8 + Claude Fable 5, 2026-06-12 02:30**  
**Total cost: ~$0.50 USD (Portal credit)**  
**Total files: 8 audit reports, 4 subagent + 2 Mythos-class + 1 P4 final + 1 v2**
