# BAW Development Guide

## 開發環境

```bash
git clone <repo> ~/baw
cd ~/baw
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 專案結構

```
baw/
├── core/                    # 核心邏輯
│   ├── court.py             # 黑白法庭狀態機
│   ├── adversarial.py       # Devil/Angel 雙聲
│   ├── tribunal.py          # 多模型共識引擎
│   ├── loop.py              # Agent loop（主執行器）
│   ├── llm.py               # LLM 調用 + fallback
│   ├── tools.py             # 工具註冊 + 執行
│   ├── evolve.py            # 自我進化引擎
│   ├── delivery_log.py      # 送達紀錄
│   ├── context.py           # 對話上下文管理
│   └── messaging/           # 各平台 connector
│       ├── telegram.py      # Telegram bot
│       ├── telegram_async.py# Async transport layer
│       └── ...
├── tools/                   # 50+ 工具
│   ├── bash.py
│   ├── web_search.py
│   ├── search_files.py
│   ├── fusion_analyze.py
│   └── ...
├── cli/                     # CLI 命令
│   ├── main.py
│   └── commands/
│       ├── court_cmd.py     # `baw court` 命令
│       └── ...
├── tests/                   # 測試
│   ├── unit/                # 單元測試
│   ├── integration/         # 集成測試
│   └── e2e/                 # E2E 測試
├── DEBUG.md
├── DEPLOYMENT.md
├── ARCHITECTURE.md
└── SOUL.md                  # 系統靈魂（行爲規則）
```

## 開發守則

### 1. 黑白法庭優先

所有新功能應該用 court system 處理，唔好 bypass 法庭。

```python
# ✅ 正確：經法庭
from core.court import file_case_sync, CourtTier
case = file_case_sync(goal="做某某任務", force_tier=CourtTier.TIER_2_MAJOR)
result = case.final_summary

# ❌ 錯誤：直接執行（除非 Tier 0）
result = execute_tool("bash", {"command": "..."})
```

### 2. 輸出原則

- **結論先行** — 最重要嘅放第一行
- **唔 dump reasoning** — 唔好顯示「我先諗吓...」
- **唔 apology** — Error 就 report error
- **精簡** — 3-5 行 summary

### 3. 測試

新增功能必須有對應 test：

```bash
# 跑全部測試
python3 -m pytest

# 跑單個 test file
python3 -m pytest tests/unit/test_court.py -v

# 覆蓋率
python3 -m pytest --cov=core tests/
```

### 4. 行為矯正

如果用戶報告行為問題，唔好只係口頭承認 — 必須：

1. 找出 root cause（點解要咁做？）
2. 改 code 或 update SOUL.md
3. 寫 test 防止 regression
4. Commit + tag

### 5. Commit 格式

```
Phase X — 簡短描述

詳細說明改咗咩、點解改。
```

版本標籤：`v1.XX.0`

### 6. 新增 Tool

```python
# tools/my_tool.py
def my_tool(param1: str, param2: int = 0) -> str:
    \"\"\"Do something useful.\"\"\"
    # 實現...
    return result
```

然後在 `tools/__init__.py` `register_all()` 中註冊。

### 7. 新增 Platform

繼承 `core/messaging/__init__.py` 嘅 `BaseConnector`：

```python
@register("my_platform", "Description", "config_key")
class MyConnector(BaseConnector):
    def start(self): ...
    def stop(self): ...
    def send(self, chat_id, text): ...
```

### 8. 調試

```bash
# 睇 delivery log
cat ~/.baw/logs/delivery.jsonl | tail

# 睇 court cases
ls -la ~/.baw/court/cases/

# Debug mode
BAW_LOG_LEVEL=DEBUG baw-bot --platform telegram

# 手動測試 court
python3 -c "from core.court import file_case_sync, CourtTier; \
  case = file_case_sync('test', force_tier=CourtTier.TIER_2_MAJOR); \
  print(case.verdict, case.score)"
```

## 常見問題

- **Search 卡死** → 15s timeout + 10MB skip，如果仍卡死，檢查 search_path 有冇大量細檔
- **Court bypassed** → 檢查 mode 設定，只有 hybrid/tight 會自動用 court
- **Delivery log 無記錄** → 檢查 `~/.baw/logs/` 權限
- **Test 用咗真實路徑** → `_LOG_FILE` 已改爲 `_log_path()`，改 `dl._LOG_DIR` 即可隔離
