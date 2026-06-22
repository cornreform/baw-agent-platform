# BAW — 完整安裝指南 (Standalone)

BAW v1.14.0 可以完全獨立安裝在任何 Linux 機上，唔需要第二個系統介入。

---

## 方法一：一鍵安裝（推薦）

```bash
curl -fsSL https://raw.githubusercontent.com/cornreform/baw-agent-platform/main/install.sh | bash
```

這個指令會：
1. **檢查依賴** — Python 3.11+、Git、uv（自動安裝）
2. **Clone repo** 到 `~/baw`
3. **安裝 Python 依賴**
4. **建立 CLI wrapper**（`baw` command）
5. **Bootstrapt SOUL.md** — 複製 SOUL.default.md → ~/.baw/SOUL.md
6. **檢測 Docker** — 如果有 Docker，提示部署

### 安裝後

1. 執行 setup wizard：
   ```bash
   baw --setup
   ```
   這會引導你設定 Telegram Bot Token、API keys 等。

2. 啟動 Telegram bot（需要 Docker）：
   ```bash
   cd ~/baw && docker compose up -d
   ```

---

## 方法二：Docker 部署

### 前置要求
- Docker Engine + Docker Compose
- Git
- Telegram Bot Token（從 @BotFather 取得）

### 步驟

```bash
# 1. Clone repo
git clone https://github.com/cornreform/baw-agent-platform.git ~/baw
cd ~/baw

# 2. 建立 config + env files
mkdir -p ~/.baw
cp SOUL.default.md ~/.baw/SOUL.md

# 3. 設定 Telegram Bot Token
echo "TELEGRAM_BOT_TOKEN=your_token_here" > ~/.baw/telegram.env

# 4. 設定 API keys（可選）
echo "DEEPSEEK_API_KEY=sk-..." >> ~/.baw/.env
echo "STEPFUN_API_KEY=sk-..." >> ~/.baw/.env

# 5. Build + Start
docker compose build baw-telegram
docker compose up -d baw-telegram

# 6. 驗證
docker ps --filter name=baw-telegram
docker logs baw-telegram --tail 20
```

---

## 方法三：搬機 / 遷移

BAW 可以透過 `self_migrate` tool 遷移到新機器：

1. 在舊機上匯出資料：
   ```
   baw "self_migrate(action='migrate')"
   ```
   這會產生：
   - `baw-export-<timestamp>.tar.gz` — config + memory + sessions + SOUL
   - `baw-bootstrap.sh` — 新機 bootstrap script

2. Copy export package 去新機

3. 在新機上執行 bootstrap：
   ```bash
   bash baw-bootstrap.sh
   ```

4. BAW 會自動 clone repo、restore data、build container、start

---

## 設定詳情

### Required: Telegram Bot Token

1. 去 @BotFather 創建一個 bot → 取得 token
2. 寫入 `~/.baw/telegram.env`：
   ```
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklmNOPqrstUVwxyz
   ```

### Optional: API Keys

| Provider | Env File | Env Key | 用途 |
|----------|----------|---------|------|
| DeepSeek | `~/.baw/.env` | `DEEPSEEK_API_KEY` | LLM (fallback) |
| Stepfun | `~/.baw/.env` | `STEPFUN_API_KEY` | LLM (default) |
| MiniMax | `~/.baw/.env` | `MINIMAX_API_KEY` | Vision, TTS, Image |
| Moonshot | `~/.baw/.env` | `MOONSHOT_API_KEY` | Tribunal (Kimi) |

最少需要一個 LLM provider。推薦：Stepfun（預設）+ DeepSeek（fallback）。

---

## 驗證安裝

```bash
# 檢查系統健康
baw --doctor

# 測試對話
baw "Hello — what can you do?"

# 如果有 Telegram bot
baw "Send a test message to Telegram"
```

---

## 目錄結構

```
~/baw/              # BAW source code
├── core/           # Engine (loop, llm, tools, context, memory)
├── tools/          # 30+ registered tools
├── cli/            # Command-line interface
├── config.yaml     # Source template config
├── docker-compose.yml
└── Dockerfile

~/.baw/             # BAW persistent data
├── config.yaml     # Runtime config (overrides source)
├── SOUL.md         # BAW's soul (personality + rules)
├── memory/         # Memory store
├── sessions/       # Conversation logs
├── cron/           # Internal cron jobs
└── logs/           # Token logs, error logs
```
