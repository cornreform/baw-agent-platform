# BAW Knowledge Base — 開發記憶庫

> **System**: BAW (Black And White)
> **Version**: v0.12 — latest
> **Versioning**: Snapshot-based — see [VERSION-WORKFLOW.md](VERSION-WORKFLOW.md)
> **Start Date**: 2026-06-07
> **Developers**: Sunny + Sticky (Hermes Agent)
> **Repo**: https://github.com/cornreform/baw-agent-platform
> **Docs Site**: https://cornreform.github.io/baw-agent-platform/

---

## Table of Contents / 目錄

1. [Design Philosophy / 設計哲學](#1-design-philosophy)
2. [Architecture Map / 架構地圖](#2-architecture-map)
3. [Development Timeline / 開發歷程](#3-development-timeline)
4. [Design Decision Records / 設計決策記錄](#4-design-decision-records)
5. [Config Reference / Config 參照](#5-config-reference)
6. [LLM Provider Setup / LLM Provider 設定](#6-llm-provider-setup)
7. [Search Provider System / Search Provider 系統](#7-search-provider-system)
8. [Angel/Devil Court Specs / 天使/魔鬼法庭細則](#8-angeldevil-court-specs)
9. [Tool Degradation / Tool Degradation 機制](#9-tool-degradation)
10. [Known Issues & Fixes / 已知問題 & 修正記錄](#10-known-issues--fixes)
11. [How to Extend / 如何擴展](#11-how-to-extend)
12. [Roadmap / Roadmap](#12-roadmap)

---

## 1. Design Philosophy

### 1.1 Never Ask the User / 永不問用戶

BAW's golden rule: **solve problems yourself — never throw them back at the user.**

> BAW 嘅黃金法則：**遇到問題自己解決，唔好拋返俾用戶。**

- Fail → retry → replan → rollback → switch strategies → only report after exhausting all
- Tool timeout → double timeout → parent directory fallback → /tmp/ fallback → replan
- Track `strategies_tried` list, switch after 3 consecutive same-strategy failures
- Only escalate to Angel/Devil court after 6 total failures

### 1.2 Never Surrender / 永不放棄

BAW never gives up on a sub-goal. Trying different approaches is mandatory:

> BAW 唔會直接放棄一個子目標。嘗試不同方法係 mandatory：

- Checkpoint save before each step
- If verify FAIL → auto-recover
- Recovery order: retry → replan → rollback
- Only report when all recovery strategies fail

### 1.3 Angel/Devil Court / Angel/Devil 法庭

- Devil = opposition voice, **zero tool permissions**, always speaks first
- Angel = executor voice, has all tools, decides only after hearing Devil
- Devil score > Angel score → BLOCK
- Court runs once per user turn (in tight mode)
- Devil persona is auto-generated foil — the more trusting Angel is, the more skeptical Devil becomes

### 1.4 Protocol-Agnostic / 協議無關

- LLM communication protocol abstraction: `register_protocol(name, handler_fn)`
- Three built-in protocols: `openai-chat`, `anthropic`, `google`
- Provider config: `base_url` + `api_key_env` + `protocol` + `models[]`
- Built-in auto-fallback: primary fails → automatic fallback

---

## 2. Architecture Map

### 2.1 Directory Structure / 目錄結構

```
baw/                        ← Code repo
├── baw                     CLI entry point (Python)
├── core/                   Core modules (26+ files)
│   ├── loop.py             Agent loop
│   ├── llm.py              LLM abstraction (protocol-agnostic)
│   ├── adversarial.py      Angel/Devil court (v2 — parallel independent)
│   ├── tools.py            Tool registry
│   ├── permission.py       3-tier permission engine
│   ├── memory.py           Memory JSONL store
│   ├── context.py          Context manager
│   ├── fact_checker.py     Fact checking
│   ├── tone.py             Tone profiles (6 modes)
│   ├── scheduler.py        Cron daemon
│   ├── skills.py           YAML skill system
│   ├── learn.py            Self-learning
│   ├── board.py            HTML dashboard
│   ├── task_manager.py     Async task manager
│   ├── github.py           GitHub integration
│   ├── search.py           Open search provider system
│   ├── setup.py            Setup wizard + Config CLI
│   ├── commands.py         Slash commands (with 60s cache)
│   ├── display.py          Step display formatter
│   ├── dream.py            Weekly self-curation
│   ├── checkpoint.py       Checkpoint / rollback
│   ├── degradation.py      Tool degradation chains
│   ├── file_history.py     File versioning (SHA256)
│   ├── autosave.py         Auto git commit
│   ├── render.py           HTML renderers
│   └── verifier.py         Per-step LLM verify
├── tools/                  Built-in tools (4 files)
│   ├── bash.py
│   ├── read_file.py
│   ├── write_file.py
│   └── web_search.py
├── search_providers/       Search provider plugins
├── config.yaml             Default config
├── docs/                   GitHub Pages documentation
│   └── index.html          Dark-themed bilingual docs site
├── knowledge/              Development knowledge base
├── BAW-INTRODUCTION.html   Full introduction
└── BAW-PLAN.html           Original design document

~/.baw/                     ← User config directory
├── config.yaml             User config
├── SOUL.md                 Soul / behavioral rules
├── .env                    API keys
├── memory/store.jsonl      Memory store
├── memory/edges.json       Memory relationship graph
├── history/manifest.jsonl  File version history
├── schedule.yaml           Schedule definitions
├── schedule_state.json     Schedule state
├── skills/*.yaml           Installed skills
├── tasks/                  Background task output
└── dashboard.html          Generated system dashboard
```

### 2.2 Module Dependency Graph / Module 相依關係

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

### 2.3 Agent Loop Flow / Agent Loop 流程 (tight mode)

```
User prompt
    │
    ▼
[Phase 1] Plan
    ├── Angel generates step plan
    └── Devil reviews plan
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

## 3. Development Timeline

### Day 1: 2026-06-07 (Intensive Development Day)

| Time | Commit | Event |
|------|--------|-------|
| 13:38 | `d699a15` | **Init**: BAW Agent Platform v3 — from scratch: core loop + LLM + tools + memory + adversarial + CLI |
| 14:07 | `dab60e9` | **Kimi K2.6**: Added Kimi as primary model with auto-fallback |
| 14:07 | `738c648` | **Config fix**: Fixed config.sample.yaml indentation |
| 14:41 | `7f8febc` | **Search Registry**: Open search provider registry with DuckDuckGo |
| 15:03 | `f6b32c1` | **Self-improving**: Self-improvement loop + checkpoint system |
| 15:29 | `8fc824a` | **P0 complete**: web_search + fact checker + HTML rendering + cost tracker |
| 15:46 | `4be7471` | **Bug fix**: regex over-escape in claim patterns |
| 15:58 | `cc3a165` | **Polish**: add tool list to --help |
| 16:05 | `2897537` | **P1: Slash commands**: 12 commands + CLI integration |
| 16:14 | `97332e2` | **P1: /rethink /court /fresh**: Three advanced slash commands |
| 16:21 | `7d64e45` | **P1: Tool degradation**: bash/write/search fallback chains |
| 17:00 | `48b52ad` | **3 modes + display**: quick/hybrid/tight modes + display overhaul |
| 17:08 | `428ddbb` | **Scheduler + Skills + Dashboard**: Three infra modules |
| 17:32 | `c0ebddb` | **Self-learning**: `--learn-skill` + `--learn-url` |
| 17:55 | `eeca807` | **Async TaskManager + GitHub**: Background tasks + GH integration |
| 17:57 | `0e9da35` | **Setup wizard + Config CLI + Chat interface**: Final UX layer |
| 18:15 | `89f7927` | **Bilingual README + docs site**: GitHub Pages documentation |
| 18:30 | `0aaf18d` | **English-first**: README + docs default to English |

**Total: 18 actual dev commits** + 10 auto-commits (BAW agent self-recorded). Full platform from zero in one day.

---

## 4. Design Decision Records

### D-001: Platform Name "BAW"

- **Date**: 2026-06-07
- **Original**: Stark (German for "strong, clean")
- **Changed to**: BAW (Black And White)
- **Reason**: User has two dogs (black & white), Angel/Devil philosophy fits better

### D-002: Angel/Devil Dual-Soul Court (v2 — Parallel Independent Analysis)

- **Date**: 2026-06-07
- **Previous (v1, deprecated)**: Devil spoke first, Angel responded after — sequential analysis biased Angel's judgment
- **Current (v2)**: Devil and Angel analyze the SAME goal independently and simultaneously, unaware of each other
- **Reason**: Eliminate sequential bias. Both voices reflect genuine independent views.
- **Court vs Execution separation**: Court phase has no execution rights; Execution phase has no court

### D-003: Protocol-Agnostic LLM Architecture

- **Date**: 2026-06-07
- **Decision**: `register_protocol()` abstraction layer, no vendor hardcoding
- **Reason**: Avoid vendor lock-in, users freely switch models

### D-004: Single Unified Memory API

- **Date**: 2026-06-07
- **Decision**: `remember()` + `search()` single interface
- **Storage**: JSONL append-only (`~/.baw/memory/store.jsonl`)

### D-005: 3-Tier Permissions (not binary)

- **Date**: 2026-06-07
- **Decision**: High (block) / Medium (prompt) / Low (allow)
- **Reason**: Binary allow/deny too coarse

### D-006: Per-Step Verify Disabled by Default

- **Date**: 2026-06-07
- **Decision**: `verify.enabled: false` by default — too expensive otherwise

### D-007: File Versioning + Auto Git

- **Date**: 2026-06-07
- **Decision**: ISO timestamp + SHA256 + auto git commit on every write

### D-008: HTML for Internal Reports

- **Date**: 2026-06-07
- **Decision**: BAW internal output uses HTML, Telegram/CLI uses plain text

### D-009: Three Execution Modes

- **Date**: 2026-06-07
- **Decision**: Quick / Hybrid / Tight

### D-010: Six Tone Profiles

- **Date**: 2026-06-07
- **Decision**: casual / business / teaching / client-doc / ot-rt / stepwise

### D-011: Setup Wizard + Config CLI

- **Date**: 2026-06-07
- **Decision**: `baw --setup` interactive wizard + `baw --cfg set/get/list` CLI

### D-012: GitHub Pages Docs Site

- **Date**: 2026-06-07
- **Decision**: `docs/index.html` dark theme + language toggle (EN/繁)
- **Languages**: English default, Traditional Chinese toggle

### D-013: Model Auto-Routing (2026-06-09)

- **Date**: 2026-06-09
- **Decision**: Auto-route short queries → fast model (deepseek-v4-flash), long context → large-context model (MiniMax-M2.5)
- **Threshold**: >8,000 estimated tokens triggers long-model routing
- **Config**: `model.route.enabled` + `model.route.threshold_tokens`

### D-014: Exponential Backoff Retry (2026-06-09)

- **Date**: 2026-06-09
- **Decision**: Retry transient errors (429/503/timeout) up to 3x with 1s→2s→4s backoff
- **Non-retryable**: 401/403/400 errors skip retry, go straight to fallback

### D-015: Command Result Cache (2026-06-09)

- **Date**: 2026-06-09
- **Decision**: 60s TTL cache for static commands (/status, /help, /version, /tools)
- **Invalidation**: /model and /tone changes invalidate /status cache

### D-016: Docs Chain Pattern (2026-06-09)

- **Date**: 2026-06-09
- **Decision**: Implement Agent Zero / Space Agent's `agents.md` docs-chain pattern — before any file edit, agent reads root→directory→file-level documentation
- **Reason**: LLMs fail on large codebases not due to intelligence but context awareness. Throwing more tokens isn't the solution — giving exactly the right context is
- **Implementation**: `core/docs_chain.py` with `find_docs_chain()`, `read_docs_chain()`, `inject_docs_context()`. `/docs` slash command for manual chain inspection
- **Structure**: `docs/README.md` (root) → `docs/<dir>/README.md` (per-directory) → sibling `.md` per file
- **Inspiration**: https://www.youtube.com/watch?v=NVkRkioBXQc — "One markdown file just fixed AI coding forever" by Yan (Agent Zero)

---

## 5. Config Reference

### 5.1 Complete Config Key List

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | string | `tight` | Execution mode: quick/hybrid/tight |
| `model.default` | string | `deepseek-v4-flash` | Default LLM model ID |
| `model.fallback` | string | (same as default) | Fallback model ID |
| `model.route.enabled` | bool | `true` | Auto-route by message size |
| `model.route.short_model` | string | `deepseek-v4-flash` | Model for short queries |
| `model.route.long_model` | string | `MiniMax-M2.5` | Model for long context |
| `model.route.threshold_tokens` | int | `8000` | Token threshold for routing |
| `tone.default` | string | `casual` | Default tone profile |
| `adversarial.enabled` | bool | `true` | Enable Angel/Devil court |
| `adversarial.flag_threshold` | int | `0` | Devil score > this → flag |
| `adversarial.warn_threshold` | int | `2` | Devil score > this → warn not block |
| `verify.enabled` | bool | `false` | Per-step LLM verify |
| `fact_check.mode` | string | `normal` | off/normal/strict |

### 5.2 Provider Config Structure

```yaml
providers:
  <provider_name>:
    base_url: "https://api.example.com/v1"
    api_key_env: "ENV_VAR_NAME"    # Read API key from env var
    protocol: "openai-chat"        # or anthropic/google/custom
    models:
      - id: "model-id"
        context_window: 65536
        vision: false
        cost_per_1m_input: 0.30
        cost_per_1m_output: 1.20
        temperature: 0.7           # Optional, override default
        model_kwargs:              # Optional, extra LLM body params
          disable_reasoning: true
```

### 5.3 Permission Config Structure

```yaml
permissions:
  risk_levels:
    high:       # ⛔ Blocked
      - path: "/etc/*"
      - cmd_prefix: "sudo"
      - cmd_prefix: "rm -rf"
    medium:     # ⚠️ Prompt user
      - tool: "write_file"
      - tool: "bash"
    low:        # ✅ Allowed
      - tool: "read_file"
```

---

## 6. LLM Provider Setup

### 6.1 Supported Providers

| Provider | Protocol | Example Models | Status |
|----------|----------|---------------|--------|
| DeepSeek | openai-chat | deepseek-v4-flash, deepseek-reasoner | **Enabled (default)** |
| MiniMax | openai-chat | MiniMax-M2.5 | **Enabled** |
| Kimi (Moonshot) | openai-chat | kimi-k2.6 | **Enabled (fallback)** |
| Anthropic | anthropic | claude-sonnet-4 | Configured (commented) |
| Google | google | gemini-2.5-pro | Configured (commented) |

### 6.2 Adding a New Provider

```yaml
# 1. Add provider entry in config.yaml
providers:
  groq:
    base_url: "https://api.groq.com/openai/v1"
    api_key_env: "GROQ_API_KEY"
    protocol: "openai-chat"  # Use this for OpenAI-compatible APIs
    models:
      - id: "llama-3.3-70b-versatile"
        context_window: 32768
        vision: false
        cost_per_1m_input: 0.59
        cost_per_1m_output: 0.79

# 2. For non-standard protocols, add a handler in core/llm.py
from .llm import register_protocol
def my_custom_handler(model, messages, tools, **kw):
    # custom logic here
    pass
register_protocol("my-protocol", my_custom_handler)
```

### 6.3 Kimi Thinking Mode Bug

**Issue**: Kimi K2.6 defaults to thinking mode, causing `content` to return `None` (thinking goes into `reasoning_content` field).
**Fix**: `model_kwargs.disable_reasoning: true` prevents empty content responses.
**Affected models**: Kimi K2.6 (`api.moonshot.ai`)

---

## 7. Search Provider System

### 7.1 Open Registration / 開放註冊機制

Search providers are pluggable: drop a file in `search_providers/` implementing the interface, call `register_search_provider()`.

### 7.2 Built-in Providers / 內置 Provider

| Provider | API Key | Description |
|----------|---------|-------------|
| DuckDuckGo | Not needed | Free, uses `duckduckgo-search` library |

### 7.3 CLI Operations / CLI 操作

```bash
baw --search-provider list                  # List all providers
baw --search-provider guide duckduckgo      # Setup guide
baw --search-provider api duckduckgo        # API reference
baw --search-provider test duckduckgo "..." # Test search
```

### 7.4 Adding a New Provider / 加新 Provider

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

## 8. Angel/Devil Court Specs (v2 — Parallel Independent)

### 8.1 Devil Role (Independent Critic)

- **Persona**: Auto-generated foil — analyzes from risk/problem perspective
- **Permissions**: Zero execution rights — no tools, no bash, no file writes (court phase only)
- **Independence**: Does NOT know what Angel said; purely independent analysis
- **Output**: Plain text analysis + `[Devil: X/10]` score
- **Purpose**: Provide genuine opposition, ensure BAW doesn't blindly agree

### 8.2 Angel Role (Independent Supporter)

- **Persona**: Auto-generated complement — analyzes from feasibility/value perspective
- **Permissions**: Zero execution rights (court phase only)
- **Independence**: Does NOT know what Devil said; purely independent analysis
- **Output**: Plain text analysis + `[Angel: Y/10]` score
- **Purpose**: Provide genuine support, ensure BAW sees opportunities and possibilities

### 8.3 BAW's Neutral Role

- BAW (the system itself) is NOT Angel — it's a neutral listener
- After receiving two independent analyses, BAW synthesizes using common sense and judgment
- BAW's response is NOT "Angel's response" — it's BAW's own neutral judgment
- Can agree more with Devil, more with Angel, or partially with neither
- **Does not please the user** — user requests may not be reasonable; BAW points this out

### 8.4 Debate Phase (Interactive Mode)

- BAW presents neutral analysis; user can respond
- User ↔ Agent back-and-forth discussion
- BAW can hold ground, concede, or propose alternatives
- Until both sides reach final consensus

### 8.5 Execution Phase (After Court)

- Once the conclusion is reached, BAW enters execution mode
- No re-litigation — the debate is settled
- Plan → Step → Verify → Recover
- Does NOT ask user on execution failure — auto retry/replan/rollback
- Only notify after exhausting all strategies

### 8.6 Disabling the Court

```bash
baw --cfg set adversarial.enabled false
# or in config.yaml:
adversarial:
  enabled: false
```

---

## 9. Tool Degradation

Each tool has a fallback chain; on failure, automatically degrades:

| Tool | Degradation Chain |
|------|------------------|
| `bash` | 1. Double timeout → 2. Retry with parent dir → 3. Retry with /tmp |
| `write_file` | 1. Retry with parent dir → 2. Retry with /tmp → 3. Offer alternative path |
| `web_search` | 1. Simplify query (3 keywords) → 2. Try different provider |

---

## 10. Known Issues & Fixes

| Issue | Status | Fix |
|-------|--------|-----|
| Kimi thinking mode returns empty content | ✅ Fixed | `disable_reasoning: true` in model_kwargs |
| NPU dispatcher zombie restart loop (17,438x) | ✅ Fixed | Services disabled (scripts don't exist) |
| Duplicate ESPHome log watcher | ✅ Fixed | Killed duplicate |
| Step 1 display suppressed in live progress | ✅ Fixed | Removed `_step_idx > 0` guard |

---

## 11. How to Extend

### Adding a New Tool / 加新 Tool

1. Create `tools/my_tool.py` with `register_tool()`
2. Add permission rules in config.yaml
3. Optionally add degradation chain

### Adding a New Protocol / 加新 Protocol

```python
from baw.core.llm import register_protocol

def my_handler(model, messages, tools, temperature, max_tokens):
    # Custom API call logic
    return LLMResponse(...)

register_protocol("my-protocol", my_handler)
```

### Adding a New Tone Profile / 加新語氣

```yaml
tone:
  profiles:
    my_tone:
      description: "My custom tone description"
```

---

## 12. Roadmap

- [x] Core loop + LLM + tools + memory + adversarial + CLI
- [x] Slash commands + config CLI + setup wizard
- [x] Scheduler + skills + dashboard
- [x] Self-learning + background tasks + GitHub integration
- [x] Bilingual docs + GitHub Pages
- [x] 3-tier model selector with back button
- [x] Route recalculation goal pursuit
- [x] Message queue with dequeue
- [x] Exponential backoff retry
- [x] 60s TTL command cache
- [x] Auto model routing (short/long queries)
- [ ] Multi-agent swarm coordination
- [ ] Voice pipeline (STT → LLM → TTS)
- [ ] Plugin marketplace
- [ ] Web UI dashboard (beyond HTML)
