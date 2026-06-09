# BAW v0.13 — Route Resilience + Anti-Stuck Architecture

**Release Date:** 2026-06-09

## Core: Route Plan 唔會再 Silent Stop

v0.13 全面重寫 route plan 執行層嘅失敗處理。之前遇到死 step（e.g., bash hang / config reload loop）會 silent stop 喺中間，而家：

| 機制 | Before | After |
|------|--------|-------|
| Step timeout | 冇 | **60s** per step |
| Recalculation cap | 5 retries | **3** (fast fail) |
| Same-step skip | 冇 | **2 fails → skip** |

Flow：死 step → timeout 60s → recalc 新 path → 再 timeout 60s → detect same-step → ⏭️ skip → 行下一步。保證 final result 永遠出到。

## New Features (since v0.12)

### 🗺️ Route Plan Execution
- **Inline progress editing** — 同一條 Telegram message 實時 update step progress
- **Typing indicator heartbeat** — 每 3 秒 refresh，唔會消失
- **Auto-continue loop** — goal not achieved → auto-feed result back (3 rounds max)

### 🔧 Tool Self-Configuration
- BAW 自己 **discover + register** CLI tools (`which`, `find /`, `ls /usr/bin`)
- 唔需要 user 預先 configure — 自己搵到 `mmx`、自己寫 wrapper、自己註冊
- Template: `~/baw/tools/vision.py` → 任何新 tool 照住寫就自動註冊

### 👁️ MiniMax Vision
- Photo handling 用 **MiniMax M3 vision**（`mmx vision describe`）
- OCR 只做 fallback（mmx 唔裝先降級）
- Sub-agent 獨立用 vision tool 分析圖片

### 🔄 Multi-Round Auto-Continuation
- Tight/hybrid mode: 如果 self-review score < 7
- Auto-feed 上一 round output 做新 prompt（最多 3 rounds）
- 唔會再出 "Let me go do X" 空頭支票

### 🛡️ Fallback Chain Fix
- Sub-agent 開波前 **驗證 executor model 存在**（ValueError catch）
- Model name 唔再 hallucinate — system prompt 列晒 available models
- Fallback: executor → default → hard fail with clear error

## Configuration

```yaml
# ~/.baw/config.yaml — 新增 executor model 設定
executor:
  model: MiniMax-M2.5    # sub-agent 用嘅 model
```

## Commits

```
74cfead fix: step timeout 60s, same-step skip after 2 fails
70aef97 fix: step timeout 120s + sub-agent iterations 10→5
413f20d fix: inline progress shorter lines, recalc retries 3→5
7ae02be fix: typing indicator + auto-continue detection
aa0b1c1 fix: tool config — BAW owns its toolchain
bd34aca feat: inline progress editing (editMessageText)
e8585b7 fix: photo handler uses MiniMax vision (mmx)
ad7916a fix: fallback chain ValueError catch
```

---

**Upgrade:** `git pull && baw --reload`
