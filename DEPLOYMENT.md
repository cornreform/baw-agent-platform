# BAW Bot — 部署指南

## 運行模式

BAW Bot 支援兩種運行模式，可以根據環境選擇。

### 模式 1：Long Polling（預設，適合本地／VPN）

```
docker compose up -d
```

無需公開 URL，Bot 直接用 Telegram API 的 `getUpdates` 拉取訊息。

### 模式 2：Webhook（適合正式 Production）

需要：
- 公開 HTTPS URL（Nginx / Cloudflare Tunnel / ngrok）
- Telegram Bot Token

```bash
# 啟動 webhook 模式
docker compose run -d \
  -e BAW_WEBHOOK_URL=https://your-domain.com/webhook \
  -e BAW_WEBHOOK_PORT=8080 \
  --service-ports baw-telegram
```

Webhook 模式會：
1. 自動 call `setWebhook` 註冊 URL
2. 啟動 FastAPI server 收 update
3. `/health` endpoint 提供健康檢查 + delivery stats

---

## 優雅重啟（Zero-downtime）

BAW Bot 支援 **SIGTERM** 優雅關機：

```bash
# 正常重啟
docker compose stop -t 30  # 俾 30 秒 drain in-flight tasks
docker compose up -d

# 或者用 kill 信號
kill -TERM <PID>    # 優雅關機
kill -HUP  <PID>    # 重新載入 config（唔使 restart）
```

關機流程：
1. 停止 scheduler（唔再接新 cron task）
2. Stop 所有 messaging connectors
3. 最多等 15 秒俾 in-flight tasks 完成
4. Save sessions + 清理
5. Exit 0

---

## 健康檢查

Docker healthcheck 用以下方法檢測：
1. If 8080 port open: `curl http://localhost:8080/health` — 回傳 uptime + active tasks + delivery stats
2. Fallback: `pgrep -f baw-bot` — 確保 process 仲活緊

```bash
# 手動檢查
docker inspect --format='{{json .State.Health}}' baw-telegram | jq

# 或直接 hit endpoint（webhook mode）
curl -s http://localhost:8080/health | jq
curl -s http://localhost:8080/health/verbose | jq  # 詳細版
```

---

## Delivery Log（送達監控）

每個 send 嘅訊息會自動記錄到 `~/.baw/logs/delivery.jsonl`。

```bash
# 看最近 60 分鐘 delivery stats
cd /app && python3 -c "
from core.delivery_log import delivery_stats, recent_deliveries
print(delivery_stats(60))
import json
print(json.dumps(recent_deliveries(30, limit=5), indent=2, ensure_ascii=False))
"
```

---

## Log 睇法

```bash
docker logs baw-telegram -f --tail 100
```

Log level 控制：
- `BAW_LOG_LEVEL=INFO` （預設）
- `BAW_LOG_LEVEL=DEBUG` （詳細 tool call）

---

## 資源

| 環境變數 | 用途 | 預設 |
|----------|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | — |
| `BAW_WEBHOOK_URL` | Webhook 公開 URL | — |
| `BAW_WEBHOOK_PORT` | Webhook server port | 8080 |
| `BAW_LOG_LEVEL` | Log level | INFO |
| `BAW_ADMIN_CHAT_ID` | Admin 通知 chat | — |

---

## 升級

```bash
cd ~/baw
git pull
docker compose build --no-cache baw-telegram
docker compose up -d
```

或者用 baw CLI：

```bash
python3 -m cli.main restart
```
