# BAW 脫離 Sticky 獨立路線圖

## 目標
BAW 成為完全自我維持、自我進化、自我優化嘅獨立系統，唔需要 Sticky 介入。

## Phase 分佈（8 CPs × 4 Phases）

### Phase A: Make It Real — 將 prompt rule 變 code（3 CPs）

| CP | Name | Deliverable | Gate |
|----|------|-------------|------|
| A1 | Fabrication Gate Automation | Code-enforced verify loop: 每 tool call 後自動 read-back，唔靠 agent 記住 | python3 -c test pass |
| A2 | Config Drift Auto-Fix | Config drift detection 從 prompt 搬落 capabilities.py，LLM call 前自動修正 | python3 -m core.diagnostics pass |
| A3 | Feedback Threshold Tuning | Threshold 由 5→2，auto-approve default enable（保留 rollback） | 檢查 behavior.jsonl 有 lessons |

### Phase B: Close the Loop — 完整 feedback-to-behavior（2 CPs）

| CP | Name | Deliverable | Gate |
|----|------|-------------|------|
| B1 | LLM-Assisted Semantic Extraction | 從 keyword regex 改為 LLM-assisted 理解 correction 意圖 | 新 lesson extraction 正確分類 |
| B2 | Unified Evolution Pipeline | 整合 memory + behavior + corrections + skill usage 到一個 pipeline | run 一次產出完整 report |

### Phase C: Self-Sustain — 自包含運作（2 CPs）

| CP | Name | Deliverable | Gate |
|----|------|-------------|------|
| C1 | Internal Scheduler Migration | 將 Hermes cron health check 搬入 BAW internal scheduler | baw cron list 見到 health check |
| C2 | Dead State Recovery | 自我重啟 + loop detection + graceful restart | 模擬 crash → 自動 recover |

### Phase D: Self-Improve — 真進化（2 CPs）

| CP | Name | Deliverable | Gate |
|----|------|-------------|------|
| D1 | Code-Level Auto-Patching | 根據 pattern 自動改 core code（加 tool、改 routing） | 成功 patch 一個 code file |
| D2 | Self-Testing Pipeline | 改 code → 自動 test → verify → rollback/deploy | CI-style pipeline 行得通 |

### Phase E: Independence — 長期驗證

| Metric | Target |
|--------|--------|
| Zero Sticky intervention | 連續 30 日冇人入嚟修復 |
| Meaningful self-evolution | 每週 ≥1 次 SOUL.md/code 修改 |
| Error recovery rate | >95% |

---

## 開始順序：Phase A → Phase B → Phase C → Phase D
逐個 CP 執行，每個有 verify gate，pass 先 next。
