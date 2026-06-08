# BAW v0.11 — Delegation + Self-Evolution + Hot Reload

> Release date: 2026-06-08  
> Tag: `v0.11`  
> Baseline: `v0.10`

---

## 🤖 Agent Delegation（全新）

**主腦 + 執行者架構** — 真正嘅 agent-to-agent 分工：

```
你問問題
  ↓
🐋 DeepSeek V4 Flash（主腦）
  ├─ Court（Devil + Angel 分析）
  ├─ 寫 Plan：將任務拆成 N 個子步驟
  └─ 每個步驟 delegate 俾 MiniMax
       ↓
🤖 MiniMax M2.5（執行者）× N
  ├─ Step 1: 獨立執行，有自己 tools
  ├─ Step 2: 接收 Step 1 context → 執行
  ├─ Step 3: 接收 Step 1+2 context → 執行
  └─ ...
       ↓
🐋 DeepSeek V4 Flash（主腦）
  └─ Synthesis：收齊結果 → 整合成最終答案
```

- 每個子 agent context 完全隔離
- 步驟結果自動傳遞俾下一個 step
- DeepSeek 最後綜合所有結果

**新增檔案**: `tools/delegate_task.py`

---

## 🧬 三層自我進化（全新）

| Layer | 機制 | 觸發 |
|-------|------|------|
| 1️⃣ 行為追蹤 | 每個 tool call 記錄（成功/失敗/時間/錯誤） | 每次 tool call 即時 |
| 2️⃣ 模式偵測 | 分析高失敗率工具、連續失敗、用戶修正 | 每週 dreaming |
| 3️⃣ 自動優化 | 根據 pattern 自動 patch SOUL.md + config | 每週 dreaming |

- 全部自動，唔需要人手觸發
- 越用越聰明

**新增檔案**: `core/evolve.py`

---

## 🔄 Hot Reload（全新）

- `/reload` — 熱重載 tools/config/SOUL，唔使 restart bot
- `/evolve` — 查看自我進化統計（events、success rate、corrections）

---

## 🧠 記憶系統強化

- 自動儲存 User query + BAW response（之前只記 user query）
- edges.json 圖譜 + 2-hop 關聯 spread
- 每日 04:00 自動記憶衰退
- 每週日 03:00 dreaming（歸檔 + decay + 自我進化）

---

## 其他改進

- Step 顯示精簡版（無 raw args、無 output noise）
- `delegate_task` import 順序修復（sys.path 在 BAW imports 之前 setup）
- `SyntaxWarning` fix（raw docstring）
- `/evolve` Telegram command 新增

---

## 🎙️ 語音辨識 — Voice STT（全新）

**智能 STT 路由** — 唔再 hardcode 某個模型，改為動態檢測系統能力：

**檢查鏈（Priority）：**
1. **模型 audio_input 能力** — 檢查當前主模型 config 有冇 `audio_input: true`
2. **掃描所有模型** — 如果有其他模型支援音訊，提示用戶 `/model` 切換
3. **STT 配置** — 讀取 `stt.method` 設定（openai-whisper / faster-whisper / google-speech）
4. **faster-whisper 本地 fallback** — 如果已安裝，自動使用（無需 config）
5. **以上全無** — 輸出完整診斷報告 + 安裝選項

**支援嘅 STT 方案：**
- **faster-whisper**（本地免費，`pip install faster-whisper`）
- **OpenAI Whisper API**（需 `OPENAI_API_KEY`）
- **Google Cloud Speech-to-Text**（需服務帳號）
- 未來可加：模型原生 audio 支援（當模型有 `audio_input: true` 時）

**設定**（`~/.baw/config.yaml`）：
```yaml
stt:
  method: "faster-whisper"     # 或 "openai-whisper" / "google-speech"
  model: "base"                # faster-whisper model size
  # api_key_env: "OPENAI_API_KEY"  # 僅 openai-whisper 需要


---

## 檔案變更

```
7 files changed, 772 insertions(+), 233 deletions(-)
├── core/evolve.py              ← NEW: 3-layer self-evolution
├── tools/delegate_task.py      ← NEW: MiniMax sub-agent delegation
├── core/loop.py                ← Phase 3b: plan → delegate → synthesise
├── core/dream.py               ← Step 5: auto-optimize during dreaming
├── core/tools.py               ← execute_tool now tracks behavior
├── core/messaging/__init__.py  ← /reload + /evolve + user feedback tracking
└── core/messaging/telegram.py  ← command menu updated
```
