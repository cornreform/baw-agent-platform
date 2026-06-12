# BAW 系統審計 Part 3 — Memory + Context + Persona + Tools + i18n

> 審計日期: 2026-06-11  
> 審計範圍: `/home/radxa/baw/core/` + `/home/radxa/baw/tools/`

---

## 1. Memory Architecture (`core/memory.py`)

### Findings (5-10 per category)

| Severity | File:Line | Current Behavior | Suggested Fix |
|----------|-----------|---------------|--------------|
| **MEDIUM** | memory.py:21-22 | JSONL append-only store 冇 cap — 会無限增長 | 加入 max_entries 或 auto-cleanup schedule |
| **MEDIUM** | memory.py:79-84 | Token count 粗略估算: `len // 4` — 中文會低估 50%+ | 用 tiktoken 或 cl100k base + CJK multiplier |
| **HIGH** | memory.py:256-287 | search() 用 substring match (`query in content`) — 唔係 semantic search | 改用 keyword Jaccard 或 embedding |
| **LOW** | memory.py:227-254 | 冇 user delete/edit API — 只有 compress_old() | 加 delete(id), update(id, new_content) |
| **MEDIUM** | memory.py:289-303 | decay() 要手動 call — 冇 auto-schedule | 加 daily cron 或 startup call |
| **MEDIUM** | memory.py:61-109 | CJK bigram extraction — 2-char only, missing Cantonese特有的 | 加 3-gram + Cantonese char filter |
| **LOW** | memory.py:341-440 | compress_old() 30日 threshold 固定 — 唔理 access frequency | 用 access_count × recency weighting |

### 📍 Cantonese / 中文 Issues

- memory.py:92-95: CJK bigram 用 `\u4e00-\u9fff` — 呢個 range 包含簡體
- memory.py:98-104: stop chars 固定 list — 冇 dynamic learning
- **呢啲係 MEDIUM issues**：memory 會隨時間變肥，但清理機制要手動 trigger

---

## 2. Context Window Management (`core/context.py`, `core/render.py`, `core/loop.py`)

### Findings

| Severity | File:Line | Current Behavior | Suggested Fix |
|----------|-----------|---------------|--------------|
| **HIGH** | context.py:79-84 | `count_tokens_approx()`: `len // 4` — 好粗略 | 用 tiktoken 或 implement proper tokenizer |
| **HIGH** | loop.py:590 | threshold 8000 定義咗但冇 enforce | 加 if count > threshold: compress |
| **MEDIUM** | guards.py:137 | truncate() 只切最後 max_length | 加 head+tail preserve |
| **MEDIUM** | loop.py:427-447 | dangling tool_calls cleanup — 事後補救 | 加 proactive truncation |
| **LOW** | context.py:26 | max_tokens 存在但冇用到 context pruning | 加 context.trim_to_fit() |

### 📍 Cantonese / 中文 Issues

- context.py:79-84: 冇考慮中文 tokenize — 會低估 50%+
- loop.py:429-447: truncate 會 cut tool response mid-sequence

---

## 3. SOUL.md / Persona (`core/adversarial.py`, `core/tone.py`, SOUL.default.md)

### Findings

| Severity | File:Line | Current Behavior | Suggested Fix |
|----------|-----------|---------------|--------------|
| **HIGH** | adversarial.py:624-638 | Court context 直接 inject 到 user message — **可被 injection** | 加 context sanitize 或 separate field |
| **MEDIUM** | adversarial.py:185-223 | merged=False sequential call — 2x cost | 用 merged=True default |
| **MEDIUM** | adversarial.py:99-103 | Devil score regex: `[Devil: X/10]` — 依賴 LLM output format | 加 fallback score 或 use JSON mode |
| **LOW** | adversarial.py:306-334 | synthesize() 淨係 display — 冇實際 decision weight | 加 score-weighted execution |
| **MEDIUM** | tone.py:42-67 | Tone detection via regex — 有限 patterns | 加 fuzzy match 或 LLM-based |
| **LOW** | tone.py:70-81 | Confirmation 用英文 markdown — 應該用中文 | 改為繁體中文 |

### 📍 Cantonese / 中文 Issues

- tone.py:73-79: 中文 tone descriptions 但 confirmation 係英文
- SOUL.md:29 話「繁體中文」，但 CLI error messages 係英文
- **呢啲係 MEDIUM issues**：System prompt 同 user-facing messages language 不一致

---

## 4. Tools System (`core/tools.py`, `tools/*.py`)

### Findings

| Severity | File:Line | Current Behavior | Suggested Fix |
|----------|-----------|---------------|--------------|
| **CRITICAL** | tools/__init__.py:3-13 | **只 register 6/16 tools** — 其餘 10 個 tool 唔可用！ | 加埋其餘 tool registration |
| **HIGH** | tools.py:52-64 | get_openai_tools() — schema generation 好 basic | 加必填欄位驗證 |
| **MEDIUM** | tools.py:67-104 | execute_tool() 用 ThreadPoolExecutor — 阻塞 | 用 async 或 queue |
| **MEDIUM** | tools.py:67 | 30s timeout hardcoded — 大 tool 可能唔夠 | 加 per-tool timeout config |
| **LOW** | tools.py:22 | _tools 係 global dict — 冇 namespace isolation | 加 tool registry per-session |
| **LOW** | tools.py:22 | 冇 tool conflict detection | 加 name collision check |

### Tool Inventory

```
Registered in tools/__init__.py (6):
✅ bash
✅ read_file
✅ write_file
✅ web_search
✅ image_generate
✅ tts

Not registered (10):
❌ delegate_task
❌ patch
❌ search_files
❌ todo
❌ vision
❌ execute_code
❌ browser
❌ memory
❌ web_extract
❌ todo (duplicate?)
```

### 📍 Cantonese / 中文 Issues

- 冇 i18n tool descriptions — 所有 tool descriptions 係英文
- **呢個係 CRITICAL**：delegate_task 冇注册，但係核心 sub-agent tool！

---

## 5. i18n / 粵語化

### Findings

| Severity | File:Line | Current Behavior | Suggested Fix |
|----------|-----------|---------------|--------------|
| **HIGH** | memory.py:92-95 | CJK bigram: `\u4e00-\u9fff` — 呢個 range 包含簡體 + 繁體 | 用 `\u4e00-\u9fff` + `\u3400-\u4dbf` (CJK-A) + Cantonese extensions |
| **MEDIUM** | tone.py:73-79 | Tone descriptions 混雜中英 | 統一用繁體中文 |
| **MEDIUM** | loop.py:190,203 | System prompt 话「Cantonese」但冇定義乜嘢 language code | 加 `yue-Hant-HK` |
| **LOW** | 冇呢個 file | 冇全形/半形 normalization | 加 unicodedata normalize |
| **LOW** | 冇呢個 file | 冇 explicit Cantonese tokenizer | 用 jieba-cantonese 或 similar |

### 📍 Cantonese / 中文 Issues (特別標記)

- **HIGH**: Cantonese 同 Simplified Chinese 用同一個 CJK range — keyword extraction 會混雜
- **MEDIUM**: CLI error messages 係英文，但 SOUL.md rules 話要用繁體中文
- **MEDIUM**: TTS voice list 有 Cantonese voices，但 system prompt 冇指定 language preference

---

## 6. Multi-agent / Sub-agent (`tools/delegate_task.py`)

### Findings

| Severity | File:Line | Current Behavior | Suggested Fix |
|----------|-----------|---------------|--------------|
| **HIGH** | delegate_task.py:173 | **冇 recursion limit** — sub-agent 可以 spawn sub-agent | 加 max_depth=1 |
| **MEDIUM** | delegate_task.py:116-233 | sub-agent failure 只 raise RuntimeError — 冇 retry | 加 2-3 retry with backoff |
| **MEDIUM** | delegate_task.py:210-228 | Failure detection 用 keyword — 有 false positive 風險 | 用 tool_call_count 做 reliable indicator |
| **LOW** | delegate_task.py:178-193 | max_iterations=12 固定 — 複雜 task 可能唔夠 | 加 config override |
| **LOW** | delegate_task.py:142-144 | toolsets restriction 用 string split — fragile | 用 list 或 set |

### 📍 Cantonese / 中文 Issues

- delegate_task.py:211-216: Failure keywords 有中文 (`無法`, `失敗`, `冇實際執行`) — good!
- 但 system prompt 冇指定 Cantonese output
- **呢啲係 LOW issues**：基本 OK，但可以改進

---

## Prioritized Action List

### P0 (Must Fix)

1. **CRITICAL**: tools/__init__.py 只 register 6/16 tools — 補齊 其餘 10 個
2. **CRITICAL**: delegate_task.py 冇注册 — 但係核心 sub-agent tool
3. **HIGH**: Court context injection 可被 injection — sanitize 或 separate field

### P1 (Should Fix)

4. **HIGH**: Context token estimation 唔準 — 用 proper tokenizer
5. **HIGH**: Context threshold 8000 定義咗但冇 enforce — 加自動 compression
6. **HIGH**: Memory 冇 cap — JSONL 會無限增長
7. **HIGH**: Cantonese/Simplified 同 range — 要 distinguish

### P2 (Nice to Have)

8. **MEDIUM**: Memory search 用 substring — 改為 semantic/keyword
9. **MEDIUM**: sub-agent 冇 retry — 加 2-3 retry
10. **MEDIUM**: Tone confirmation 混雜中英 — 統一
11. **LOW**: decay() 要手動 call — 加 auto-schedule
12. **LOW**: CLI error messages 用英文 — 應該用中文

---

## Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Memory | 0 | 0 | 4 | 3 |
| Context | 0 | 2 | 2 | 1 |
| Persona | 0 | 1 | 3 | 2 |
| Tools | **1** | 1 | 2 | 2 |
| i18n | 0 | 1 | 2 | 2 |
| Sub-agent | 0 | 1 | 2 | 2 |
| **Total** | **1** | **6** | **15** | **12** |

### Cantonese / 中文 Related Issues

- Cantonese/Simplified 同 CJK range: **HIGH**
- CLI error messages vs SOUL.md language: **MEDIUM**
- Tone descriptions 混雜: **MEDIUM**
- TTS voice specification: **LOW**

### Recommended Priority

1. Fix tools/__init__.py (CRITICAL)
2. Fix delegate_task registration (CRITICAL)  
3. Fix context threshold enforcement (HIGH)
4. Fix Cantonese CJK range (HIGH)
5. Fix court injection (HIGH)