# BAW 開發記憶庫 — 繁體中文總結

> **系統**: BAW (Black And White)  
> **版本**: v0.12 — 最新版本  
> **開發日期**: 2026-06-07 起

---

## 一、系統概述

BAW 係一個 AI Agent 開發平台，專為「永不問用戶」同「永不放棄」呢兩個黃金法則而設計。平台由零開始構建，包含晒核心循環、LLM 整合、工具系統、記憶庫、對抗法庭等核心模組。

### 核心設計原則

1. **永不問用戶** — 遇到問題自己解決，唔好拋返俾用戶
2. **永不放棄** — 每個子目標都要嘗試唔同方法，唔會直接放棄
3. **對抗法庭** — Angel/Devil 雙聲音獨立分析，確保決定經過充分討論
4. **協議無關** — LLM 通訊協議抽象化，支援多個 Provider

---

## 二、架構組成

### 2.1 目錄結構

```
baw/                      ← Code repo
├── baw                   CLI entry point
├── core/                 Core modules (26+ files)
│   ├── loop.py           Agent loop
│   ├── llm.py           LLM abstraction
│   ├── adversarial.py   Angel/Devil court
│   ├── tools.py         Tool registry
│   ├── permission.py    3-tier permission engine
│   ├── memory.py        Memory JSONL store
│   ├── context.py       Context manager
│   ├── fact_checker.py  Fact checking
│   └── ... (其他模組)
├── tools/                Built-in tools (4 files)
├── search_providers/    Search provider plugins
├── config.yaml          Default config
├── docs/                GitHub Pages documentation
└── knowledge/           Development knowledge base
```

### 2.2 核心模組

| 模組 | 功能 |
|------|------|
| `loop.py` | Agent 主循環，控制執行流程 |
| `llm.py` | LLM 協議抽象化，支援 DeepSeek/MiniMax/Kimi 等 |
| `adversarial.py` | Angel/Devil 法庭 (v2 — 平行獨立分析) |
| `tools.py` | 工具註冊同權限管理 |
| `permission.py` | 三層權限引擎 (高/中/低) |
| `memory.py` | JSONL 記憶庫 |
| `checkpoint.py` | 檢查點同 rollback |
| `degradation.py` | Tool degrade 鏈 |
| `search.py` | 開放搜尋供應商系統 |
| `setup.py` | 設定精靈 + Config CLI |

---

## 三、執行模式

### 三種模式

| 模式 | 描述 |
|------|------|
| `quick` | 快速執行，無對抗法庭 |
| `hybrid` | 混合模式，部分步驟經過法庭 |
| `tight` | 嚴格模式，每步都經過法庭 |

### 執行流程 (tight mode)

```
User prompt
    │
    ▼
[Phase 1] Plan
    ├── Angel 生成步驟計劃
    └── Devil 審查計劃
         │
         ▼
[Phase 2] Each step
    ├── 檢查點保存
    ├── Devil 挑戰步驟 → [Devil: X/10]
    ├── Angel 回應 → [Angel: Y/10]
    ├── Y > X ? proceed : BLOCK
    ├── 執行工具
    │     └── 權限檢查
    │     └── Tool degrade
    ├── 驗證結果
    ├── 成功 ? 自動提交 : 恢復
    │
    ▼
[Phase 3] Report
    ├── 完成咩
    ├── 起咗咩作用
    └── 成本總結
```

---

## 四、LLM Provider 支援

| Provider | Protocol | Models |
|----------|----------|--------|
| DeepSeek | openai-chat | deepseek-v4-flash, deepseek-reasoner |
| MiniMax | openai-chat | MiniMax-M2.5 |
| Kimi (Moonshot) | openai-chat | kimi-k2.6 |
| Anthropic | anthropic | claude-sonnet-4 |
| Google | google | gemini-2.5-pro |

### 模型自動路由

- **短查詢** → `deepseek-v4-flash` (快速)
- **長上下文** → `MiniMax-M2.5` (大context)
- **閾值**: >8,000 tokens 觸發長模型路由

---

## 五、搜尋供應商系統

### 內置 Provider

| Provider | API Key | Description |
|----------|---------|-------------|
| DuckDuckGo | 唔需要 | 免費，使用 `duckduckgo-search` library |

### CLI 操作

```bash
baw --search-provider list                  # 列出所有 provider
baw --search-provider guide duckduckgo      # 設定指南
baw --search-provider api duckduckgo        # API 參考
baw --search-provider test duckduckgo "..." # 測試搜尋
```

---

## 六、對抗法庭詳情 (v2)

### Devil 角色 (獨立批評者)

- **人格**: 自動生成既反對聲音 — 從風險/問題角度分析
- **權限**: 零執行權限 — 無 tools，無 bash，無檔案寫入
- **獨立性**: 唔知道 Angel 講咗咩；純粹獨立分析
- **輸出**: 純文本分析 + `[Devil: X/10]` 分數

### Angel 角色 (獨立支持者)

- **人格**: 自動生成既支持聲音 — 從可行性/價值角度分析
- **權限**: 零執行權限
- **獨立性**: 唔知道 Devil 講咗咩；純粹獨立分析
- **輸出**: 純文本分析 + `[Angel: Y/10]` 分數

### BAW 中立角色

- BAW 唔係 Angel — 佢係中立既聆聽者
- 收到兩個獨立分析後，BAW 用 common sense 同判斷力綜合
- 可以同意 Devil 多啲、同意 Angel 多啲、或者部分同意雙方
- **唔會討好用戶** — 用戶既要求可能唔合理；BAW 會指出呢啲

---

## 七、Tool Degradation

每個工具都有 fallback 鏈；失敗時自動降級：

| Tool | Degradation Chain |
|------|------------------|
| `bash` | 1. 雙倍 timeout → 2. 用 parent dir 重試 → 3. 用 /tmp 重試 |
| `write_file` | 1. 用 parent dir 重試 → 2. 用 /tmp 重試 → 3. 提供 alternative path |
| `web_search` | 1. 簡化查詢 (3 keywords) → 2. 試唔同 provider |

---

## 八、已知問題同修復

| 問題 | 狀態 | 修復 |
|------|------|------|
| Kimi thinking mode 返回空 content | ✅ 已修復 | `disable_reasoning: true` |
| NPU dispatcher zombie restart loop | ✅ 已修復 | Services disabled |
| duplicate ESPHome log watcher | ✅ 已修復 | 殺掉 duplicate |
| Step 1 display suppressed | ✅ 已修復 | 移除 `_step_idx > 0` guard |

---

## 九、配置參照

### 完整 Config Key 清單

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `tight` | 執行模式 |
| `model.default` | string | `deepseek-v4-flash` | 預設 LLM 型號 |
| `model.route.enabled` | bool | `true` | 啟用自動路由 |
| `tone.default` | string | `casual` | 預設語氣 |
| `adversarial.enabled` | bool | `true` | 啟用對抗法庭 |
| `verify.enabled` | bool | `false` | 預設關閉每步驗證 |
| `fact_check.mode` | string | `normal` | 事實檢查模式 |

---

## 十、開發歷程

### Day 1: 2026-06-07 (密集開發日)

| Time | Commit | Event |
|------|--------|-------|
| 13:38 | `d699a15` | **Init**: BAW Agent Platform v3 |
| 14:07 | `dab60e9` | **Kimi K2.6**: 添加為 primary model |
| 14:41 | `7f8febc` | **Search Registry**: DuckDuckGo 整合 |
| 15:03 | `f6b32c1` | **Self-improving**: 自改善循環 |
| 15:29 | `8fc824a` | **P0 complete**: web_search + fact checker |
| 16:05 | `2897537` | **P1: Slash commands**: 12 commands |
| 17:00 | `48b52ad` | **3 modes + display**: 執行模式改革 |
| 17:08 | `428ddbb` | **Scheduler + Skills + Dashboard** |
| 17:32 | `c0ebddb` | **Self-learning**: --learn-skill |
| 17:57 | `0e9da35` | **Setup wizard + Config CLI** |
| 18:15 | `89f7927` | **Bilingual README + docs site** |

**Total**: 18 actual dev commits + 10 auto-commits

---

## 十一、重要設計決策記錄

| 決策 | 日期 | 描述 |
|------|------|------|
| D-001 | 2026-06-07 | 平台名稱 "BAW" (Black And White) |
| D-002 | 2026-06-07 | Angel/Devil 雙聲音法庭 (v2) |
| D-003 | 2026-06-07 | 協議無關 LLM 架構 |
| D-004 | 2026-06-07 | 單一統一記憶 API |
| D-005 | 2026-06-07 | 三層權限 (高/中/低) |
| D-012 | 2026-06-07 | GitHub Pages Docs 網站 |
| D-013 | 2026-06-09 | 模型自動路由 |
| D-014 | 2026-06-09 | 指數退避重試 |
| D-015 | 2026-06-09 | 命令結果快取 (60s TTL) |
| D-016 | 2026-06-09 | Docs Chain Pattern |

---

## 十二、 Roadmap

- [x] 核心循環 + LLM + tools + memory + adversarial + CLI
- [x] Slash commands + config CLI + setup wizard
- [x] Scheduler + skills + dashboard
- [x] Self-learning + background tasks + GitHub 整合
- [x] 雙語 docs + GitHub Pages
- [x] 三層模型選擇器
- [x] 訊息佇列
- [x] 指數退避重試
- [x] 60s TTL 命令快取
- [x] 自動模型路由
- [ ] Multi-agent swarm coordination
- [ ] Voice pipeline (STT → LLM → TTS)
- [ ] Plugin marketplace
- [ ] Web UI dashboard

---

## 十三、如何擴展

### 添加新 Tool

1. 建立 `tools/my_tool.py` with `register_tool()`
2. 喺 `config.yaml` 添加權限規則
3. 可選添加 degradation chain

### 添加新 Protocol

```python
from baw.core.llm import register_protocol

def my_handler(model, messages, tools, temperature, max_tokens):
    # Custom API call logic
    return LLMResponse(...)

register_protocol("my-protocol", my_handler)
```

---

## 十四、資源連結

- **Repo**: https://github.com/cornreform/baw-agent-platform
- **Docs Site**: https://cornreform.github.io/baw-agent-platform/
- **Developers**: the user + Sticky (Hermes Agent)

---

> 📝 本總結基於 `knowledge/INDEX.md` 編寫，最後更新：2026-06-09