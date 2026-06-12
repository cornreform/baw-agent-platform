# BAW Code-Level Structural Audit — NEW Issues

> 審計日期: 2026-06-12  
> 審計範圍: Code-level issues NOT covered in prior audits (BAW_SYSTEM_AUDIT.md, _P2.md, _P3.md)

---

## Prior Audits Covered (DO NOT REPEAT)

| Audit | Focus Areas |
|-------|-----------|
| BAW_SYSTEM_AUDIT.md | Logic issues, speed bottlenecks, file handling, contradictions, feature gaps |
| BAW_SYSTEM_AUDIT_P2.md | Security, permissions, cost tracking, observability, test coverage, deployment, UX |
| BAW_SYSTEM_AUDIT_P3.md | Memory, context, persona, tools registration (CRITICAL: only 6/16 tools registered), i18n, sub-agent |

> ⚠️ **NOTE**: Issue #1 below was ALREADY covered in P3 (tools/__init__.py:3-13) as CRITICAL. This audit confirms it's STILL UNFIXED and provides code-level fix.

---

## NEW Structural Issues Found

### 1. [CRITICAL — DUPLICATE FROM P3, STILL UNFIXED] Incomplete Tool Registration

**File**: `tools/__init__.py:3-13`  
**Status**: Already flagged as CRITICAL in P3, but still unfixed

**Current code**:
```python
from . import bash, read_file, write_file, web_search, image_generate, tts

def register_all():
    register(**bash.TOOL_DEF)
    register(**read_file.TOOL_DEF)
    register(**write_file.TOOL_DEF)
    register(**web_search.TOOL_DEF)
    register(**image_generate.TOOL_DEF)
    register(**tts.TOOL_DEF)
```

**Missing tools** (9 still not registered):
- `delegate_task`, `patch`, `search_files`, `todo`, `vision`, `execute_code`, `browser`, `memory`, `web_extract`

**Fix** (code-level):
```python
from . import (
    bash, read_file, write_file, web_search, image_generate, tts,
    delegate_task, patch, search_files, todo, vision, execute_code,
    browser, memory, web_extract
)

def register_all():
    register(**bash.TOOL_DEF)
    register(**read_file.TOOL_DEF)
    register(**write_file.TOOL_DEF)
    register(**web_search.TOOL_DEF)
    register(**image_generate.TOOL_DEF)
    register(**tts.TOOL_DEF)
    # Add missing 9 tools:
    register(**delegate_task.TOOL_DEF)
    register(**patch.TOOL_DEF)
    register(**search_files.TOOL_DEF)
    register(**todo.TOOL_DEF)
    register(**vision.TOOL_DEF)
    register(**execute_code.TOOL_DEF)
    register(**browser.TOOL_DEF)
    register(**memory.TOOL_DEF)
    register(**web_extract.TOOL_DEF)
```

---

### 2. [HIGH — NEW] No Type Guard on config dict Access

**File**: `core/llm.py:192-193`  
**Current behavior**:
```python
def get_model(config: dict, model_id: Optional[str] = None) -> ModelDef:
    cfg = config.get("model", {})  # ⚠️ No isinstance check
    model_id = model_id or cfg.get("default", "deepseek-v4-flash")
```

**Issue**: If caller passes wrong type for `config`, this fails with cryptic error. No defensive type check — violates type safety.

**Fix**:
```python
def get_model(config: dict, model_id: Optional[str] = None) -> ModelDef:
    # Type guard (NEW)
    if not isinstance(config, dict):
        raise TypeError(f"config must be dict, got {type(config).__name__}")
    
    cfg = config.get("model", {})
    if not isinstance(cfg, dict):
        cfg = {}
    
    model_id = model_id or cfg.get("default", "deepseek-v4-flash")
    # ... rest of function
```

---

### 3. [MEDIUM — NEW] Inconsistent Exception Handling in Tool Loading

**File**: `core/messaging/__init__.py:811-815`  
**Current behavior**:
```python
s = _iu.spec_from_file_location(f'_tk_{name}', p)
if s is None or s.loader is None:
    raise ImportError(f"Cannot load tool '{name}' from {p}")
m = _iu.module_from_spec(s)
s.loader.exec_module(m)
return m.TOOL_DEF  # ⚠️ No AttributeError check
```

**Issue**: If tool module missing `TOOL_DEF`, raises bare `AttributeError` — not user-friendly.

**Fix**:
```python
s = _iu.spec_from_file_location(f'_tk_{name}', p)
if s is None or s.loader is None:
    raise ImportError(f"Cannot load tool '{name}' from {p}")
m = _iu.module_from_spec(s)
s.loader.exec_module(m)

# Add guard (NEW)
if not hasattr(m, 'TOOL_DEF'):
    raise AttributeError(f"Tool '{name}' missing TOOL_DEF attribute. "
                         f"Available: {dir(m)}")

return m.TOOL_DEF
```

---

### 4. [MEDIUM — NEW] Missing Optional Type Hint

**File**: `core/llm.py:143`  
**Current behavior**:
```python
@dataclass
class ModelDef:
    # ... other fields ...
    model_kwargs: dict = None  # ⚠️ Should use Optional[]
```

**Issue**: Type hint inconsistency with other optional fields. Should use `Optional[dict]` for consistency.

**Fix**:
```python
    model_kwargs: Optional[dict] = None  # Use Optional[] wrapper
```

---

### 5. [LOW — NEW] Duplicate CostTracker Summary Method

**File**: `core/loop.py:142-143` + `CostTracker.summary()`  
**Current behavior**: Two methods with similar purpose:
```python
# In CostTracker class:
def summary(self) -> str: ...

# In loop.py:
def format_cost_summary() -> str:
    return _get_tracker().summary()
```

**Issue**: Semantic confusion — should have single source of truth.

**Fix** (consolidate):
```python
def format_cost_summary(detailed: bool = False) -> str:
    tracker = _get_tracker()
    if detailed:
        lines = [f"{c['model']}: {c['tokens_in']} in, {c['tokens_out']} out" 
                for c in tracker.calls]
        return "\n".join(lines) if lines else "No calls yet"
    
    total_in = sum(c["tokens_in"] for c in tracker.calls)
    total_out = sum(c["tokens_out"] for c in tracker.calls)
    return f"💰 Tokens: {total_in:,} in / {total_out:,} out"
```

---

## Summary Table

| # | Severity | File:Line | Issue | Fix | Covered Before? |
|---|----------|-----------|-------|-----|-----|---------------|
| 1 | CRITICAL | tools/__init__.py:3-13 | Only 6/15 tools registered | Add 9 register() calls | **YES** (P3) |
| 2 | HIGH | core/llm.py:192 | No type guard on config.get() | Add isinstance() check | **NEW** |
| 3 | MEDIUM | core/messaging/__init__.py:811 | No TOOL_DEF attribute check | Add hasattr() guard | **NEW** |
| 4 | MEDIUM | core/llm.py:143 | Missing Optional[] type hint | Use Optional[dict] | **NEW** |
| 5 | LOW | core/loop.py:142 | Duplicate summary methods | Consolidate into one | **NEW** |

---

## Recommended Priority

### Must Fix (P0)
1. **#1** — Complete tool registration (still unfixed since P3)

### Should Fix (P1)
2. **#2** — Add type guard on config dict access (type safety)
3. **#3** — Add TOOL_DEF attribute check (error handling)

### Nice to Have (P2)
4. **#4** — Add Optional[] wrapper to model_kwargs
5. **#5** — Consolidate cost summary methods

---

> Generated by BAW Code-Level Audit (2026-06-12)