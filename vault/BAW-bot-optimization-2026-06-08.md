# BAW Telegram Bot — 優化記錄 (2026-06-08)

## 問題
Bot 回覆慢 (~1.7–3.0s)，有時 timeout (>2min)

## Root Cause
1. 每句 message spawn 新 subprocess → 每次重啟 Python、重 import、重 establish httpx connection pool
2. Quick mode 照 load 成個 6KB SOUL.md（包含 court rules / tool descriptions / permission rules）
3. LLM API timeout (120s) + subprocess timeout (120s) 疊加 → 2分鐘 timeout

## 修改內容

### 1. In-process BAW engine
`core/messaging/__init__.py` — `_baw_ensure()` + `_run_baw()` 重寫
- Lazy import BAW modules in-process (save 0.3s per call)
- 保持 httpx connection pool warm 跨 message
- ThreadPoolExecutor for timeout handling (60s)
- `_baw_cfg_set()` 直接修改 config file + 更新 memory cache

### 2. Quick mode system prompt 精簡
`core/loop.py` — `build_system_prompt()` 新增 `quick_mode` flag
- Quick mode 只取 SOUL.md Identity + 核心靈魂 (~600 chars)
- 跳過法庭規則 / 工具描述 / permission 規則 / 動態 context
- 加入 "Quick mode" 指示：簡短、casual、1-2段

### 3. 新 Command: /model, /models
`core/messaging/__init__.py` — route() 處理 `/model <name>` 同 `/models`
- /model deepseek-v4-flash / kimi-k2.6 / MiniMax-M3
- 即時 switch model + 更新 config file
- 同步更新 Telegram command menu (`telegram.py`)

## 結果
- 正常回覆: ~1.0–1.5s (之前 ~1.7–3.0s)
- No more 2-min timeout (60s timeout, in-process)
- Model switching available via `/model` command

---

### 4. In-process import fix (2026-06-08 #2)
`core/messaging/__init__.py` — `_baw_ensure()` 工具註冊重寫

**問題：** `tools/__init__.py` 用 `from ..core.tools import register`（relative import），
in-process 直接 import 時 `tools` 係 top-level package，`..` 無法 resolve。
error 訊息顯示 `No module named 'baw'`（轉嫁錯誤）。

**Fix：** 改用 `importlib.util.spec_from_file_location()` 直接 load 每個 tool 檔案，
跳過 `tools/__init__.py` 避免 relative import 失敗。

**commit:** `8b7d016`
