# BAW Architecture — 黑白法庭 Agent 哲學

> BAW 唔係 Hermes 或 OpenClaw 嘅超集。我哋借鑑佢哋賴以運作正常嘅系統，但緊守自己嘅核心：**黑白法庭** + **BAW 自身 Agent 哲學**。

---

## 核心哲學

### 黑白法庭（Black & White Court）

BAW 嘅根本差異在於所有任務都經過一個**法庭系統**審理，唔係直接執行。

```
User Message → Court.file_case()
                    │
                    ▼
  ┌──────────────────────────────────────────────┐
  │  FILED → TRIAGE → INDICTMENT → HEARING →    │
  │         EXECUTION → REVIEW → VERDICT → CLOSED │
  └──────────────────────────────────────────────┘
```

**法庭角色：**
| 角色 | 身份 | 權力 | 說明 |
|------|------|------|------|
| 🖤 檢察官（Devil） | 批評者 | 零執行權 | 獨立分析任務漏洞、風險、邏輯矛盾 |
| 🤍 守護天使（Angel） | 支持者 | 零執行權 | 獨立設計執行計劃、評估可行性 |
| 👨‍⚖️ 法官（Judge） | 評分者 | 零執行權 | 評分 0-10，決定 APPROVED/RETRY/APPEAL/DISMISS |
| 📎 證物（Evidence） | 事實紀錄 | — | 每個工具執行結果都係證物 |
| ⚖️ 上訴庭（Appellate） | 重審者 | 可推翻原判 | 原審兩次未達標，自動移交上級模型重審 |

**四級審理（Tier）：**
- **Tier 0 (Fast Lane)** — 簡單任務，直接執行工具，無法庭費用
- **Tier 1 (Minor)** — 法官 only，執行後評分
- **Tier 2 (Major)** — Devil + Angel + 法官，完整審判程序
- **Tier 3 (Supreme)** — 完整審判 + 上訴程序，最高級別審查

### BAW 自身 Agent 哲學

BAW 唔係一個 prompt engineering 項目，而係一個自我進化嘅 Agent 平台：

1. **自我判斷複雜度** — 自然語言理解任務難度，自動匹配處理深度（auto/quick/hybrid/tight）
2. **自我進化** — 每週自動分析行為模式、用戶修正、分數漂移，自動調整 SOUL.md 同 config
3. **多模型融合（Fusion）** — 對複雜任務，用多個平價模型協作 = 唔需要貴模型都可以達到高品質
4. **行為矯正** — 用戶指出問題時，系統自動記錄爲 Lessons Learned，下次避免
5. **輸出原則** — Lead with result，唔 dump 推理過程，唔 apology

---

## 系統架構

```
┌─────────────────────────────────────────────────┐
│                    User Interface                │
│  Telegram / Discord / CLI / Matrix / Signal     │
└──────────────────────┬──────────────────────────┘
                       │ Message
┌──────────────────────▼──────────────────────────┐
│            Messaging Layer (async transport)     │
│  telegram_async.py / telegram.py / discord.py   │
└──────────────────────┬──────────────────────────┘
                       │ Processed message
┌──────────────────────▼──────────────────────────┐
│                 Agent Loop (loop.py)             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐ │
│  │   Court    │  └──► Execute │  │  Verify    │ │
│  │  (Devil/   │  │    Tools   │  │  (Judge/   │ │
│  │   Angel)   │  │            │  │  Verifier) │ │
│  └────────────┘  └────────────┘  └────────────┘ │
└──────────────────────┬──────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
┌──────────────┐ ┌────────┐ ┌──────────────┐
│   Tools      │ │  LLM   │ │   Memory     │
│  (50+)       │ │  Multi │ │   Store      │
│              │ │  Model │ │   SOUL.md    │
│              │ │ Fusion │ │   KG         │
└──────────────┘ └────────┘ └──────────────┘
```

---

## 與 Hermes/OpenClaw 嘅差異

| 層面 | BAW | Hermes | OpenClaw |
|------|-----|--------|----------|
| **核心不同** | 法庭審判 | Agent framework | Agent framework |
| **任務處理** | 法庭先審後判 | 直接執行 | 直接執行 |
| **自我進化** | 每週自動分析+調整 | 無 | 無 |
| **多模型協作** | Fusion Mode（多位模型） | 單模型 | 單模型 |
| **行為紀錄** | Delivery log + court evidence | — | — |
| **上訴機制** | Tier 3 多模型上訴 | — | — |
| **輸出哲學** | 結論先行，無 reasoning dump | 可顯示 reasoning | 可顯示 reasoning |
| **依賴** | 自建（無 gateway） | Hermes Gateway | 需要特定 infra |

---

## 關鍵設計決策

1. **Async transport** (Phase 1) — 0 threads for I/O，改用 asyncio + webhook
2. **Tool isolation** (Phase 3) — 每個 tool 用獨立 subprocess 執行，唔會污染 shared state
3. **Focus Mode** (Phase 4) — 100 tool turns，auto-retry，直到完成
4. **Fusion Mode** (Phase 5) — 平行多模型 research + synthesis
5. **Delivery log** (Phase 6) — 每個 send 嘅訊息都有 track record
6. **Graceful shutdown** (Phase 7) — SIGTERM 後 drain in-flight tasks
7. **Per-mode max_tokens** (Phase 8) — quick=4K, hybrid=8K, tight=16K
8. **Court default path** (Phase 9) — hybrid/tight 模式自動使用法庭系統
9. **Court score drift** (Phase 10) — 每週分析法庭分數，自動調整 tier/model
10. **Search timeout** (Phase 11) — 15s timeout + 10MB file skip（唔會卡死）

---

## 快速參考

```bash
# 啟動
docker compose up -d

# 法庭命令
baw court recent       # 最近 5 宗案件
baw court detail C001  # 查看完整案件記錄

# 查看 delivery 狀態
python3 -c "from core.delivery_log import delivery_stats; print(delivery_stats(60))"

# 健康檢查
curl http://localhost:8080/health

# 優雅重啟
kill -TERM <PID>
kill -HUP  <PID>   # 重新載入 config
```
