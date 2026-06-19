# BAW v0.22.0 — 對話功能驗證測試

用你對 the user 嘅真實認知（pet1pet2、MINI JCW、USL車業務、Alpaca交易）設計以下對話測試。每個測試 Send 一句俾 BAW，檢查 Response 是否符合預期。

---

## Test 1: 語言 — 粵語輸入 → 粵語輸出

**Send:**
```
pet1今日乖唔乖啊？
```

**Expected:**
- ⚠️ BAW 唔知pet1今日乖唔乖（冇 real-time info），但應該：
  - ✓ 用 **粵語** 回應
  - ✓ 唔會作故仔（honesty over polish）
  - ✓ 如果佢想 check memory 搵pet1資料，係正常行為

**執行的系統**: Language Rule（system prompt 強制粵語）

---

## Test 2: 記憶 Curator — Noise 過濾

**Send:**
```
好嘅
```

**Expected:**
- ✓ 回覆極短（單字確認都 ok）
- ✓ `memory recall` 唔會見到新嘅記憶 entry（因為 noise gate discard 咗）
- ✗ 唔應該出現 `[OK] Memory saved` 之類

**執行的系統**: Memory Curator — Noise Classification

---

## Test 3: 記憶 Curator — Preference 自動保存

**Send:**
```
我鍾意你每次回答都先講結論，再講細節，最後先俾技術資料
```

**Expected:**
- ✓ Response 結構：先結論，再細節
- ✓ `memory recall` → 出現呢條 preference（分類 = preference，score ≥ 0.85）
- ✓ 下次對話 BAW 記得你嘅 output 偏好

**執行的系統**: Memory Curator — 分類 + 高價值評分 + 自動記憶

---

## Test 4: 記憶 Curator — Conflict Detection（修正舊記憶）

呢個需要 BAW 已經有相關記憶。Send：

```
之前我話過 Alpaca 係用 paper trading，但其實我個 account 係 live 嚟㗎
```

**Expected:**
- ✓ BAW 應該 detect 到 conflict（同現有記憶「Alpaca Live #226854146」相關）
- ✓ 回覆應該提及「已更新記憶」或「修正咗」
- ✓ `memory search "alpaca live"` → 內容已被修正
- ✗ 唔應該出現兩條矛盾記憶

**執行的系統**: Memory Curator — detect_conflicts → "update" action

---

## Test 5: Output Structure — 三層結構

**Send:**
```
解釋一下我而家用緊嘅 AI provider 配置，包括每個 provider 嘅角色同 fallback 順序
```

**Expected:**
```
✅ <一句結論 — Layer 1>

<2-3 bullets 關鍵配置 — Layer 2>

（冇 Layer 3 raw data 除非你問）
```

- ✓ 第一句就係最重要結論（唔係「等我分析一下」）
- ✓ 冇「總結」section 喺尾
- ✓ 冇 raw JSON dump
- ✓ 精簡，唔超過 6-8 行

**執行的系統**: Output Structure Rule（system prompt Three Layers）

---

## Test 6: Anti-Duplication — 唔准重複自己

**Send:**
```
幫我 check 吓而家 BAW 嘅 cron job 狀態同 memory store 統計
```

**Expected:**
- ✓ 結果一次性俾晒（唔好分開兩輪 message）
- ✓ **冇「總結」/「Summary」/「以下係」** section
- ✓ 冇重複嘅內容（例如開頭講一次，結尾又 summary 一次）
- ✓ 結構清晰：cron status → memory stats

**執行的系統**: Output Validator — anti-duplication + _compress_verbose

---

## Test 7: Tool Cap Awareness — 複雜任務自動收尾

**Send:**
```
幫我全面系統審計：檢查 config.yaml 所有 provider、capabilities、cron job、memory store、safety rules、tool 文件完整度，然後俾一份完整報告
```

**Expected:**
- ✓ BAW 應該喺 **~20-22 個 tool calls** 內自己收尾
- ✓ 唔應該等到 25 cap hit 被 force stop
- ✓ Response 係完整報告（唔係 cut-off 英文）
- ✓ 報告有層次（先 summary，再逐項細節）

**執行的系統**: System prompt Tool Turns Budget → self-termination

---

## Test 8: Context Compaction — 長對話觸發壓縮

呢個需要連續 Send 多條訊息製造 context。順序 Send：

```
1. 你記唔記得我養咗咩狗？
2. 佢哋幾重？
3. 我揸咩車？
4. USL 係做咩㗎？
5. 我嘅交易 account 係 paper 定 live？
6. 我用緊邊個 AI provider 做 vision？
7. STT 用邊個 provider？
8. 我嘅 trading strategy 係咩？
9. 點解我要用 RSI + MACD？
10. BAW 嘅 executor 用邊個 model？
```

重複到 context 累積 ~30000 chars → compaction 觸發

**Expected:**
- ✓ 某個 response 開頭出現 `✅ 對話歷史壓縮完成 — N 條舊訊息已合併為摘要，保留最新 5 輪完整內容`
- ✓ BAW 仍然可以正確回答最新嘅問題（記憶冇斷）
- ✓ 舊 turn 已被壓縮但關鍵資訊保留

**執行的系統**: ctx.compact() at 30000 chars threshold

---

## Quick Run（濃縮版）

一次過 Send 呢 4 條，cover 主要功能：

```
1. pet1今日乖唔乖啊？
2. 我鍾意你每次回答都先講結論，再講細節
3. 之前我話過 Alpaca 係 paper，但其實係 live account 嚟㗎
4. 解釋我而家用緊嘅 AI provider 配置
```

**Expected:**
- #1 → 粵語 ✓
- #2 → preference saved to memory ✓
- #3 → conflict detected, memory updated ✓
- #4 → 三層結構輸出 ✓
