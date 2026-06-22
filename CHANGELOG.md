# Changelog

All notable changes to BAW (Black And White) Agent Platform.

## v1.1.0 — 2026-06-17 (Full Independence 🚀)

### Phase 1 — Self Code Management (Git + Docker)

- **Git tool** — BAW can commit, push, pull, status, log its own repo
- **Docker tool** — BAW can build, restart, logs, cleanup its own container
- **Self-deploy pipeline**: code → commit → push → build → restart

### Phase 2 — Self Operation (Runtime Management)

- **`system` tool** — container health, systemd services, cron jobs, disk usage
- **`self_diagnose` tool** — 7-point health check (container, providers, tools, memory, disk, config, cron) with score %
- **`resource_monitor` tool** — disk/memory report, stale session cleanup, auto-cleanup
- **Auto-cleanup cron** — daily 4am cleanup of stale sessions and logs

### Phase 3 — Self Knowledge (Architecture Awareness)

- **`self_capabilities` tool** — scan tools + providers + config, describe own capabilities
- **SOUL.md architecture section** — complete system architecture, code structure, identity boundaries
- **Identity boundaries** — BAW knows own code/data vs system files

### Phase 4 — Self Extension (Tool Creation)

- **`tool_generate` tool** — describe a tool → LLM generates code → syntax check → register → smoke test
- **Code generation safety** — syntax validation before registration, auto-cleanup on failure

### Phase 5 — Self Hosting (Migration)

- **`self_migrate` tool** — analyze target machine, export data (config/memory/sessions/SOUL), generate bootstrap script
- **Full migration pipeline**: analyze → export → bootstrap in one command

### Standalone Install Readiness (6 P0 fixes)

- **install.sh**: fixed ordering bug (dependency install before clone), Docker detection, SOUL.md bootstrap, PEP 668 support
- **docker-compose.yml**: removed hardcoded `/home/radxa/` paths → use `~/` tilde expansion
- **deploy/baw-docker.service**: parameterized user/path
- **core/doctor.py**: replaced hardcoded `/home/radxa/baw` with dynamic `_REPO_ROOT`
- **cli/commands/tools_cmd.py**: fixed hardcoded paths in tool generation templates
- **config.sample.yaml**: removed real Telegram token, replaced with placeholder

### Provider Fixes

- **Default model**: MiniMax-M3 → `step-3.7-flash` (MiniMax-M3 was failing)
- **Fallback**: added `deepseek-v4-flash` as proper fallback (was same as primary)
- **Circuit breaker**: fast-skip when a provider fails ≥8 consecutive times
- **`_delegation_results` bug**: fixed NameError (referenced but never initialized)

### UX Improvements

- **Progress reporting**: long tasks (>2min) auto-send periodic "Still working" updates
- **Delegation vs Inline**: distinct ╔═══ 巳分工 ═══╗ box marker for sub-agent results
- **System prompt rules**: both quick/regular modes have delegation vs inline presentation rules
- **SOUL.md**: comprehensive architecture, delegation rules, long-task progress rules


## v1.0.0 — 2026-06-17 (Self-Evolution Milestone 🎯)

### Self-Evolution System — 5 Phases Complete

**Phase A — Foundation Gates**
- A1: Fabrication Gate — code-enforced verify loop after every write tool call (10/10)
- A2: Config Drift Auto-Fix — 4 patterns auto-detected + enforced before every LLM call
- A3: Learning Threshold — 5→2 corrections for faster adaptation

**Phase B — Semantic Understanding**
- B1: LLM-Assisted Classification — 5/5 correction types correctly identified
- B2: Unified Evolution Pipeline — 6 stages: dream → health → memory → LLM → SOUL → code

**Phase C — Self-Containment**
- C1: Internal Scheduler — SOUL health check migrated from cron, 24h/6h/10min cycles
- C2: Dead State Recovery — loop detection (≥5 consecutive fails) + graceful restart

**Phase D — Code-Level Self-Improvement**
- D1: Auto-Patching — LLM generates code patches from failure patterns, syntax-verified
- D2: Self-Testing Pipeline — change → syntax verify → pytest → rollback on failure

### Audit: 6 Critical + 6 High Fixes (2026-06-17)
- **EXC-1**: Sandbox escape fix — removed `type`/`getattr`/`setattr` from SAFE_BUILTINS
- **SCHED-1**: Shell command whitelist for `!` prefix in scheduler
- **EVO-1**: Recursion guard — `@_depth_guard` decorator, max 3 nested optimize calls
- **BASH-2**: Env sanitization — subprocess stripped of `_API_KEY`/`_SECRET`/`_TOKEN`
- **WRF-1**: Path traversal sandbox — writes restricted to `~/baw/` or user home
- **MEM-1**: Atomic memory write — `.tmp` → `.replace()` crash-safe pattern
- **TOL-1**: Non-idempotent tools skip retry on timeout
- **LOOP-1**: Empty prompt explicit type-TREATMENT
- **EXC-2**: Unicode NFKC normalization for dangerous pattern detection

## v0.22.0 — 2026-06-16

### Self-Evolution: Phase C — Self-Containment

- C1: Internal Scheduler — SOUL health check migrated from cron, 24h/6h/10min cycles
- C2: Dead State Recovery — loop detection + graceful restart

### 🏥 Reliability Pillars (6.5 → 9.5/10)

- **P0: Config routing fix** — `image→step-image-edit-2`, `tts→stepaudio-2.5-tts`, `vision→MiniMax-M3`, judge `3.5→3.7`, +angel_model, +timeout
- **P0: Never Surrender** — pursuit limit 2→5, recalc 3→5, skip→alternative LLM call, diagnosis-on-exhaust
- **P1: Memory decay persistence** — `decay()` now saves scores, auto-compress at 500 entries
- **P1: Tribunal E2E tests** — 8 tests covering court imports, tiers, verdicts, docket, night court
- **P2: Active Challenge Gate** — Devil ≥9 blocks, ≥7 warns, ≥4 notes — BAW pushes back on bad decisions
- **P2: Skills audit** — no `--break-system-packages`, 2 safe skills verified

### 🛡️ Auto-Recovery

- **Stuck step killer**: kills sub-agent after 10min, triggers replan
- **Health watchdog**: 60s polling, score tracking, auto-alert
- **Health dashboard**: 10-point check → `/doctor`, `/watchdog` — current: 9.5/10
- **Backup system**: daily backup (02:00 UTC), `/backup` slash command, keep 7 days, restore support
- **Monitoring**: error rate tracker, weekly reliability report, threshold alerts

### 🧪 Test Coverage (15 → 157 tests)

- 42 new unit tests: loop pursuit branches, llm fallback chain, messaging edges, config integrity
- 14 P0 critical path validation tests
- 8 Tribunal E2E tests
- 6 P1-P2 challenge + skills tests

## v0.20.4 — 2026-06-14

### 🔧 Bug Fixes

- **Court merged mode**: Devil and Angel content were identical — now correctly split at [DEVIL: X/10] / [ANGEL: X/10] markers
- **Message truncation removed**: Telegram no longer cuts messages at 1800 chars; only splits at Telegram's 4000 limit

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

## v1.14.2 — 2026-06-22 (Community files + version sync)

### Version sync
- All files now report v1.14.2: baw, core/__init__.py, commands.py, setup.py, install.sh, README.md, SETUP.md

### Community health
- CONTRIBUTING.md: friendly, low-friction guide — open issue first, no CLA
- Bug report template: 5-field YAML template
- Feature request template: 3-field YAML template

### README improvements
- GitHub stars + last-commit badges added
- Humble description paragraph with discoverable keywords (multi-model, autonomous, built from scratch)
- CLI demo section showing real `baw --version` output
- Fixed broken independence roadmap table (was corrupted with git help dump artifact)
- Replaced old v0.x changelog with current v1.8-v1.14 highlights

### GitHub repo settings
- Description, topics (7), releases (v1.8-v1.14) all updated
- v1.14.1 + v1.14.2 releases created

## v1.14.0 — 2026-06-22 (Raise autonomy limits: tool cap, mode tokens, scaling guard)

### ⚙️ Autonomy Limits Raised

- **MAX_TOOL_TURNS 25→50** — base tool loop cap doubled, giving BAW more room for complex multi-step workflows
- **Complexity scaling**: moderate 75→100, complex 100→150 — scaling multipliers adjusted proportionally
- **Scaling guard fix**: was hardcoded to `25` — now uses `MAX_TOOL_TURNS` constant, so scaling works correctly for any base value
- **bawrun.py hardcoded 15→50** — the runner was bypassing all complexity scaling with its own 15-turn limit; now respects the system-wide cap
- **Mode max tokens doubled**: quick 4096→8192, hybrid 8192→16384, tight 16384→32768, auto 5120→12288, focus 16384→32768 — ensures complex outputs aren't truncated mid-synthesis

---

## v1.13.0 — 2026-06-22 (Full system audit fixes: slash commands, CLI, installer)

### 🔧 Slash Command Hardening

- **/fresh handler** added to `route()` — was registered in help text and menu but dead, silently falling through to generic dispatch
- **/v alias removed** from validate (conflicted with /version)
- **/task bare usage** — added missing handler for task without arguments (was falling through to _dispatch)
- **/models now accurate** — shows auxiliary model roles as documented, not just model names

### 📦 Installer + Version Sync

- **install.sh version sync**: v1.1.0 → v1.12.1 (was 12 versions out of date)
- **baw standalone script version sync**: 0.22.0 → 1.12.1 (was 18 versions out of date)
- **`__version__`** added to `core/__init__.py` as single source of truth

### 🐧 Systemd Service Fixes

- **baw.service**: now uses native systemd specifiers `%h`/`%u` — no more fragile `sed` substitution at install time
- **baw-docker.service**: same `%h`/`%u` fix, removed hardcoded `YOUR_USERNAME` placeholder
- **install.sh**: auto-detects Docker GID for `docker-compose.yml` — no more GID mismatch errors
- **install.sh**: removed obsolete `%HOME%`/`%USER%` sed substitution that broke on some distros
- **docker-compose.yml**: GID documented with `getent group docker` command in comment
- **install.sh**: auto-fixes Docker GID mismatch during install

---

## v1.12.1 — 2026-06-22 (Fix: POST-TURN VERIFICATION false positives + broken unicode filters)

### 🐞 Bug Fixes

Three fixes in `_verify_post_turn_claims` Pattern 2 (config claim detection):

1. **Code reference exclusion** — tokens containing `=` (e.g. `fresh_start=True`), starting with backtick or `/`, or containing `.py` are now skipped. Prevents code documentation and command examples from being flagged as config claims.

2. **Broken Chinese-only filter** — was using raw-string double-backslash `r'^[\\u4e00-\\u9fff]+$'` which matched literal `\\u4e00` characters, NOT actual Chinese Unicode. Changed to `'^[\u4e00-\u9fff]+$'` — now correctly detects Chinese text.

3. **Broken punctuation filter** — was checking for literal text `'\\u3001'` instead of the actual Unicode character `'、'`. Same fix applied for `'\\uff0c'` (→ `'，'`) and `'\\u3002'` (→ `'。'`).

---

## v1.12.0 — 2026-06-22 (Fix: English reasoning leakage + court verdict footer stub)

### 🐞 Bug Fixes

**1. English reasoning leakage** — system prompt contained a loophole: "reasoning chain can be in English". Removed this permission entirely. Reasoning MUST be in Cantonese/Traditional Chinese at all stages. Added explicit "First word = first word user sees" clarity. The English permission loophole is now closed at the system prompt level.

**2. Court verdict footer stub** — footer implementation at line 2481-2482 was:
```python
if _has_real_content: pass
```
The `pass` meant the verdict footer was never appended. Now appends:
```
⚖️ 法庭 {scores} | {agreement} | {gap}
```
Both fixes address user report of missing footer info and visible English reasoning in BAW output.

---

## v1.11.0 — 2026-06-22 (Phase 13: Documentation)

### 📚 Documentation

- **ARCHITECTURE.md** — comprehensive architecture document covering the Black & White Court philosophy (黑白法庭哲學), four-tier adjudication system, and architectural differences from Hermes/OpenClaw
- **DEVGUIDE.md** — development guide with coding conventions (開發守則), project structure map (專案結構), debugging guide (調試指南), and FAQ (常見問題)

### 🧪 Tests

- 27 tests all passing (court system, delivery log, evolve pipeline)
- Coverage maintained across all test suites

---

## v1.10.0 — 2026-06-22 (Phase 11-12: Error Recovery + Testing)

### 🛡️ Phase 11 — Error Recovery Hardening

- **Search timeout tightened**: `rg` (ripgrep) timeout reduced from 30s to 15s; Python fallback now also times out at 15s and skips files over 10MB
- **rg I/O error resilience**: partial results returned on I/O errors instead of hard failure — BAW can work with incomplete data
- **Synthesis enforcement**: when sending a MEDIA file (image/audio/video), BAW must also include a text description — no more silent file-only sends
- **Output synthesis**: ensures all file sends are accompanied by human-readable explanations of what was sent and why

### 🧪 Phase 12 — Test Coverage Expansion

- **27 new tests**:
  - Court system: 18 tests covering tier routing, verdict generation, score tracking
  - Delivery log: 5 tests for delivery confirmation, persistence, and recovery
  - Evolve pipeline: 4 tests for pattern analysis, lesson learning, and auto-patching
- **Test isolation fix**: replaced global `_LOG_FILE` with `_log_path()` method for file-scoped test isolation
- All 27 tests passing

---

## v1.9.0 — 2026-06-22 (Phase 9-10: Court System Hardening + Self-Evolution)

### 🏛️ Phase 9 — Court System Hardening

- **TIER_0 fast lane** now supports tool execution (was LLM-only) — simple questions can produce richer responses with live tool data
- **TIER_3 Supreme Court**: multi-model appellate review process — when lower tiers disagree, a panel of models deliberates and produces a unified appellate verdict
- **Court default on**: court is now enabled by default for hybrid/tight modes (was opt-in) — no more silent bypasses
- **New CLI subcommands**: `/court recent` (last N rulings) and `/court detail <id>` (full ruling with scores)
- **Court telemetry**: score drift detection — tracks if judge scores are trending up/down over time for model quality monitoring

### 🧬 Phase 10 — Self-Evolution Integration

- **`_analyze_court_scores()`**: detects retry rate drift and appeal rate drift — flags when court performance is degrading
- **Court score analysis** integrated into weekly evolution pipeline — court outcomes now feed into BAW's self-improvement cycle
- **Learned lessons summary restored** — evolution reports now include what was learned from court pattern analysis
- **Auto-learn from court patterns**: tier selection becomes smarter over time based on which tiers/tools produced the best outcomes

---

## v1.8.0 — 2026-06-22 (Phase 6-8: Monitoring & Reliability + Production Readiness + Performance)

### 📊 Phase 6 — Monitoring & Reliability

- **Delivery confirmation log** (`core/delivery_log.py`): tracks every message BAW sends — message ID, timestamp, platform, content hash, delivery status. Provides audit trail for "did BAW actually send that?"
- **execute_tool pool shutdown cleanup**: proper `finally` block ensures thread pool is always cleaned up, preventing zombie threads on error paths
- **Health endpoint improvement**: added uptime, active task count, and delivery stats to the health check response — richer diagnostics for `/doctor` and monitoring tools

### 🚀 Phase 7 — Production Readiness

- **Graceful restart with drain**: SIGTERM/SIGINT/SIGHUP handling — BAW finishes in-flight tasks before shutting down. No more mid-conversation kills.
- **Webhook deployment guide** (`DEPLOYMENT.md`): step-by-step guide for deploying BAW behind a reverse proxy with webhook endpoints
- **Docker healthcheck improvement**: health endpoint now checks real application state (not just "process is running") — orchestrator can detect stuck/infinite-loop states

### ⚡ Phase 8 — Performance Tuning

- **Session compression**: keep count increased 4→8 messages per compression cycle — fewer compression events, smoother conversation flow
- **Per-mode max_tokens tuning**: quick=4K, hybrid=8K, tight=16K, focus=16K — mode-appropriate output budgets prevent both truncation and waste
- **Lightweight performance profiler**: `perf_start()`/`perf_end()` markers — BAW can self-measure how long individual steps take without external tooling

### 🔧 Notable Fixes Leading to v1.8.0

- **Thread safety**: `_session_lock` prevents dict corruption on concurrent access (v1.7.2)
- **Telegram poll loop crash-proof**: httpx thread safety, offset persistence, auto-restart on crash (v1.7.3)
- **Vision session injection**: inject analysis result before `_run_baw`, strip old photo entries so BAW sees current photo (v1.7.4)
- **Photo+caption handling**: Telegram caption text no longer silently dropped (v1.7.5)
- **ThreadPoolExecutor shutdown fix**: `wait=False` prevents stuck tasks from blocking process exit (v1.7.6)
- **Output format enforcement**: `_strip_narration` output filter removes AI narrative framing from user-facing output
- **Synthesis guard**: empty output now triggers text-only retry instead of silent fallback
- **Tool turn caps**: dynamic tool cap based on complexity — simple=50, moderate=75, complex=100
- **Token optimization**: CostTracker reset bug fixed, context compaction improvements, model list summary trimming
- **Config drift auto-fix**: system-defined overrides always win, user config changes blocked without permission
- **Cantonese routing**: router scoring recognizes 第一/第二/第三 multi-step patterns and deep keywords (睇/config/system)

### 🧪 Test Coverage

- 157 tests (grown from 15 at v0.22.0)
- Memory curator: classification accuracy, conflict detection, noise gate
- Context compaction: threshold trigger, summary quality, no-false-trigger
- Memory search by ID: full ID, partial suffix, content backwards compatibility
- 15/15 verification tests passing

---

## Earlier versions

See git history for v0.17.x and below.
