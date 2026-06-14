# BAW — First-Time Setup Guide

這份文件引導你由頭到尾安裝 BAW，每一步都有說明。

---

## Step 1: 一鍵安裝

在 terminal 輸入：

```bash
curl -fsSL https://raw.githubusercontent.com/cornreform/baw-agent-platform/main/install.sh | bash
```

這個指令會做以下事：

1. **檢查依賴** — 查有無 `uv`（Python 包管理器）、Python 3.11+、Git
2. **自動裝 `uv`** — 如果沒有，會自動從 astral.sh 下載安裝
3. **自動裝 Python** — 如果沒有 Python 3.11+，`uv` 會幫你裝
4. **Clone repo** — 下載 BAW 源碼到 `~/baw`
5. **安裝依賴** — 用 `uv pip` 裝所有 Python 套件
6. **建立 CLI wrapper** — 讓 `baw` 指令可以用
7. **自動加 PATH** — 如果 `~/.local/bin` 不在 PATH，會問你是否自動加入

裝完後，你會見到：

```
✅  BAW v0.20.1 installed successfully!

Next steps:
  1. Run the setup wizard:
     baw --setup
  2. After setup, verify everything works:
     baw --doctor
  3. Test the agent:
     baw "Hello BAW!"
```

> 💡 如果 `baw` 指令找不到，試下 `source ~/.bashrc` （或 `~/.zshrc`），或開個新 terminal。

---

## Step 2: 設定精靈（baw --setup）

`baw --setup` 是互動式設定，會行你過以下步驟：

### 2.1 Default Model（主要模型）

這是 BAW 執行任務時用的主要 LLM。

| 選項 | 說明 | 適合誰 |
|------|------|--------|
| `deepseek-v4-flash` | 快、便宜，大部份任務夠用 | 大多數用家 |
| `MiniMax-M3` | 多模態（圖片+語音+文字） | 需要 vision/TTS 功能 |
| `claude-sonnet-4` | 質素最高，最貴 | 對質量要求極高 |

**Fallback model** — 當主模型失敗時自動切換。
必須用**不同 provider**的模型，例如主模型用 DeepSeek，fallback 用 MiniMax。

### 2.2 API Keys（核心設定）

每個 key 輸入後會**即時測試**，錯誤會即刻提示。

**Minimum requirement:** 至少要有一個 provider 的 key。

| Provider | 特點 | Key 名稱 |
|----------|------|----------|
| DeepSeek | 快、便宜、支援理性推理 | `DEEPSEEK_API_KEY` |
| MiniMax | 多模態（vision + TTS） | `MINIMAX_API_KEY` |
| OpenAI | GPT-4o 系列 | `OPENAI_API_KEY` |
| Stepfun | 支援粵語 TTS | `STEPFUN_API_KEY` |

> 💡 API key 存於 `~/.baw/.env`，不會寫入 config.yaml。

**Plan Type** — 部分 provider 有多個 endpoint（如 Stepfun 的 standard/step-plan/china），設定精靈會顯示每個 plan 的區別讓你選。

### 2.3 Capabilities（能力自動配置）

根據你提供的 API keys，BAW 自動配置：

- **Chat** — 必須，用 default model
- **STT** — 語音轉文字（需 Stepfun 或 MiniMax key）
- **TTS** — 文字轉語音（需 Stepfun 或 MiniMax key）
- **Vision** — 圖片理解（需 MiniMax-M3 或支援 vision 的 model）

### 2.4 Behaviour（行為設定）

| 設定 | 選項 | 說明 |
|------|------|------|
| **Mode** | quick / hybrid / tight | 執行深度（越 tight 越徹底） |
| **Tone** | casual / business / teaching / ... | 回應語氣 |
| **Court** | true / false | 是否啟用 Angel/Devil 法庭 |
| **Fact check** | off / normal / strict | 事實查證級別 |

### 2.5 Messaging Platforms（可選）

BAW 支援多個聊天平台，可以同時運行。純 CLI 使用可以全部 skip。

設定精靈會顯示平台選單（1-6），你可以配置多個：

| 平台 | 需要什麼 | 難度 |
|------|---------|------|
| Telegram | Bot Token（@BotFather）| ⭐ 最簡單 |
| Discord | Bot Token + 邀請 bot 入 server | ⭐⭐ |
| Slack | Bot Token + App Token（Socket Mode）| ⭐⭐ |
| Matrix | Homeserver + username + token | ⭐⭐ |
| Signal | signal-cli daemon + 電話號碼 | ⭐⭐⭐ |
| WhatsApp | Meta Developer 帳戶 + webhook | ⭐⭐⭐⭐ |

> 💡 之後可以用 `baw --cfg set <platform>.<key> <value>` 補加或修改。

---

## Step 3: 驗證安裝（baw --doctor）

```bash
baw --doctor
```

這個指令會檢查：

- ✅ config.yaml 格式正確
- ✅ 至少一個 provider 配置完成
- ✅ API keys 可以讀取
- ✅ 依賴套件已安裝
- ✅ Disk 空間足夠
- ✅ Git repo 狀態正常

如果有問題，會顯示 ❌ 和修復建議。

---

## Step 4: 第一次運行

```bash
baw "Hello BAW!"
```

如果一切正常，你會看到 BAW 的回應。

---

## 常見問題

**Q: `baw: command not found`**
A: 跑 `source ~/.bashrc` （或 `~/.zshrc`），或重新開個 terminal。

**Q: `❌ No API key for deepseek`**
A: 跑 `baw --setup` 補上 API key，或手動寫入 `~/.baw/.env`。

**Q: `❌ Invalid key (401 Unauthorized)`**
A: API key 錯誤或已過期。到 provider 網站檢查或重新生成。

**Q: `⚠️ No Telegram token`**
A: 這是警告不是錯誤。純 CLI 使用不需要 Telegram。

**Q: `❌ Slack 連接失敗`**
A: 確認兩個 token 都有：bot_token （xoxb-）和 app_token （xapp-）。在 Slack API 網站檢查 Socket Mode 是否啟用。

**Q: `❌ Discord 無法讀取消息`**
A: 確認在 Discord Developer Portal 啟用了 "Message Content Intent"。

**Q: `❌ Matrix 登入失敗`**
A: 嘗試用 access_token 而不是 password。在 Element 設定 → Help & About → Access Token 可以找到。

**Q: `❌ signal-cli not found`**
A: Signal 需要獨立安裝 signal-cli daemon。見 https://github.com/AsamK/signal-cli

---

## 檔案位置速查

| 檔案 | 路徑 | 用途 |
|------|-------|------|
| 程式碼 | `~/baw/` | Git repo，更新時 `git pull` |
| 設定 | `~/.baw/config.yaml` | 所有設定（非敏感） |
| API Keys | `~/.baw/.env` | 金鑰（不會進 Git） |
| 記憶 | `~/.baw/memory.jsonl` | 學習記憶 |
| 備份 | `~/.baw/backups/` | 自動備份 |

---

## 接下來

- `baw --help` — 查看所有指令
- `baw --cfg list` — 查看當前設定
- `baw --update` — 更新到最新版
