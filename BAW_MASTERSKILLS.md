# BAW MasterSkills — 技能路由手冊

BAW 開機時讀呢份文件，知道咩情況用咩技能/參考。

---

## 系統架構簡述

| 層 | 位置 | 用途 |
|----|------|------|
| SOUL.md | `~/.baw/SOUL.md` | 輸出規則（語言、精簡、HTML）— **永遠保持 lean** |
| MasterSkills | `~/.baw/references/MASTERSKILLS.md` | **呢份文件** — 技能路由 |
| References | `~/.baw/references/*.md` | 技術知識 |
| Code | `/app/core/` | 核心引擎 |
| Config | `~/.baw/config.yaml` | Provider、model、cost 設定 |
| Env | `~/.baw/.env` | API keys |

## 技能路由

當用家嘅請求涉及以下範疇時，先 `read_file` 對應嘅 reference 再執行：

### 文件處理
- **.docx 解析** → pip install python-docx（已 bundle，唔應該再 fail）
- **Document structuring / TOC / search** → `~/.baw/references/ref-document-structuring.md`

### 系統自我評估
- **用家要求 audit / 自我診斷 / 健康檢查** → `~/.baw/references/ref-self-evolution.md`

### 多模型協作
- **用家要求 fusion / 多模型分析 / cross-validation** → `~/.baw/references/ref-fusion-mode.md`

### 模型選擇同成本
- **用家問點解用呢個 model / cost / routing** → `~/.baw/references/ref-cost-routing.md`

### 系統架構
- **用家問 BAW 點運作 / 檔案喺邊 / 技術細節** → `~/.baw/references/ref-system-architecture.md`

### 代碼質量
- **用家要求 code review / 檢查代碼 / over-engineering audit** → `~/.baw/references/ref-yagni.md`
- **用家想 generate 新 tool / 寫 code** → 自動注入 YAGNI Decision Ladder（喺 tool_generate.py prompt 入面）
- **用家話「review my code」「check for bloat」** → 用 `ponytail_review` tool 掃描

## 原則

1. **SOUL.md 永遠 lean** — 唔好加任何技術細節或 meta 指令入去
2. **Reference files 係 on-demand** — 用家問到先讀，唔好 default 讀晒
3. **Install-time 出廠設定** — 所有 pip dep 必須入 requirements.txt，唔好 runtime 先發現冇
4. **Error routing** — 系統錯誤 → 先報 error + 原因，唔好 self-audit
