# BAW 現實操作測試 — 任務清單

我有好多工作需要你做。請全部完成，然後將每項任務嘅結果輸出到 `/tmp/baw-test-report.md`，每個任務標題、簡短結果、同最後總結。

唔好省略步驟，唔好跳過任務，逐項完成。

---

## 任務 1：系統狀態
執行 `baw status` 同 `baw self-test --no-fetch`，輸出系統嘅 default model、connector 狀態、同 self-test 結果。

## 任務 2：檔案操作
建立一個檔案 `/tmp/baw-test-data.txt`，入面寫住「BAW 測試檔案 — 2026-06-13」。然後讀取呢個檔案，確認內容正確。最後將檔案改名做 `/tmp/baw-test-data-done.txt`。

## 任務 3：搜尋資訊
Search 「香港 pet-friendly 餐廳 中西區」，摘錄 3 間餐廳嘅名稱同地址。

## 任務 4：記憶系統
記低「測試記憶：我鍾意食壽司」。然後搜尋返呢條記憶，確認可以搵得返。

## 任務 5：Multi-step 流程
執行以下步驟：
1. 建立一個 file `/tmp/baw-summary.txt`
2. 寫入「Testing multi-step workflow」
3. Append 一句「Step 2 complete」
4. Read 返成個 file 確認內容
5. 最後話我知總共有幾多行

## 任務 6：Tool chaining
Search 而家香港天氣，然後用「香港天氣」同「東京天氣」做對比，寫一句簡短結論。

## 任務 7：錯誤處理
嘗試讀取一個唔存在嘅檔案 `/tmp/baw-nonexistent-12345.txt`。正常報告錯誤，唔好停頓，繼續下一個任務。

## 任務 8：呢個任務本身
寫出一句自我描述：用一句話解釋你係乜嘢系統。

## 任務 9：容量測試
Search 三個唔同 topics：
- 「香港 Hong Kong」
- 「Tokyo Japan」
- 「London UK」
然後 summarize 呢三個 search 結果各自有幾多個 result。

## 任務 10：完整報告
將以上所有任務嘅結果整理到 `/tmp/baw-test-report.md`，格式如下：

```markdown
# BAW Real-World Test Report
Date: 2026-06-13

## Task 1: System Status
**Result:** PASS/FAIL
[簡短結果]

## Task 2: File Operations
**Result:** PASS/FAIL
[簡短結果]

...如此類推每項任務...

## Summary
- Total: 10/10
- Pass: X
- Fail: Y
- Notes: ...
```

完成後話我知報告已經準備好。
