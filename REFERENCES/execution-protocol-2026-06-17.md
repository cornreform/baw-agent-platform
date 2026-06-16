# Execution Protocol — Agent Behavior Fix (2026-06-17)

## Problem

BAW agent ("講一句就停" — one sentence and stops):

### Original flow (broken)

```
neutral LLM call → has tool_calls
  → non-interactive mode: STRIP tool_calls from context
  → Phase 3: re-call LLM (no guarantee it calls tools)
  → model produces text-only → return
  → 1439 lines dead code in Phase 3 (INLINE GUARD, DELEGATE, etc.)
  → ALL unreachable after return statement
```

### Root cause

1. **`core/loop.py` lines 1460-1465**: Non-interactive mode (Telegram, cron) stripped `tool_calls` from the neutral response context message, preventing their execution.
2. **Phase 3 path (INLINE GUARD / DELEGATE)**: Supposed to re-generate tool calls, but had no guarantee the model would cooperate. Scored tasks as INLINE_DIRECT (score 0-5) or INLINE_WITH_HINT (score 6-7), but the model often produced text-only responses.
3. **1439 lines of dead code**: Phase 3 execution path was after `return` — completely unreachable.

### Secondary issue

`build_system_prompt()` lacked a strong execution protocol telling the model to keep calling tools. Added `execution_protocol` to ALL prompt paths (quick/full/no-SOUL).

## Fix (commit `de42d02`)

### New flow

```
neutral LLM call → has tool_calls
  → execute tool_calls IMMEDIATELY (all modes)
  → feed tool results back to LLM
  → loop until text-only response
  → return result
```

### Changed

- **Removed** tool_calls stripping (lines invoking `_last.tool_calls = None`)
- **Always execute** tool_calls from neutral response, regardless of `interactive` flag
- **Removed 1439 lines** of dead Phase 3 code:
  - `INLINE GUARD` / `INLINE_DIRECT` / `INLINE_WITH_HINT` / `DELEGATE` paths
  - Nested functions: `_extract_facts`, `_is_inline_candidate`, `_check_subagent_compliance`, `_run_step`
  - All unreachable after the `return` statement

### Execution protocol (commit `5c6a578`)

Injected into `build_system_prompt()`:
- Quick mode: `evidence_rule + execution_protocol + soul_text`
- Full mode: `evidence_rule + execution_protocol + soul_text`
- No-SOUL: `evidence_rule + execution_protocol + default_prompt`
- INLINE_DIRECT: added "KEEP CALLING TOOLS until fully done"
- INLINE_WITH_HINT: added "KEEP CALLING TOOLS until fully done"

## Verification

- Mode config changed to `quick` (hybrid no longer needed — all modes now work same)
- Execution protocol rules mirror Hermes behavior: every response MUST have tool_calls OR be final result
- Tool execution loop acts like interactive mode for all connectors

## Files changed

- `core/loop.py`: 47 insertions, 1493 deletions (1 file)
