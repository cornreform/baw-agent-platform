# Changelog

All notable changes to BAW (Black And White) Agent Platform.

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
