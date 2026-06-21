# Skill: Fix Verification Failure

## 問題診斷

當收到 `[SYSTEM] POST-TURN VERIFICATION FAILED` 時：

1. **Read the system message** — 搵出具体哪條 rule 被违反
2. **Search session history** — 用 `session_search` 搵相關對話
3. **Identify the pattern** — 常見類型：
   - Fabricated source (claim咗從未search的平台)
   - Unverified numbers (具體數字無backlink)
   - Forbidden phrases ("I have enough material now")

## 修復流程

### Step 1: SOUL.md patch (持久規則)

在 `~/.baw/SOUL.md` 加 Source Verification Gate：

```markdown
## Source Verification Gate（研究/分析 task 強制）

做 research、analysis、comparison、review 等 task 時，**每個 factual claim 必須有對應嘅 tool call output 做 backlink**。

- <b>搜咗先claim</b>：話「Amazon reviews 顯示…」之前，必須有 `web_search(query="...")` 或 `web_extract(url="...")` 嘅 result
- <b>數字要有來源</b>：任何具體數字必須標明出處，否則標明 ⚠️ unverified
- <b>禁止 fabricated sources</b>：唔可以 claim 從未搜過嘅平台
- <b>標記方式</b>：無 source 嘅 claim 加 `<b>⚠️ unverified</b>` 前綴，或者直接省略
```

### Step 2: loop.py patch (快速路徑)

`_process_btw` 用 `fresh_start=True` 會 bypass SOUL.md。必須在 `fresh_start` branch 直接注入 verification gate：

```python
if fresh_start:
    verification_gate = (
        "\n\n## [WARN] SOURCE VERIFICATION GATE (always on)\n"
        "For research/analysis tasks: EVERY factual claim MUST have a corresponding\n"
        "search/web_extract tool output as backlink...\n"
    )
    return verification_gate + "..."
```

### Step 3: 驗證修復

1. `grep -n "Source Verification" ~/.baw/SOUL.md` — 確認 SOUL.md 有規則
2. `grep -n "verification_gate" /app/core/loop.py` — 確認 loop.py 有修復
3. 重啟 BAW 後測試新對話

## 預防措施

- 每次 research/analysis task 結束前，逐條 claim 檢查有冇對應 search result
- 唔好「fill gaps」— 有 gap 就 report gap，唔好造野
- 數字一定要搵到出處先 claim
