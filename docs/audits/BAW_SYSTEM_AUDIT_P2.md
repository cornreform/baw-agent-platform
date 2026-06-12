# BAW 系統審計報告 (Part 2)

> 審計日期: 2025-06-11  
> 審計範圍: Security + Permissions + Cost + Observability + Test Coverage + Deployment + UX

---

## 1. Security (安全)

### 1.1 [CRITICAL] API Key Handling — Logged in HTTP Headers
- **File**: `core/llm.py:224`
- **Current behavior**: `Authorization: Bearer {model.api_key}` is sent in plain HTTP headers
- **Issue**: If logging is set to DEBUG, API keys could be logged. Need to verify logging config
- **Suggested fix**: Ensure no logging of Authorization header, or use redacted format

### 1.2 [CRITICAL] Path Traversal in Permission Engine
- **File**: `core/permission.py:40-43`
- **Current behavior**: Uses `fnmatch` for path matching, could be bypassed with `../etc/passwd`
- **Suggested fix**: Use `pathlib.Path.resolve()` to resolve to absolute path before checking

### 1.3 [HIGH] Dockerfile Runs as Non-root User
- **File**: `Dockerfile:10,21`
- **Current behavior**: `USER baw` after `useradd -m baw`
- **Status**: ✅ Good — container runs as non-root

### 1.4 [HIGH] No Prompt Injection Protection
- **File**: Multiple files in `core/`
- **Current behavior**: No visible prompt injection sanitization
- **Issue**: User input goes directly to LLM without sanitization
- **Suggested fix**: Add input sanitization layer or system prompt instruction

### 1.5 [MEDIUM] Auto-approve Paths Too Broad
- **File**: `core/permission.py:25-31`
- **Current behavior**: `~/.baw/*` auto-approved for read_file/write_file
- **Issue**: Could allow access to sensitive files in .baw directory
- **Suggested fix**: Narrow to specific safe paths (config.yaml, .env, etc.)

### 1.6 [MEDIUM] High-risk Rules Are Deny-only
- **File**: `core/permission.py:75-78`
- **Current behavior**: High-risk rules return deny, no prompt
- **Suggested fix**: Consider adding session-level override option

### 1.7 [LOW] Session Override Not Persistent
- **File**: `core/permission.py:91-97`
- **Current behavior**: `_session_allows` and `_session_denies` are in-memory only
- **Suggested fix**: Consider persisting to disk for session recovery

### 1.8 [LOW] No Audit Trail for Permission Decisions
- **File**: `core/permission.py`
- **Current behavior**: No logging of allow/deny/prompt decisions
- **Suggested fix**: Add audit logging for security review

---

## 2. Cost Tracking (成本追蹤)

### 2.1 [HIGH] calculate_cost Called in All LLM Paths
- **File**: `core/loop.py:485,526,656,701,794,1036,1357,1428,1514`
- **Current behavior**: Cost is calculated after every LLM call
- **Status**: ✅ Good — comprehensive tracking

### 2.2 [HIGH] Cost Displayed After Each Response
- **File**: `core/loop.py:567,712`
- **Current behavior**: `format_cost_summary()` appended to output
- **Status**: ✅ Good — user sees cost per call

### 2.3 [HIGH] No Budget Limit Enforcement
- **File**: `core/llm.py` + `core/loop.py`
- **Current behavior**: No budget limit check before calling LLM
- **Issue**: User could exceed quota without warning
- **Suggested fix**: Add budget check in `call_llm_with_fallback`

### 2.4 [MEDIUM] No Quota Exceeded Handling
- **File**: `core/loop.py:1237` (error keywords only)
- **Current behavior**: Detects "quota exceeded" in errors but no automatic handling
- **Issue**: No auto-switch on quota exceeded
- **Suggested fix**: Add automatic fallback trigger on quota errors

### 2.5 [MEDIUM] Per-Model Cost Breakdown Exists But Not Shown
- **File**: `core/loop.py:89-108` (CostTracker class)
- **Current behavior**: `CostTracker` tracks per-model costs
- **Issue**: `format_cost_summary()` only shows totals
- **Suggested fix**: Add per-model breakdown to summary

### 2.6 [LOW] Cost Not Persistent Across Sessions
- **File**: `core/loop.py:81-108`
- **Current behavior**: In-memory cost tracking
- **Suggested fix**: Consider saving to session for historical tracking

---

## 3. Observability (可觀察性)

### 3.1 [HIGH] Logging Configured in baw-bot
- **File**: `baw-bot:29-34`
- **Current behavior**: `logging.basicConfig(level=logging.INFO)` with `--debug` flag
- **Status**: ✅ Good — configurable log level

### 3.2 [HIGH] No Structured Log File Output
- **File**: `baw-bot:29-34`
- **Current behavior**: Logs to stdout only
- **Issue**: No persistent log file for debugging
- **Suggested fix**: Add file handler with rotation

### 3.3 [MEDIUM] Circuit Breaker Stats Exist but Not Exposed
- **File**: `core/llm.py:64-67`
- **Current behavior**: `get_circuit_stats()` exists
- **Issue**: Not exposed as tool or command
- **Suggested fix**: Register as diagnostic tool

### 3.4 [MEDIUM] No Per-Step Timing Metrics
- **File**: `core/loop.py`
- **Current behavior**: No per-step duration tracking
- **Issue**: Cannot identify slow steps
- **Suggested fix**: Add timing to step execution

### 3.5 [MEDIUM] No Error Rate Counter
- **File**: `core/loop.py` + `core/llm.py`
- **Current behavior**: Circuit breaker tracks failures but no aggregate error rate
- **Suggested fix**: Add error rate metrics

### 3.6 [LOW] Context Window Tracker Exists
- **File**: `core/loop.py:55-80`
- **Current behavior**: `_CONTEXT_TRACKER` tracks model + context usage
- **Status**: ✅ Exists, shown in HTML output

### 3.7 [LOW] No Session History Audit Trail
- **File**: `core/messaging/telegram.py`
- **Current behavior**: Messages logged but not user-level audit
- **Suggested fix**: Add user/action audit log

---

## 4. Test Coverage (測試覆蓋)

### 4.1 [HIGH] No Tests for call_llm_with_fallback
- **File**: `tests/`
- **Current behavior**: No test for fallback logic
- **Issue**: Critical path has no test coverage
- **Suggested fix**: Add unit test for fallback

### 4.2 [HIGH] No Tests for TTS
- **File**: `tests/`
- **Current behavior**: No TTS test
- **Issue**: TTS is a critical feature
- **Suggested fix**: Add TTS integration test

### 4.3 [HIGH] No Tests for Media Send
- **File**: `tests/`
- **Current behavior**: No media send test
- **Issue**: Critical for Telegram bot
- **Suggested fix**: Add media send test

### 4.4 [MEDIUM] Basic Memory Graph Test Exists
- **File**: `tests/test-memory-graph.py`
- **Current behavior**: Tests memory storage, edges, scoring
- **Status**: ✅ Good — comprehensive memory tests

### 4.5 [MEDIUM] No E2E Tests
- **File**: `tests/`
- **Current behavior**: No end-to-end test
- **Issue**: Cannot test full flow
- **Suggested fix**: Add E2E test with mock messaging

### 4.6 [LOW] No pytest Fixtures
- **File**: `tests/`
- **Current behavior**: No pytest fixtures for mocking LLM
- **Suggested fix**: Add fixtures for config, mock LLM

---

## 5. Deployment / Operations (部署 / 運維)

### 5.1 [HIGH] Docker Healthcheck is Placebo
- **File**: `docker-compose.yml:28-33`
- **Current behavior**: `python3 -c 'import sys; sys.exit(0)'` always succeeds
- **Issue**: Healthcheck doesn't verify actual service health
- **Suggested fix**: Use actual health check endpoint or status command

### 5.2 [HIGH] Auto-restart on Failure
- **File**: `docker-compose.yml:8`
- **Current behavior**: `restart: unless-stopped`
- **Status**: ✅ Good — auto-restart enabled

### 5.3 [HIGH] Backup Script Exists
- **File**: `core/backup.py`
- **Current behavior**: `cmd_backup()` creates tar.gz
- **Status**: ✅ Good — backups config, .env, sessions, memory

### 5.4 [MEDIUM] No Migration Script
- **File**: `core/setup.py`
- **Current behavior**: No config version migration
- **Issue**: Config format changes require manual update
- **Suggested fix**: Add version check + migration

### 5.5 [MEDIUM] No Health Check Endpoint
- **File**: `core/commands.py`
- **Current behavior**: `/status` command exists
- **Issue**: No HTTP health check for Docker
- **Suggested fix**: Add HTTP health endpoint

### 5.6 [LOW] Deploy Script Exists
- **File**: `deploy/deploy-bot.sh`
- **Current behavior**: Builds Docker, installs systemd service
- **Status**: ✅ Good — comprehensive deploy

---

## 6. User Experience (用戶體驗)

### 6.1 [HIGH] Error Messages Are Friendly
- **File**: `core/guards.py:23-37`
- **Current behavior**: `bail()` function with templates
- **Status**: ✅ Good — consistent error format

### 6.2 [HIGH] /help Command Complete
- **File**: `core/commands.py:129-165`
- **Current behavior**: Auto-generated from command registry
- **Status**: ✅ Good — comprehensive help

### 6.3 [HIGH] Typing Indicator
- **File**: `core/messaging/telegram.py:235-244,1259-1273`
- **Current behavior**: Heartbeat typing indicator while processing
- **Status**: ✅ Good — shows user is waiting

### 6.4 [MEDIUM] No Progress Bar
- **File**: `core/messaging/telegram.py`
- **Current behavior**: No progress bar for multi-step tasks
- **Issue**: User doesn't see progress
- **Suggested fix**: Add step counter in output

### 6.5 [LOW] Markdown Rendering Issues
- **File**: `core/messaging/telegram.py`
- **Current behavior**: HTML conversion for Telegram
- **Issue**: Some markdown may not render correctly
- **Status**: ⚠️ Partial — needs testing

---

## Prioritized Action List

### CRITICAL (Fix Immediately)

1. **1.2** - Fix path traversal vulnerability in permission engine
2. **1.4** - Add prompt injection protection
3. **2.3** - Add budget limit enforcement before LLM calls
4. **5.1** - Fix Docker healthcheck to verify actual health

### HIGH (Fix in Next Sprint)

5. **1.5** - Narrow auto-approve paths in permission engine
6. **2.5** - Show per-model cost breakdown
7. **3.2** - Add persistent log file with rotation
8. **3.4** - Add per-step timing metrics
9. **4.1** - Add test for call_llm_with_fallback
10. **4.2** - Add TTS test
11. **4.3** - Add media send test

### MEDIUM (Fix in Future)

12. **1.8** - Add permission audit trail
13. **2.4** - Auto-switch on quota exceeded
14. **3.3** - Expose circuit breaker as tool
15. **3.5** - Add error rate counter
16. **4.5** - Add E2E tests
17. **5.4** - Add config migration script
18. **5.5** - Add HTTP health endpoint

### LOW (Nice to Have)

19. **1.7** - Persist session overrides
20. **2.6** - Save cost history across sessions
21. **3.7** - Add user action audit trail
22. **4.6** - Add pytest fixtures
23. **6.4** - Add progress bar for multi-step tasks

---

## Summary

| Category | Critical | High | Medium | Low | Total |
|----------|----------|------|--------|-----|-------|
| Security | 2 | 3 | 2 | 1 | 8 |
| Cost Tracking | 0 | 3 | 2 | 1 | 6 |
| Observability | 0 | 2 | 3 | 2 | 7 |
| Test Coverage | 0 | 3 | 2 | 1 | 8 |
| Deployment | 0 | 3 | 2 | 1 | 6 |
| User Experience | 0 | 3 | 1 | 1 | 5 |
| **Total** | **2** | **17** | **12** | **7** | **40** |

---

> Generated by BAW System Audit Part 2 (2025-06-11)