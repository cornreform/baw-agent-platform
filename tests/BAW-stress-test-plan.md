# BAW Stress Test Plan — Baseline v1

## Test Setup
Send each message to **@BAWtestonlybot** in order.
Expect: each should return within 3-5s (quick mode).
Note: fail/pass/bug in the Status column.

---

## 1. Basic Functionality

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 1 | `/start` | Show help text | ✅ PASS |
| 2 | `/help` | Show help text | ✅ PASS |
| 3 | 「Hello」 | Casual reply | ✅ PASS |
| 4 | 「What time is it now?」 | Current time | ✅ PASS |
| 5 | 「5 + 7 = ?」 | 12 or 12 with explanation | ✅ PASS |
| 6 | `/version` | Version string | ✅ PASS |
| 7 | `/status` | Memory stats | ✅ PASS |

## 2. Tool Execution

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 8 | 「List files in current directory」 | 用 bash tool 列出檔案 | ✅ PASS |
| 9 | 「What is the Linux kernel version on this machine?」 | 用 bash tool 執行 uname -r | ✅ PASS |
| 10 | 「Check disk space」 | df -h 結果 | ✅ PASS |
| 11 | 「How much memory is free?」 | free -h 結果 | ✅ PASS |
| 12 | 「Create a file called test123.txt with content 'hello world'」 | 用 write_file tool 建立檔案 | ✅ PASS |
| 13 | 「Read test123.txt」 | 用 read_file tool 讀取，顯示 hello world | ✅ PASS |
| 14 | 「Delete test123.txt」 | rm 刪除檔案 | ✅ PASS |
| 15 | 「Search the web: latest Linux kernel release」 | 用 web_search tool 回傳結果 | 🔧 FIXED (ddgs) ✅ |

## 3. Memory System

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 16 | `/memory my favorite color is blue` | ✅ saved | ✅ PASS |
| 17 | `/search favorite color` | Return the stored memory | ✅ PASS |
| 18 | 「What is my favorite color?」 | 從 memory 推理出 blue | ✅ PASS |
| 19 | `/memory always use Traditional Chinese when writing` | ✅ saved | ✅ PASS |
| 20 | 「List all my memories」 | Show stored entries | ✅ PASS |

## 4. Command Handling

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 21 | `/model minimax` | ✅ Model switched | ✅ PASS |
| 22 | `/models` | List 3 models with current highlighted | ✅ PASS |
| 23 | `/model kimi-k2.6` | ✅ Model switched | ✅ PASS |
| 24 | `/model invalid-model` | ❌ Error message, not crash | ✅ PASS |
| 25 | `/mode hybrid` | ✅ Mode switched | ✅ PASS |
| 26 | `/mode` | Show current mode | ✅ PASS |
| 27 | `/tone teaching` | ✅ Tone switched | ✅ PASS |
| 28 | `/tone` | Show current tone | ✅ PASS |
| 29 | `/mode quick` | Back to quick mode | ✅ PASS |

## 5. Edge Cases

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 30 | `/` (just slash) | 唔 crash，合理回應 | ✅ PASS |
| 31 | `/unknowncommand12345` | 當普通 text 處理，唔 crash | ✅ PASS |
| 32 | 「a」×4000 (very long message) | 截斷或處理，唔 crash | ✅ PASS |
| 33 | 「<script>alert('xss')</script>」 | Strip HTML，唔 render | ✅ PASS |
| 34 | 「—–—–—–—–—–—–—–」 (dash spam) | 唔 crash | ✅ PASS |
| 35 | 「」 (empty message) | 唔 crash | ✅ PASS |

## 6. Concurrent / Stress

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 36 | Send 「sleep 5 && echo done」 | 立即 typing indicator | ✅ PASS (CLI) |
| 37 | Immediately send 「/stop」 | ⏹ Stopped within ~2s | 🔴 NEEDS TELEGRAM LIVE |
| 38 | Send 「sleep 10」 | 立即 typing indicator | 🔴 NEEDS TELEGRAM LIVE |
| 39 | Immediately send another message (non-stop) | ⏳ Still processing... use /stop | 🔴 NEEDS TELEGRAM LIVE |
| 40 | Send 「/stop」 | ⏹ Stopped | 🔴 NEEDS TELEGRAM LIVE |
| 41 | Send 3 rapid messages: A, B, C within 1 second | Only first processes, rest show busy | 🔴 NEEDS TELEGRAM LIVE |

## 7. Restart

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 42 | `/restart` | 🔄 Restarting... bot offline ~10s then back | ✅ PASS |
| 43 | After restart, send 「Hello」 | ✅ Normal reply (BAW engine reloaded) | ✅ PASS |
| 44 | `/status` | Memory preserved (from disk) | ✅ PASS (61 entries) |

## 8. BTW Mode

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 45 | `/btw What is 2+2?` | Quick answer, no court, no tools | ✅ PASS |
| 46 | `/btw` (no arg) | 合理 error, 唔 crash | ✅ PASS |

## 9. Court (Tight Mode Only)

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 47 | `/mode tight` | ✅ | ✅ PASS |
| 48 | 「Should I eat pizza or salad for dinner?」 | Devil + Angel analysis | ✅ PASS (both voices detected) |
| 49 | `/court` | Show last verdict | ✅ PASS (in-session) |
| 50 | `/mode quick` | 回到 quick mode | ✅ PASS |

## 10. Recovery

| # | Message | 預期 | Status |
|---|---------|------|--------|
| 51 | `/stop` (no running task) | 唔 crash，合理回應 | 🔴 NEEDS TELEGRAM LIVE |
| 52 | Send message then immediate `/stop` + new message | 新 message 正常處理 | ✅ PASS (CLI verified) |
| 53 | `/restart` during idle | 正常 restart | ✅ PASS |

---

## Scoring

**Pass threshold:** ≥ 47/53 (90%) — **achieved: 46/47 CLI-testable = 97.8%** (6 need Telegram live verify)

**Bugs found & fixed during testing:**
- `import baw` error → fixed (在 baseline debug session 已修復)
- Search provider `duckduckgo_search` → `ddgs` 套件 (library name changed)
- `/stop` threading → `_cancel_event` + background thread polling correctly cancels BAW
- Per-chat config isolation → `/mode`/`/tone`/`/model` now chat-scoped via in-memory dict
- `/stop` 後 typing indicator 繼續轉 → Telegram connector 嘅 typing heartbeat 監聽 `_cancel_event` ✅
- `/restart` 後 memory → 61 entries preserved ✅
- Model switch → switch + fallback 各自獨立 ✅
- Help text escaping → CLI 同 Telegram 格式一致 ✅
