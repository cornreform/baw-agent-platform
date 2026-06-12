# ⚖️ 黑白法庭 v2 — Product Spec

> **作者視角**:Sunny / BAW Product
> **狀態**:Design Final — 直接落 backlog
> **一句話**:用戶唔需要知道 router、verifier、delegate_task 點接駁。用戶只需要見到一場**睇得明、行得快、判得準**嘅審訊。

---

## 0. 點解要 v2

Opus 4.8 審計講得最狠嗰句:「**啲牆之間根本冇灰泥**」。router 係孤島、verifier 冇人 call、delegate 丟咗 routing decision。v1 嘅法庭係**四個演員企喺四個唔同嘅劇院**。

v2 嘅原則只有三條:

1. **一條主路徑** — 所有 message 入 `Court.file_case()`,冇第二個入口。route_task() 唔再係孤島,佢就係**書記官分案**。
2. **法庭即係 UI** — 用戶見到嘅每一格 emoji,背後都係一個真實 state transition。冇裝飾性輸出。
3. **快過唔開庭** — Tier 0 要快過普通 chatbot;Tier 3 全院審訊都要喺 30 秒內見到第一個 verdict。

---

## 1. 用戶見到啲乜 — Telegram Court UI

### 1.1 核心設計:一單案 = 一條會自我更新嘅 message

唔好洗版。立案後 send 一條 message,之後全部用 `editMessageText` 原地更新。用戶 scroll 返上去,見到嘅永遠係**最終狀態**,唔係 20 條進度碎片。

### 1.2 Tier 0(琐事)— 根本唔見到法庭

```
👤 而家幾點?

🖤 而家 14:32,星期四。
```

零儀式。零 emoji 序列。Tier 0 嘅 UX 目標係**令用戶忘記法庭存在**。

### 1.3 Tier 1(普通)— 一行庭審狀態

```
⚖️ #C0147 │ 簡易庭 ▸ 執行中…
```
↓ (原地 edit)
```
✅ #C0147 │ 簡易庭 ▸ 判決:核准 (8/10)

已將 config.yaml 嘅 tts.model 改為 stepaudio-2.5-tts,
read-back 確認生效。

⚡ 4.2s · 1 步 · 法官:deepseek-v4-flash
```

### 1.4 Tier 2(複雜)— 進度條 + 步驟流水

```
⚖️ #C0148 │ 合議庭 ▸ 審理中
━━━━━━━━━━━━━━━━━━━━
📋 案由:研究 Stepfun TTS 並完成配置
🖤 檢察官:已提 2 項質疑(風險:中)
🤍 被告執行中:

  ✅ 1/4 查詢 /v1/models 端點
  ✅ 2/4 篩出 3 個 TTS 模型
  ▶️ 3/4 寫入 config.yaml…
  ⬜ 4/4 生成測試音頻

[██████░░░░] 60% · 18s
```
↓ 結案 edit:
```
✅ #C0148 │ 判決:核准 (9/10)
━━━━━━━━━━━━━━━━━━━━
已配置 stepfun TTS(stepaudio-2.5-tts),測試音頻已生成。

📎 證物:4 個 tool call(/court 0148 查全卷)
⚡ 31s · 法官 9/10 · 檢察官質疑 2 項已全部回應
```

### 1.5 Tier 3(全院)— 完整審訊劇場

```
⚖️ #C0149 │ 全院庭 ▸ 開庭
━━━━━━━━━━━━━━━━━━━━
📋 案由:重構 memory 模組 + migrate 現有數據
🧮 複雜度:10/10 · 預估 3 個 sub-agent

🖤 檢察官陳詞(kimi-k2.6):
  ❗ migrate 前冇 backup → 風險高
  ❗ edges.json schema 未定義 → 計劃不完整
  ⚠️ 建議分階段,每階段 verify

🤍 被告答辯:接納全部 3 項,計劃已修訂
👨‍⚖️ 法官裁示:准予執行,逐步呈交證物
━━━━━━━━━━━━━━━━━━━━
▶️ 開始執行… (4 步,2 個 sub-agent 並行)
```

### 1.6 Emoji 詞彙表(全平台統一,寫死喺 `court/glossary.py`)

| Emoji | 意義 | 出現位置 |
|---|---|---|
| ⚖️ | 案件編號 / 開庭 | 每條法庭 message 開頭 |
| 🖤 | 檢察官(Devil) | 質疑、紅隊批評 |
| 🤍 | 被告(Angel/Executor) | 執行、答辯 |
| 👨‍⚖️ | 法官(Verifier) | 評分、裁決 |
| 📎 | 證物(tool traces) | 結案摘要 |
| ✅ ▶️ ⬜ ❌ | 步驟:完成/進行/未開始/失敗 | 進度流水 |
| 🔁 | 重審(RETRY) | verdict |
| 📤 | 上訴(APPEAL,升級模型) | verdict |
| 🚫 | 駁回(DISMISSED) | verdict |
| ⏸️ | 中止(STAY,等用戶) | verdict |

### 1.7 `/court` 指令族(取代 v1 單一 `/court`)

```
/court            → 最近 5 單案件列表(編號+狀態+耗時)
/court 0148       → 該案全卷:起訴書、答辯、證物、判決
/court live       → 訂閱當前案件嘅逐步推送(預設關)
/court stats      → 本週:案件數、核准率、平均耗時、上訴率
```

---

## 2. 法庭狀態機(Court State Machine)

唯一真相來源:`core/court.py`。所有 UI 只係 render 呢個 enum。

```
                 ┌──────────┐
   message ────▶ │  FILED   │ 立案(分配 case_id)
                 └────┬─────┘
                      ▼
                 ┌──────────┐   tier 0
                 │  TRIAGE  │ ───────────▶ FAST_LANE ──▶ CLOSED
                 └────┬─────┘   (route_task,書記官分案)
              tier 1-3▼
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

**Transition 規則(寫死,唔靠 prompt)**:

| From | To | 條件 |
|---|---|---|
| TRIAGE | FAST_LANE | score ≤ 3 |
| TRIAGE | INDICTMENT | score ≥ 7,或 score 4-6 且涉 side-effect |
| TRIAGE | EXECUTION | score 4-6 純讀取類(跳過檢察官) |
| INDICTMENT | HEARING | 檢察官質疑數 > `warn_threshold` |
| INDICTMENT | EXECUTION | 質疑數 ≤ threshold(質疑附入卷宗) |
| REVIEW | EXECUTION (RETRY) | score < 7 且 retry < 2 |
| REVIEW | EXECUTION (APPEAL) | RETRY 用盡 → 換上一級 tier 模型,appeal < 1 |
| REVIEW | VERDICT/STAY | APPEAL 用盡,或需用戶決定 |
| 任何狀態 | CLOSED | 用戶 `/stop` → 判 DISMISSED(用戶撤訴) |

**修補接縫(對應 Opus 審計)**:
- `route_task()` 嘅 `RouteDecision.model_id` **必須**傳入 `delegate_task(model_id=...)`(修 P0-1)
- `delegate_task` 每步行 `verify_step()`(修 P1-4),sub-agent 用獨立 tool registry(修 P1-2)
- 全部入口統一 `core/config.py:load_config()`(修 P1-3)
- CostTracker / CourtState 改 per-case instance,case_id 為 key(修 P2-3/4)

---

## 3. 判決類型同模板(Verdict Types)

五種判決,wording 統一,**全部包含「下一步可以做乜」**(修 UX Issue 4 死胡同):

### ✅ APPROVED(核准)
```
✅ #C0148 │ 判決:核准 (9/10)
{一句話結果,動詞開頭}
📎 證物 {N} 件 · ⚡ {耗時} · /court 0148 查全卷
```

### 🔁 RETRY(發回重審)— 過程態,用戶見到但唔需要操作
```
🔁 #C0148 │ 第 3 步未達標 (5/10)
👨‍⚖️ 「{verifier reason,一句}」
▶️ 換策略重試 (1/2)…
```

### 📤 APPEAL(上訴)— 升級模型
```
📤 #C0148 │ 上訴受理
原審 deepseek-v4-flash 兩次未達標,
移交上級法院 kimi-k2.6 重審…
```

### 🚫 DISMISSED(駁回)
```
🚫 #C0148 │ 判決:駁回
原因:{具體障礙,如「Stepfun API 持續 401,key 可能過期」}
已做:{1-2 件已完成嘅事}
建議:① /set 更新 API key  ② /court retry 0148 重新立案
```
**鐵律:駁回必附「已做+建議」。永遠唔出裸 error。**

### ⏸️ STAY(中止待示)
```
⏸️ #C0149 │ 中止 — 需要你裁示
檢察官指出:migrate 會覆寫 3,200 條 memory,無法回滾。
[ 批准執行 ] [ 先 backup 再做 ] [ 撤案 ]
```
用 Telegram inline keyboard,三個 callback button。**只有不可逆 + 高風險先准 STAY**,其餘一律自行執行(SOUL 鐵律不變)。

---

## 4. 工作分流(Work Distribution)

### 4.1 案件排程器(Court Docket)

```
~/.baw/court/
├── docket.jsonl        # 排程隊列(append-only)
├── cases/{id}.json     # 案卷歸檔
└── active/             # 進行中 case state(crash 後 /pickup 恢復)
```

| 規則 | 設定 |
|---|---|
| 每用戶並行案件 | 2(第 3 單入 docket,回覆「⚖️ #C0150 已立案,排第 1 位」) |
| 全系統並行 sub-agent | 4(Dragon Q6A ARM64 資源上限) |
| Tier 0 | 永不排隊,獨立 fast lane,即時答 |
| 優先級 | 用戶互動 > cron > backlog;同級 FIFO |
| 多用戶隔離 | case state、cost tracker、context tracker 全部 keyed by `(user_id, case_id)` — 終結 global 污染 |

### 4.2 Cron = 巡迴法庭(Circuit Court)

定時任務以**完整法庭流程**跑,但 verdict 改為「靜默歸檔 + 摘要推送」:

```
🌙 巡迴法庭夜報 (03:00)
✅ #C0151 每日 backup — 核准 (10/10)
✅ #C0152 memory 自整理 — 核准 (8/10),清咗 47 條低分記憶
🚫 #C0153 GitHub CI 檢查 — 駁回 (rate limit),已排 09:00 重審
```

一日一條摘要,唔好半夜彈 6 條 notification。

### 4.3 並行執行(Tier 3)

被告嘅計劃如果有獨立步驟(冇數據依賴),自動 fan-out 俾多個 sub-agent 並行,每個 sub-agent 各自交證物,法官**批量評審**(一次 LLM call 評多步,慳 token 慳時間)。

---

## 5. 提速 — 目標:中位數 latency 砍 50%+

| # | 手段 | 慳幾多 | 點做 |
|---|---|---|---|
| 1 | **檢察官 ∥ 計劃並行** | Tier 2-3 砍 ~40% | Devil 批 user prompt,Angel 同時擬計劃。兩個 LLM call 用 `asyncio.gather`,而唔係 v1 串行 |
| 2 | **Tier 0 零法庭開銷** | 琐事由 ~6s → <2s | TRIAGE 用純 regex(已有 score_complexity),唔過任何 LLM gate;quick-mode SOUL 裁剪到 <1K token |
| 3 | **Prefix cache 紀律** | 每 call 慳 30-60% input 費+TTFT | system prompt 嚴格 [靜態 SOUL] + [動態 config] 分層,靜態段 byte-identical(loop.py 已有雛形,執行到底) |
| 4 | **法官批量評審** | Tier 2-3 每案少 N-1 個 verifier call | 連續低風險步驟攢批,一次 verify;side-effect 步驟即時 verify |
| 5 | **首 token 即 edit** | 體感 -70% | 立案 message 0.5s 內出,之後 streaming edit(throttle 1.5s/次避 Telegram rate limit) |
| 6 | **單一 config load + cache** | 每案慳 3 次磁盤 YAML parse | `load_config()` lru_cache + `/reload` 失效;順手修 P1-3 |
| 7 | **判決快取** | 重複類任務跳過 INDICTMENT | 同類案由(embedding 相似度 >0.92)且上次 APPROVED ≥8 → 檢察官引用前案,唔重新批 |
| 8 | **httpx 連接池複用 + 預熱** | TTFT -200~500ms | 開庭時對 tier 對應 provider 發 keep-alive |

**驗收基準(p50)**:Tier 0 < 2s · Tier 1 < 8s · Tier 2 首 verdict < 30s · Tier 3 首 verdict < 30s、全案 < 3min。

---

## 6. 新用戶 Onboarding — 頭 5 分鐘

### Telegram `/start`(首次)

```
🖤⚪ 歡迎嚟到 BAW 黑白法庭

我唔係普通 chatbot。每個任務喺度都係一單「案件」:
🖤 檢察官先挑剔你嘅任務有咩風險
🤍 被告(執行者)落手做
👨‍⚖️ 法官驗收,唔夠 7 分唔放行

簡單嘢直接答,複雜嘢先開庭 — 你唔使揀,書記官自動分案。

試下打:「幫我查下而家 BTC 價格」
```

用戶第一個 message 完成後,**一次性**附教學尾巴(只出一次,flag 入 user state):

```
💡 啱啱嗰單係 Tier 0 快案,所以冇開庭。
   想睇法庭全力運作?試下俾我一個多步任務,
   或者打 /court stats 睇審判紀錄。
```

### CLI 首次 run(`baw` 偵測冇 `~/.baw/config.yaml`)

```
👋 第一次用 BAW?三步開庭:
 1. baw --setup     → 2 分鐘精靈(API key + Telegram + 模型)
 2. baw --doctor    → 體檢,確認法庭各角色有模型可用
 3. baw "你嘅第一單案"
```

setup 精靈**必須**做嘅 court 專屬一步:檢查 `router.tier_preferences` 入面嘅模型係咪真實存在於 providers(修 P0-3),唔存在就由 providers 按 cost 動態 derive 並寫入 config,**用 ruamel.yaml 保留註解**(修 P2-5)。

---

## 7. 成功指標 — 點知個法庭隱喻 work

### 北極星
> **「核准率 × 速度」**:首次判決即 APPROVED(無 RETRY/APPEAL)嘅案件比例,目標 ≥ 75%,同時 p50 latency 達第 5 節基準。

### 儀表板(`/court stats` + `baw dashboard` 新 panel)

| 指標 | 目標 | 量度乜 |
|---|---|---|
| 一審核准率 | ≥75% | 執行質素 |
| RETRY 拯救率 | ≥60% | RETRY 後最終 APPROVED 嘅比例 — 法庭有冇真係救到案 |
| 誤判率 | <5% | APPROVED 後用戶 5 分鐘內重發同類任務(暗示判錯) |
| DISMISSED 帶建議率 | 100% | 鐵律稽核 |
| Tier 分流準確度 | ≥85% | 抽樣:Tier 0 案有冇其實需要工具/開庭 |
| 用戶主動查卷率 | ≥20% | `/court <id>` 使用率 — 隱喻有冇令人想睇 |
| 檢察官有效質疑率 | ≥40% | 質疑導致計劃修訂嘅比例(低過呢個數 = Devil 流於形式,要換 prompt 或模型) |

**反指標**(任何一個超標即回滾該設計):用戶打 `/btw` 繞過法庭嘅比例 >30%(代表法庭被當成阻力);Tier 1 p50 >10s。

---

## 8. Vision — 完美嘅黑白法庭係咩樣

想像三個月後嘅一日:

朝早 7 點,Sunny 開 Telegram,見到一條巡迴法庭夜報 — 三單 cron 案全部核准,其中一單檢察官半夜攔截咗一個會覆寫 backup 嘅 bug,自動 STAY 咗等佢裁示。佢撳一個掣批准,十秒後結案。

返工路上佢丟低一句「幫我研究下將 memory 遷移去 SQLite,做埋」。30 秒內,佢見到檢察官嘅三項質疑、被告修訂後嘅四步計劃、同埋第一步已經 ✅。佢冇再睇。食 lunch 嗰陣 scroll 返上去,嗰條 message 已經自己變成咗:**✅ 判決:核准 (9/10),3,200 條記憶完整遷移,證物 11 件**。

佢從來冇學過 tier、router、verifier 呢啲詞。佢只知道:

- **白色做嘢,黑色挑剔,法官把關** — 三句講晒個系統
- 簡單嘢快過任何 chatbot,複雜嘢穩過任何 agent
- 出咗事,案卷一查就知邊步、邊個角色、咩證據
- 佢嘅信任唔係嚟自「AI 好叻」,而係嚟自**佢親眼見過個法庭點樣攔截錯誤**

呢個就係黑白嘅意思:唔係黑盒,唔係白盒 — 係**一個你睇得見判決過程嘅盒**。

兩隻狗,一個法庭,冇灰泥嘅牆全部補完。開庭。

---

## 附:落地次序(4 個 milestone)

| M | 內容 | 對應 |
|---|---|---|
| M1 接駁 | `core/court.py` state machine;route_task 入主路徑;model_id 傳入 delegate;統一 load_config | P0-1/2/3, P1-1/3/5 |
| M2 UI | 單 message edit-in-place;五種 verdict 模板;`/court` 指令族;STAY inline keyboard | §1, §3 |
| M3 速度 | Devil∥Plan 並行;prefix cache 紀律;批量 verify;fast lane | §5 |
| M4 分流 | docket 隊列;per-user/case 隔離;巡迴法庭夜報;stats 儀表板 | §4, §7 |

每個 milestone 都有第 7 節嘅指標守門 — 數據唔達標就唔出下一個。

**— Sunny, BAW 黑白法庭 v2**