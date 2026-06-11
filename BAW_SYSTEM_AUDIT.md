# BAW 系統審計報告

> 審計日期: 2025-06-11  
> 審計範圍: 邏輯 + 速度 + 文件 + 矛盾 + 功能缺口

---

## 1. 邏輯問題 (Logic Issues)

### 1.1 [CRITICAL] Silent fail in search provider initialization
- **File**: `core/loop.py:349-350`
- **Current behavior**: `except Exception: pass` silently swallows all errors
- **Suggested fix**: Log the error or raise a proper exception

### 1.2 [HIGH] Silent fail in memory save
- **File**: `core/loop.py:569-571`
- **Current behavior**: `except Exception: pass` silently fails
- **Suggested fix**: Log to diagnostic channel

### 1.3 [HIGH] Silent fail in adversarial court model loading
- **File**: `core/loop.py:596-597`
- **Current behavior**: Models fail to load but continue silently
- **Suggested fix**: Log which model failed and why

### 1.4 [MEDIUM] Inline code execution security
- **File**: `core/loop.py:1188-1195`
- **Current behavior**: `exec()` with `__builtins__` can execute arbitrary code
- **Suggested fix**: Use sandboxed execution or restrict imports

### 1.5 [MEDIUM] Race condition in media group buffering
- **File**: `core/messaging/telegram.py:502-518`
- **Current behavior**: threading.Timer with mutable dict could race
- **Suggested fix**: Use thread-safe lock for buffer access

### 1.6 [LOW] Circuit breaker stats not exported as tool
- **File**: `core/llm.py:64-67`
- **Current behavior**: `get_circuit_stats()` exists but not exposed
- **Suggested fix**: Register as diagnostic tool

### 1.7 [MEDIUM] Zero-tool-call detection too simplistic
- **File**: `core/loop.py:1289-1291`
- **Current behavior**: Only checks for "0 tool calls" string
- **Suggested fix**: Add more error patterns

---

## 2. 速度瓶頸 (Speed Bottlenecks)

### 2.1 [HIGH] Repeated file reads each turn
- **File**: `core/loop.py:175,224-227`
- **Current behavior**: SOUL.md and ORCHESTRATOR.md read from disk every turn
- **Suggested fix**: Cache system prompt with TTL

### 2.2 [MEDIUM] No LLM response caching
- **File**: `core/llm.py:200-212`
- **Current behavior**: Every call hits API, no caching
- **Suggested fix**: Add response cache for identical prompts

### 2.3 [MEDIUM] Inline gate spawns extra LLM call
- **File**: `core/loop.py:1143-1197`
- **Current behavior**: Each inline step spawns new LLM call + exec
- **Suggested fix**: Direct tool execution for simple steps

### 2.4 [HIGH] Sub-agent spawns separate process
- **File**: `tools/delegate_task.py:178-263`
- **Current behavior**: Each delegate_task is separate loop (max 12 iterations)
- **Suggested fix**: Reuse parent context for simple tasks

### 2.5 [LOW] HTTP client per-session
- **File**: `core/llm.py:21-31`
- **Current behavior**: New httpx.Client per session
- **Suggested fix**: Use singleton pattern with connection pool

### 2.6 [MEDIUM] Config reloads .env every call
- **File**: `core/llm.py:143-152`
- **Current behavior**: Reads .env file every time
- **Suggested fix**: Load once at startup

---

## 3. 文件處理 (File Handling)

### 3.1 [LOW] Good format support
- **File**: `core/messaging/telegram.py:351-467`
- **Current behavior**: Supports PDF, DOCX, PPTX, XLSX, CSV, images
- **Status**: ✅ Working

### 3.2 [MEDIUM] Image OCR depends on external deps
- **File**: `core/messaging/telegram.py:452-463`
- **Current behavior**: Requires pytesseract + tesseract-ocr installed
- **Suggested fix**: Document dependency or fallback to MiniMax vision

### 3.3 [HIGH] No file size limit check
- **File**: `core/messaging/telegram.py:310-349`
- **Current behavior**: Downloads without size validation
- **Suggested fix**: Add max file size (e.g., 20MB) check

### 3.4 [MEDIUM] No retake mechanism
- **File**: `core/messaging/telegram.py:310-349`
- **Current behavior**: User cannot re-download same file
- **Suggested fix**: Store file path in session for re-access

### 3.5 [LOW] Image processing runs in thread
- **File**: `core/messaging/telegram.py:479-483`
- **Current behavior**: Uses daemon thread, could be lost
- **Suggested fix**: Use proper job queue

---

## 4. 矛盾 (Contradictions)

### 4.1 [HIGH] config.yaml model.default vs executor.model
- **File**: `config.yaml:6,25`
- **Current behavior**: Two different default model settings
- **Suggested fix**: Unify to single default, use task_rules for routing

### 4.2 [HIGH] SOUL.md "精簡回覆" vs long outputs
- **File**: `SOUL.default.md:47-58` vs actual output
- **Current behavior**: Rules say 1-3 paragraphs, actual often longer
- **Suggested fix**: Enforce max tokens or add length check

### 4.3 [MEDIUM] Quick mode hardcoded Cantonese
- **File**: `core/loop.py:186-196`
- **Current behavior**: Hardcoded in prompt, ignores tone config
- **Suggested fix**: Use tone config instead

### 4.4 [MEDIUM] Cost tracking mentioned but not detailed
- **File**: `core/loop.py:265` vs `core/loop.py:101-108`
- **Current behavior**: Says "per-call cost shown" but only total shown
- **Suggested fix**: Show per-call breakdown in summary

### 4.5 [LOW] Context window tracker exists but not shown
- **File**: `core/loop.py:55-80`
- **Current behavior**: `_CONTEXT_TRACKER` exists, shown only in HTML output
- **Suggested fix**: Show in text output too

---

## 5. 功能缺口 (Feature Gaps)

### 5.1 [HIGH] No voice clone support
- **File**: `tools/tts.py:206-263`
- **Current behavior**: Only preset voices, no clone capability
- **Suggested fix**: Add voice clone API (ElevenLabs, etc.)

### 5.2 [MEDIUM] Session resume partially implemented
- **File**: `core/messaging/__init__.py:516-519,709-712`
- **Current behavior**: `/resume` and `/pickup` exist but incomplete
- **Suggested fix**: Complete session restoration

### 5.3 [MEDIUM] History compression at 80% threshold
- **File**: `core/messaging/__init__.py:1294-1306`
- **Current behavior**: Compresses at 80% context, may lose info
- **Suggested fix**: Lower threshold to 60% or add warning

### 5.4 [MEDIUM] Image input doesn't auto-invoke vision
- **File**: `core/messaging/telegram.py:473-500`
- **Current behavior**: Image uploaded but vision not auto-called
- **Suggested fix**: Auto-detect and invoke vision tool

### 5.5 [LOW] No cost breakdown by model
- **File**: `core/loop.py:101-108`
- **Current behavior**: Only total cost shown
- **Suggested fix**: Add per-model breakdown

### 5.6 [LOW] Some inline keyboard callbacks incomplete
- **File**: `core/messaging/telegram.py:1548-1583`
- **Current behavior**: Some buttons may not respond
- **Suggested fix**: Add logging for callback errors

### 5.7 [MEDIUM] CLI vs Telegram output inconsistency
- **File**: `cli/` vs `core/messaging/telegram.py`
- **Current behavior**: CLI uses Rich, Telegram uses HTML
- **Suggested fix**: Unified rendering layer

### 5.8 [LOW] No Chinese/Cantonese i18n system
- **File**: Multiple files
- **Current behavior**: Hardcoded strings throughout
- **Suggested fix**: Add i18n system with locale files

---

## Prioritized Action List

### CRITICAL (Fix Immediately)

1. **1.1** - Add error logging for search provider init silent fail
2. **4.1** - Unify config model.default and executor.model
3. **3.3** - Add file size limit check

### HIGH (Fix in Next Sprint)

4. **2.1** - Cache system prompt with TTL
5. **2.4** - Optimize sub-agent spawning
6. **4.2** - Enforce output length limits
7. **5.1** - Add voice clone support
8. **5.4** - Auto-invoke vision for image uploads

### MEDIUM (Fix in Future)

9. **2.2** - Add LLM response caching
10. **3.2** - Document or handle OCR dependencies
11. **5.3** - Lower history compression threshold
12. **5.7** - Unified rendering layer

### LOW (Nice to Have)

13. **1.6** - Export circuit breaker as tool
14. **4.5** - Show per-model cost breakdown
15. **5.8** - Add i18n system

---

## Summary

| Category | Critical | High | Medium | Low | Total |
|----------|----------|------|--------|-----|-------|
| 邏輯問題 | 1 | 2 | 3 | 0 | 6 |
| 速度瓶頸 | 0 | 2 | 4 | 0 | 6 |
| 文件處理 | 0 | 1 | 2 | 2 | 5 |
| 矛盾 | 0 | 3 | 2 | 0 | 5 |
| 功能缺口 | 0 | 1 | 4 | 4 | 9 |
| **Total** | **1** | **9** | **15** | **6** | **31** |

---

> Generated by BAW System Audit (2025-06-11)