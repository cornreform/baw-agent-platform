# Changelog

All notable changes to BAW (Black And White) Agent Platform.

## v0.20.3 — 2026-06-14

### 💬 Multi-Platform Messaging

- **Slack connector** (`core/messaging/slack.py`)
  - Socket Mode via WebSocket — no public URL needed
  - Auto-reconnect, DM + @mention response, channel/user allowlists
  - Message splitting for Slack 4K limit, full ack/envelope handling
- **Multi-platform setup wizard** (`core/setup.py`)
  - Interactive menu (1-6) for Telegram, Discord, Slack, Matrix, Signal, WhatsApp
  - Configure multiple platforms in one session
  - Environment variable fallbacks for all tokens
- **Full platform documentation**
  - README.md: per-platform `<details>` quick-setup guides for all 6 platforms
  - SETUP.md: platform difficulty table + platform-specific FAQ
  - config.sample.yaml: commented examples for all platforms

## v0.20.2 — 2026-06-14

### 🔧 CLI Installation UX Overhaul

- **Version consistency**: all files now report `v0.20.2` (install.sh, baw, setup.py, README badge)
- **install.sh**: auto-detects/installs `uv`, auto-installs Python 3.12 via uv, uses `uv pip`, auto-adds PATH, verifies `baw --version` at end
- **setup.py**: API keys validated in real-time (test request sent immediately), Telegram moved to optional last step, plan types explained before asking, every setting includes context
- **SETUP.md**: rewritten as full step-by-step guide (200+ lines) with model comparison table, API key reference, FAQ

## v0.20.1 — 2026-06-14

### 🏛️ Tribunal Model-Agnostic

- Tribunal no longer hardcodes any models
- Reads `tribunal.bench` and `tribunal.chief` from `config.yaml`
- Auto-detects from available models if `tribunal:` section missing
- Chief Justice optional — falls back to highest-confidence judge
- `/tribunal bench` shows current configuration
- `config.yaml`: added `tribunal:` section with examples

## v0.20.0 — 2026-06-14

### 🏛️ Tribunal — Multi-Model Consensus Engine

- Courtroom-inspired consensus: multiple "judges" evaluate independently, "Chief Justice" synthesises unified verdict
- Parallel execution via ThreadPoolExecutor
- Consensus scoring via semantic keyword overlap
- Minority opinion detection
- Cost estimation per ruling
- Telegram: `/tribunal <question>`
- Court integration: auto-triggered on Tier-2 disputes

### 🧪 Real-World Validator

- Zero mocks — every test hits REAL APIs, writes REAL files
- Validates: config, DeepSeek API, MiniMax API, evolve logging, memory R/W, Telegram bot, disk space, git repo, scheduler, watchdog
- Telegram: `/validate [subcommand]`

## v0.19.6 — 2026-06-14

### 🖼️ Telegram Test Suite

- `/test` — quick health check (7 items)
- `/test all` — full pytest suite (87 tests)
- `/test unit` — unit tests only
- `/test config|evolve|memory|watchdog|scheduler|git` — module tests

## v0.19.5 — 2026-06-14

### 🧘 Self-Evolution Roadmap Complete

- **Phase 1**: track_tool_call wired to loop.py, cron analyze every 6h, /doctor selftest, dry-run logic
- **Phase 2**: lightweight healthcheck, resource monitor, emergency cleanup
- **Phase 3**: dry-run approval system, pending_approvals queue
- **Phase 4**: behavior pattern detection, pattern library, auto-optimization proposals

---

## v0.19.0 — 2026-06-14

### ⚠️ Critical Fixes

- **Per-Chat Sequential Processing** (`core/messaging/*`)
  - Fixed "task jumping" (打尖): same chat can no longer spawn multiple concurrent threads. New messages are queued until the active task completes.
  - Added `_active_chats` set + `_chat_lock` to enforce one-active-task-per-chat.
  - Queue dispatcher re-checks chat busy state before spawning handler thread.

- **LLM Hard Timeout** (`core/llm.py`)
  - `_call_with_timeout()` now uses `ThreadPoolExecutor` + `future.result(timeout=90)` to prevent indefinite hangs on slow-streaming APIs.
  - Added 5-minute overall timeout to `delegate_task` sub-agent loop.

- **Chat Bypass Identity** (`core/messaging/__init__.py`)
  - Chat bypass now loads `SOUL.md` into system prompt.
  - BAW no longer answers "我是一個語言模型" — it correctly identifies as an agent with tool-execution capabilities.

- **Config Path Consistency** (`runtime`)
  - Fixed dual-config drift: `/app/config.yaml` (repo) and `~/.baw/config.yaml` (runtime) had diverged to different contents.
  - Symlinked `~/.baw/config.yaml → /app/config.yaml` so all code paths read the same file.
  - `read_file` tool no longer returns "file not found" when called on `~/.baw/config.yaml`.

### 🔧 Sub-Agent Tool Registry

- **Expanded sub-agent tool set** (`tools/delegate_task.py`)
  - Previously only 6 tools were registered for sub-agents: `bash`, `read_file`, `write_file`, `web_search`, `vision`, `tts`.
  - Now **13 tools**: added `web_extract`, `search_files`, `patch`, `memory`, `todo`, `image_generate`, `install`.
  - Inline executor prompt also updated to import and reference all new tools.

### 🔧 Inline Executor Hardening

- **Tool return-type awareness** (`core/loop.py`)
  - Added explicit instruction: `web_search()` and `web_extract()` return **plain text**, NOT JSON.
  - Prevents LLM from generating `json.loads(result)` on HTML/text output.
  - Added `web_extract` to both `_SUBAGENT_REQUIRED_PATTERNS` and `_SUBAGENT_REQUIRED_PATTERNS_API` so web-fetch steps always go through sub-agent (proper tool call) instead of inline code generation.

### 🔧 New Tools

- **`install` tool** (`tools/install.py`)
  - Self-healing package installation: `npm`, `pip`, `apt`, `auto-detect`.
  - Falls back to local install (`~/npm/`) if global fails.
  - Used by vision tool for `mmx-cli` self-installation.

- **`selftest` tool** (`tools/selftest.py`)
  - Container healthcheck: verifies `baw --version`, `/app` paths, writable data dir, API key presence.

### 🔧 Vision Tool Self-Healing

- **Auto-install `mmx-cli`** (`tools/vision.py`)
  - If `mmx` is not found, vision tool automatically calls `install('mmx-cli', 'npm')` before falling back to MiniMax direct API.

### 🔧 Safety & Hardening

- **Safety POST-split** (`core/loop.py`)
  - Sensitive-file protection now runs **per-step** (after multi-task split) rather than pre-split, preventing bypass via task concatenation.

- **Model identity guard** (`core/messaging/__init__.py`)
  - Chat bypass system prompt explicitly instructs: "你是 BAW 系統的一部分" — prevents model from hallucinating other identities.

### 🔧 Messaging Reliability

- **Intent Shift Detection** (`core/messaging/__init__.py`)
  - Keyword-overlap heuristic (≤25%) triggers topic shift, clears old context to prevent cross-contamination.

- **Session auto-compression** (`core/messaging/__init__.py`)
  - When token usage > 80%, auto-summarizes older messages via LLM and writes to memory.

### 🔧 Bug Fixes

- **web_extract parameter defense** (`tools/__init__.py`)
  - Handles both `urls` (list) and `url` (string) parameter shapes.
  - Returns clear error for non-HTTP(S) URLs.

- **TTS tool trigger** (`tools/__init__.py`)
  - Fixed keyword matching for voice/audio/tts triggers.

- **bash sensitive-file protection** (`tools/bash.py`)
  - Strengthened regex patterns for `/etc/shadow`, SSH keys, `.env`, API keys.

---

## v0.18.2 — 2026-06-13

- Router tier fix: `tier_preferences` model_id now passed to `delegate_task` to prevent silent override.
- Memory dedup: Jaccard keyword fallback + 24h update window.
- Pronoun resolution ("佢") → name mapping.

## v0.18.1 — 2026-06-12

- Direct Shortcut safety: `_is_sensitive` defense-in-depth.
- Chat Bypass stateful: loads session + memory, saves messages.

## v0.18.0 — 2026-06-11

- 3-layer anti-fake completion defense (L1/L2/L3 gates).
- Inline executor anti-fake output validation.
- Subprocess repr added to fake markers.

## Earlier versions

See git history for v0.17.x and below.
