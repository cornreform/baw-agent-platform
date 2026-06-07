# BAW Knowledge Base — 開發記憶庫

> **系統名稱**: BAW (Black And White)
> **狀態**: v1.0.0 — 測試版本
> **開始日期**: 2026-06-07
> **開發者**: Sunny + Sticky (Hermes Agent)
> **Repo**: https://github.com/cornreform/baw-agent-platform
> **文檔站**: https://cornreform.github.io/baw-agent-platform/

---

## 目錄

1. [設計哲學](#1-設計哲學)
2. [架構地圖](#2-架構地圖)
3. [開發歷程](#3-開發歷程)
4. [設計決策記錄](#4-設計決策記錄)
5. [Config 參照](#5-config-參照)
6. [LLM Provider 設定](#6-llm-provider-設定)
7. [Search Provider 系統](#7-search-provider-系統)
8. [天使/魔鬼法庭細則](#8-天使魔鬼法庭細則)
9. [Tool Degradation 機制](#9-tool-degradation-機制)
10. [已知問題 & 修正記錄](#10-已知問題--修正記錄)
11. [如何擴展](#11-如何擴展)
12. [Roadmap](#12-roadmap)

---

## 1. 設計哲學

### 1.1 永不問用戶 (Never Ask the User)

BAW 嘅黃金法則：**遇到問題自己解決，唔好拋返俾用戶。**

- 失敗 → retry → replan → rollback → 換策略 → 全部用盡先上報
- Tool timeout → 加倍 timeout → parent directory fallback → /tmp/ fallback → replan
- 追蹤 `strategies_tried` list，連續 3 次同一策略失敗換下個
- 6 次總失敗先上報天使/魔鬼法庭

### 1.2 永不放棄 (Never Surrender)

BAW 唔會直接放棄一個子目標。嘗試不同方法係 mandatory：
- Checkpoint save before each step
- 如果 verify FAIL → 自動 recover
- Recover 順序：retry → replan → rollback
- 只有所有 recover 策略失敗先上報

### 1.3 Angel/Devil 法庭

- Devil = 反對派，**零工具權限**，永遠先發言
- Angel = 執行者，有齊工具，聽完 Devil 先決定
- Devil 分數 > Angel 分數 → BLOCK
- 每次 user turn 都行一次 court（tight mode 下）
- Devil persona 係自動生成嘅 foil —— Angel 越 trustful，Devil 越 skeptical

### 1.4 協議無關 (Protocol-agnostic)

- LLM 通訊協議抽象層：`register_protocol(name, handler_fn)`
- 內置三個 protocol：`openai-chat`、`anthropic`、`google`
- Provider config：`base_url` + `api_key_env` + `protocol` + `models[]`
- 內置 auto-fallback：primary 失敗自動試 fallback

---

## 2. 架構地圖

### 2.1 目錄結構

```
baw/                        ← Code repo
├── baw                     CLI entry point (Python, 700 lines)
├── core/                   核心模組 (26 files)
│   ├── loop.py             Agent loop (844 lines)
│   ├── llm.py              LLM abstraction (405 lines)
│   ├── adversarial.py      Angel/Devil court (216 lines)
│   ├── tools.py            Tool registry (81 lines)
│   ├── permission.py       三級權限引擎
│   ├── memory.py           記憶 JSONL store (133 lines)
│   ├── context.py          Context manager
│   ├── fact_checker.py     事實查證
│   ├── tone.py             語氣 profiles
│   ├── scheduler.py        Cron daemon
│   ├── skills.py           YAML skill system
│   ├── learn.py            自我學習技能
│   ├── board.py            HTML dashboard (203 lines)
│   ├── task_manager.py     Async task manager
│   ├── github.py           GitHub integration
│   ├── search.py           開放 search provider
│   ├── setup.py            Setup wizard + Config CLI (267 lines)
│   ├── commands.py         Slash commands (367 lines)
│   ├── display.py          步驟顯示 (127 lines)
│   ├── dream.py            每週自我整理 (105 lines)
│   ├── checkpoint.py       Checkpoint / rollback
│   ├── degradation.py      Tool degradation chains
│   ├── file_history.py     檔案版本 SHA256
│   ├── autosave.py         自動 git commit
│   ├── render.py           HTML renderers
│   └── verifier.py         Per-step LLM verify
├── tools/                  內置工具 (4 files)
│   ├── bash.py
│   ├── read_file.py
│   ├── write_file.py
│   └── web_search.py
├── search_providers/       Search provider plugins
├── config.yaml             預設配置
├── docs/                   GitHub Pages 文檔
│   └── index.html          暗色主題雙語文檔站
├── knowledge/              開發記憶庫（你喺度）
├── BAW-INTRODUCTION.html   完整介紹書
└── BAW-PLAN.html           設計原稿

~/.baw/                     ← 用戶設定目錄
├── config.yaml             用戶配置
├── SOUL.md                 Soul / 行為規則
├── .env                    API keys
├── memory/store.jsonl      記憶儲存
├── memory/edges.json       記憶關聯圖
├── history/manifest.jsonl  檔案版本歷史
├── schedule.yaml           排程定義
├── schedule_state.json     排程狀態
├── skills/*.yaml           已安裝技能
├── tasks/                  背景任務輸出
└── dashboard.html          生成式系統儀錶板
```

### 2.2 Module 相依關係

```
baw (CLI entry)
 ├── core/llm.py           ─── httpx (HTTP client)
 ├── core/loop.py          ─── llm, tools, permission, memory,
 │                            context, checkpoint, file_history,
 │                            autosave, display, render, adversarial
 ├── core/tools.py         ─── tools/bash, read_file, write_file, web_search
 ├── core/adversarial.py   ─── llm (Devil voice)
 ├── core/commands.py      ─── memory, llm, dream, search
 ├── core/setup.py         ─── yaml, config I/O
 ├── core/scheduler.py     ─── croniter, threading
 ├── core/board.py         ─── scheduler, skills
 ├── core/task_manager.py  ─── threading, subprocess
 └── core/search.py        ─── search_providers/* plugins
```

### 2.3 Agent Loop 流程 (tight mode)

```
User prompt
    │
    ▼
[Phase 1] Plan
    ├── Angel 生成 step plan
    └── Devil 審查 plan
         │
         ▼
[Phase 2] Each step
    ├── Checkpoint save
    ├── Devil challenges step → [Devil: X/10]
    ├── Angel responds → [Angel: Y/10]
    ├── Y > X ? proceed : BLOCK
    ├── Execute tool(s)
    │     └── Permission check (high/medium/low)
    │     └── Tool degradation (fallback chain)
    ├── Verify result (if enabled)
    ├── Success ? auto-commit : recover
    │     └── retry → replan → rollback
    │
    ▼
[Phase 3] Report
    ├── What was done
    ├── What worked
    └── Cost summary
```

---

## 3. 開發歷程

### Day 1: 2026-06-07（密集開發日）

| 時間 | Commit | 事件 |
|------|--------|------|
| 13:38 | `d699a15` | **Init**: BAW Agent Platform v3 — 從零 reset，核心 loop + LLM + tools + memory + adversarial + CLI |
| 14:07 | `dab60e9` | **Kimi K2.6**: 加入 Kimi 做 primary model，auto-fallback 機制 |
| 14:07 | `738c648` | **Config fix**: 修正 config.sample.yaml indentation |
| 14:41 | `7f8febc` | **Search Registry**: 開放 search provider registry，內置 DuckDuckGo |
| 15:03 | `f6b32c1` | **Self-improving**: 自我改進 loop + checkpoint system |
| 15:29 | `8fc824a` | **P0 complete**: web_search tool + fact checker upgrade + HTML rendering + thread-safe cost tracker |
| 15:46 | `4be7471` | **Bug fix**: regex over-escape in claim patterns |
| 15:58 | `cc3a165` | **Polish**: add tool list to --help |
| 16:05 | `2897537` | **P1: Slash commands**: 12 commands + CLI integration |
| 16:14 | `97332e2` | **P1: /rethink /court /fresh**: 三個進階 slash command |
| 16:21 | `7d64e45` | **P1: Tool degradation**: bash/write/search fallback chains |
| 17:00 | `48b52ad` | **3 modes + display**: quick/hybrid/tight execution modes + display overhaul + BTW + background delegation |
| 17:08 | `428ddbb` | **Scheduler + Skills + Dashboard**: 三大 infra 模組 |
| 17:32 | `c0ebddb` | **Self-learning**: `--learn-skill` + `--learn-url` |
| 17:55 | `eeca807` | **Async TaskManager + GitHub**: 背景任務管理 + GH issues/PRs/CI |
| 17:57 | `0e9da35` | **Setup wizard + Config CLI + Chat interface**: 最後 UX 層 |
| 18:15 | `89f7927` | **Bilingual README + docs site**: GitHub Pages 文檔 |
| 18:30 | `0aaf18d` | **English-first**: README + docs default to English |

總計 **18 個實際開發 commits**，加 10 個 auto-commit（BAW agent 自己紀錄）。全 day 從零到完整 platform。

### 開發模式

- **主體開發**: Sticky (Hermes Agent) 用 DeepSeek V4 Flash / Kimi K2.6
- **部分自產 commits**: BAW agent 自己入 git commit 紀錄狀態
- **測試**: 每個模組開發後 functional test 驗證
- **版本控制**: git commit + auto-commit cron (every 6h) + push to GitHub (SSH key `id_cornreform`)

---

## 4. 設計決策記錄

### D-001: 平台名稱 BAW

- **日期**: 2026-06-07
- **原名**: Stark（德文 "strong, clean"）
- **改動**: 改為 BAW (Black And White)
- **原因**: 用戶養咗兩隻狗（黑白配），Angel/Devil 哲學更貼切
- **影響**: 所有檔案名、CLI 入口、變數名全部改曬

### D-002: Angel/Devil 雙魂法庭（v2 — 同步獨立分析）

- **日期**: 2026-06-07（初始版），2026-06-07（v2 重寫）
- **v1 設計（已廢棄）**: Devil 永遠先發言，Angel 聽完再回應。順序分析會 bias Angel 嘅判斷。
- **v2 新設計**: Devil 同 Angel 同步獨立分析同一個目標，各自評分，互不知情。
- **原因**: 避免順序 bias。兩個聲音都反映真實獨立觀點。BAW 以中立角色聆聽雙方。
- **格式**: `[Devil: X/10]` + `[Angel: Y/10]` — 獨立評分，無先後次序
- **法庭 vs 執行分離**: Court phase 冇執行權限；Execution phase 冇法庭。結論確立後直接執行。
- **用戶態度**: BAW 回覆時保持中立，唔討好用家，會勇於反駁。用家想法唔一定合理。

### D-003: 協議無關 LLM 架構

- **日期**: 2026-06-07
- **Decision**: `register_protocol()` 抽象層，唔 hardcode 任何 provider
- **原因**: 避免 vendor lock-in，用戶可以自由轉模型
- **實作**: 3 個 wire protocol (`openai-chat`, `anthropic`, `google`) + custom handler 支援
- **Config**: `providers.<name>.protocol` 決定用邊個 handler

### D-004: 單一統一記憶 API

- **日期**: 2026-06-07
- **Decision**: `remember()` + `search()` 單一 interface，唔暴露底層 layers
- **原因**: 簡化 agent 同用戶嘅使用體驗
- **儲存**: JSONL append-only (`~/.baw/memory/store.jsonl`)
- **評分**: 重複存取 → 加分，high score 記憶優先注入 system prompt

### D-005: 三級權限 (唔係 binary)

- **日期**: 2026-06-07
- **Decision**: High (禁止) / Medium (提示) / Low (允許)
- **原因**: Binary allow/deny 太粗糙。sudo/rm -rf 要 block，write_file 可以提示，read_file 直接俾
- **Config**: `permissions.risk_levels` 用 path pattern 同 command prefix 定義

### D-006: Per-step verify 預設關閉

- **日期**: 2026-06-07
- **Decision**: `verify.enabled: false` 預設
- **原因**: 每個 step 行一次 LLM verify 太貴（token + latency），有用先開
- **使用場景**: tight mode 配合 token budget 充足時

### D-007: File version + Auto git

- **日期**: 2026-06-07
- **Decision**: 每次寫入記錄 ISO timestamp + SHA256 + 自動 git commit
- **原因**: 可追溯性、rollback 能力。防止意外覆蓋重要檔案
- **實作**: `file_history.py` (manifest) + `autosave.py` (git commit)

### D-008: HTML 內部報告

- **日期**: 2026-06-07
- **Decision**: BAW 內部輸出用 HTML，Telegram/CLI 用純文字
- **原因**: HTML dashboard 同 court report 更可讀，但 terminal 唔需要 HTML
- **例外**: `baw --board` 輸出 HTML file

### D-009: 三種執行模式

- **日期**: 2026-06-07
- **Decision**: Quick / Hybrid / Tight
- **原因**: 唔同場景需要唔同安全等級。quick = 快速答問題，tight = 重要操作
- **Config**: `mode: tight`（預設）

### D-010: 六種語氣 Profile

- **日期**: 2026-06-07
- **Decision**: casual / business / teaching / client-doc / ot-rt / stepwise
- **原因**: 語氣影響 LLM response quality，對話場景唔同需要唔同語氣
- **Config**: `tone.default: casual`

### D-011: Setup Wizard + Config CLI

- **日期**: 2026-06-07
- **Decision**: `baw --setup` 互動引導 + `baw --cfg set/get/list` 即時設定
- **原因**: 唔係個個用戶想手動 edit YAML
- **即時生效**: Config CLI 修改直接寫入 `~/.baw/config.yaml`

### D-012: GitHub Pages 文檔站

- **日期**: 2026-06-07
- **Decision**: `docs/index.html` dark theme + 語言切換 (繁/EN)
- **原因**: README 太長會 overwhelming，獨立文檔站更有結構
- **語言**: 英文 default，繁中 toggle

---

## 5. Config 參照

### 5.1 完整 Config Key 列表

| Key | 類型 | 預設值 | 說明 |
|-----|------|--------|------|
| `mode` | string | `tight` | Execution mode: quick/hybrid/tight |
| `model.default` | string | `deepseek-v4-flash` | 預設 LLM 模型 ID |
| `model.fallback` | string | (同 default) | Fallback 模型 ID |
| `tone.default` | string | `casual` | 預設語氣 profile |
| `adversarial.enabled` | bool | `true` | 開啟天使/魔鬼法庭 |
| `adversarial.flag_threshold` | int | `0` | Devil 分數高於此值即 flag |
| `adversarial.warn_threshold` | int | `2` | Devil 分數高於此值 warn 而非 block |
| `verify.enabled` | bool | `false` | 每步 LLM verify |
| `fact_check.mode` | string | `normal` | off/normal/strict |

### 5.2 Provider Config 結構

```yaml
providers:
  <provider_name>:
    base_url: "https://api.example.com/v1"
    api_key_env: "ENV_VAR_NAME"    # 從環境變數讀 key
    protocol: "openai-chat"        # 或 anthropic/google/custom
    models:
      - id: "model-id"
        context_window: 65536
        vision: false
        cost_per_1m_input: 0.30
        cost_per_1m_output: 1.20
        temperature: 0.7           # 可選，override default
        model_kwargs:              # 可選，extra LLM body params
          disable_reasoning: true
```

### 5.3 權限 Config 結構

```yaml
permissions:
  risk_levels:
    high:       # ⛔ 禁止
      - path: "/etc/*"
      - cmd_prefix: "sudo"
      - cmd_prefix: "rm -rf"
    medium:     # ⚠️ 提示
      - tool: "write_file"
      - tool: "bash"
    low:        # ✅ 允許
      - tool: "read_file"
```

---

## 6. LLM Provider 設定

### 6.1 支援中嘅 Provider

| Provider | Protocol | 模型例子 | 狀態 |
|----------|----------|----------|------|
| DeepSeek | openai-chat | deepseek-v4-flash, deepseek-reasoner | **已啟用 (default)** |
| MiniMax | openai-chat | MiniMax-M3, MiniMax-M2.5 | **已啟用** |
| Anthropic | anthropic | claude-sonnet-4 | 已配置 (commented) |
| Google | google | gemini-2.5-pro | 已配置 (commented) |

### 6.2 加新 Provider

```yaml
# 1. config.yaml 加 provider entry
providers:
  groq:
    base_url: "https://api.groq.com/openai/v1"
    api_key_env: "GROQ_API_KEY"
    protocol: "openai-chat"  # OpenAI 相容就用呢個
    models:
      - id: "llama-3.3-70b-versatile"
        context_window: 32768
        vision: false
        cost_per_1m_input: 0.59
        cost_per_1m_output: 0.79

# 2. 如果是唔同 protocol，core/llm.py 加 handler
from .llm import register_protocol
def my_custom_handler(model, messages, tools, **kw):
    # custom logic here
    pass
register_protocol("my-protocol", my_custom_handler)
```

### 6.3 Kimi Thinking Mode Bug

**問題**: Kimi K2.6 預設會用 thinking mode，導致 `content` 回傳 `None`（因為 thinking 內容喺 `reasoning_content` field）。
**修正**: `model_kwargs.disable_reasoning: true` 避免空 content。
**適用模型**: Kimi K2.6 (`api.moonshot.ai`)

---

## 7. Search Provider 系統

### 7.1 開放註冊機制

Search provider 係 pluggable：喺 `search_providers/` 放一個 file 實作介面，call `register_search_provider()`。

### 7.2 內置 Provider

| Provider | API Key | 說明 |
|----------|---------|------|
| DuckDuckGo | 唔需要 | 免費，`duckduckgo-search` library |

### 7.3 CLI 操作

```bash
baw --search-provider list                  # 列出所有 provider
baw --search-provider guide duckduckgo      # 設定指南
baw --search-provider api duckduckgo        # API 參考
baw --search-provider test duckduckgo "..." # 測試
```

### 7.4 加新 Provider

```python
# search_providers/tavily.py
from baw.core.search import register_search_provider

def search_tavily(query, limit=5):
    # call Tavily API
    return results

register_search_provider(
    name="tavily",
    description="Tavily AI search",
    handler=search_tavily,
    requires_api_key=True,
    env_var="TAVILY_API_KEY",
)
```

---

## 8. 天使/魔鬼法庭細則（v2 — 同步獨立分析）

### 8.1 Devil 角色（Independent Critic）

- **人設**: 自動生成嘅 foil — 分析目標時從風險/問題角度出發
- **權限**: 零執行權限 — 冇 tools、冇 bash、冇寫 file（法庭階段）
- **獨立性**: 唔知道天使講咗咩，純粹從自己角度分析
- **輸出**: 純文字分析 + `[Devil: X/10]` 分數
- **目的**: 提供真實嘅反對觀點，確保 BAW 唔會盲目同意

### 8.2 Angel 角色（Independent Supporter）

- **人設**: 自動生成嘅 complement — 分析目標時從可行性/價值角度出發
- **權限**: 零執行權限（法庭階段）
- **獨立性**: 唔知道魔鬼講咗咩，純粹從自己角度分析
- **輸出**: 純文字分析 + `[Angel: Y/10]` 分數
- **目的**: 提供真實嘅支持觀點，確保 BAW 睇到機會同可能性

### 8.3 BAW 中立角色

- BAW（系統本身）唔係天使，而係中立嘅聆聽者
- 收到兩個獨立分析後，BAW 用常識同判斷力 synthesise
- BAW 嘅回應唔係「天使嘅回應」—— 係 BAW 自己嘅中立判斷
- 可以同意魔鬼多啲、天使多啲、或者兩邊都唔完全同意
- **唔討好用家** — 用家要求唔一定合理，BAW 會指出

### 8.4 辯論階段（互動模式）

- BAW 俾出中立分析後，用家可以回應
- 用家 ↔ Agent 來回討論
- BAW 可以堅持己見、讓步、或者提出替代方案
- 直至雙方達成最終共識

### 8.5 執行階段（法庭之後）

- 結論確立後，BAW 進入執行模式
- 唔會重新開庭（結論已確立）
- Plan → Step → Verify → Recover
- 執行失敗時唔問用家 — 自動 retry/replan/rollback
- 所有策略用盡先通知

### 8.6 熄咗法庭

```bash
baw --cfg set adversarial.enabled false
# 或者 config.yaml
adversarial:
  enabled: false
```

---

## 9. Tool Degradation 機制

每個 tool 有 fallback chain，失敗時自動降級：

### bash

1. 原始 timeout → 失敗
2. Timeout 加倍（up to 300s）→ 再失敗
3. Parent directory fallback（`cd .. && ...`）→ 再失敗
4. 上報策略失敗，replan

### write_file

1. 原始路徑 → Permission denied
2. `/tmp/` fallback（同檔名）→ 成功
3. 記錄到 file history

### web_search

1. 原始 query → 無結果
2. 縮短 query（取關鍵字）→ 無結果
3. 換 provider（如果有 multiple providers）
4. 回報無結果

### 策略追蹤

- `strategies_tried: []` list 記錄已嘗試策略
- `MAX_CONSECUTIVE_FAILURES = 3` → 同一策略失敗 3 次換下個
- `MAX_TOTAL_FAILURES = 6` → 全部失敗上報法庭

---

## 10. 已知問題 & 修正記錄

### 10.1 已修正

| 問題 | 日期 | 修正 |
|------|------|------|
| Kimi K2.6 thinking mode `content` = None | 2026-06-07 | `model_kwargs.disable_reasoning: true` |
| `--help` epilog 冇 tool list | 2026-06-07 | 加 `epilog` 參數到 argparse |
| regex over-escape `\s` → `\\s` in fact_checker | 2026-06-07 | 修正為 `\s`（一個 backslash） |
| Clarify 4-選-1 UI 唔好用 | 2026-06-06 | 用文字 option list + 即刻執行 |

### 10.2 注意事項

- **MiniMax M2.5**: 401 auth error（主系統用 DeepSeek，MiniMax 似乎 key 有問題）
- **SSH key**: 用 `id_cornreform` 連 `github.com-cornreform` host（`~/.ssh/config`）
- **auto-commit**: 每 6 小時 cron job（job_id: `b73866740e51`）
- **GH Pages**: 需要手動 enable Settings → Pages → main /docs

### 10.3 安全注意

- API keys 唔寫入 git（.gitignore exclude `.env` 同 `config.yaml`）
- SSH key 專用 `id_cornreform` 唔共用其他 service
- Permission 引擎預設 block `sudo`、`rm -rf`、`/etc/*`、`*.pem`、`*.key`

---

## 11. 如何擴展

### 11.1 加新 Tool

```python
# tools/my_tool.py
from baw.core.tools import register

def my_handler(param1, param2):
    # do something
    return result

register(
    name="my_tool",
    description="What this tool does",
    handler=my_handler,
    parameters={
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "..."},
            "param2": {"type": "integer", "description": "..."},
        },
        "required": ["param1"],
    },
    risk_level="medium",
)
```

### 11.2 加新 LLM Protocol

```python
# core/llm.py 最後加
def my_protocol_handler(model, messages, tools, **kw):
    # 實作同 anthropic/google handler 類似結構
    return LLMResponse(...)

register_protocol("my-protocol", my_protocol_handler)
```

### 11.3 加新 Search Provider

見 [7.4 Search Provider 加新 Provider](#74-加新-provider)

### 11.4 加新 Tone

```yaml
# config.yaml
tone:
  profiles:
    executive:
      description: "C-level brief — bullet points, key metrics, no filler"
```

### 11.5 Self-Learning Skills

```bash
# 自動學習
baw --learn-skill "每個星期日晚上 check disk usage，如果超過 85% send 一個 summary"
# 從 URL 學習
baw --learn-url "https://example.com/backup-workflow.md"
```

---

## 12. Roadmap

### 已實現 (v1.0.0 — 測試版)

- [x] Angel/Devil 雙魂法庭
- [x] 三種執行模式 (quick/hybrid/tight)
- [x] 協議無關 LLM (3 protocols)
- [x] 內置工具 (bash/read/write/web_search)
- [x] 永不放棄哲學 (6 strategies)
- [x] 三級權限引擎
- [x] 統一記憶 + 事實查證
- [x] 語氣設定 (6 profiles)
- [x] Scheduler 排程
- [x] Skills 技能系統
- [x] 自我學習技能
- [x] Async TaskManager (max 3 concurrent)
- [x] GitHub 整合
- [x] HTML Dashboard
- [x] Setup Wizard + Config CLI
- [x] 互動式 Chat 介面 (Tab 補全)
- [x] BTW 快捷模式
- [x] Tool degradation chains
- [x] Per-step verify (default off)
- [x] File history + auto git
- [x] 每週自我 dreaming
- [x] Bilingual docs (GitHub Pages)

### 考慮中

- [ ] Telegram bot 整合（接收 message 自動 run）
- [ ] 正式 testing suite（目前靠 functional test）
- [ ] Docker 支援
- [ ] Web UI
- [ ] Plugin / extension marketplace
- [ ] Multi-user support
- [ ] Streaming output (live LLM token display)

---

*Last updated: 2026-06-07*
*Maintained by: Sticky*
