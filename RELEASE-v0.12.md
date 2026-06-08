# BAW v0.12 — Agent Self-Modification + Voice Pipeline Fixes + Release Automation

## 新增

- **Agent 自我修改 Config** — BAW 可以通過對話直接修改 `config.yaml` 同 `.env`，唔使手動改 code
  - 新增 provider / model / capability 全部對話控制
  - `/reload` 指令套用修改
  - SOUL.md 加入完整 config schema + 修改指引
- **GitHub Release 自動化** — Sticky 可以直接 publish release
  - `gh` CLI v2.45.0 安裝 + auth login
  - 修正 PATH 遮擋問題（舊 Python `gh` script）
  - `git tag` → `git push` → `gh release create` 全自動
- **StickS3 v18 Firmware** — voice assistant 全面修復
  - `voice_assistant` component 取代無效嘅 `micro_wake_word`
  - 按掣真正開始 mic streaming → HA voice pipeline
  - 按 KEY1/KEY2 觸發 `voice_assistant.start()` — 唔再只係 set display
  - 消除 idle/listening 畫面多餘空白行
  - 顯示 mic 狀態指示 (`o o o`)
  - OTA 成功 compile + upload 到 StickS3 (.222)

## 修復

- `config.yaml` / `.env` 權限由 block 降為 warn — 容許 agent 自修改
- StickS3 按掣行為 — 由純 display flag 改為真正 `voice_assistant.start()`
- StickS3 display — idle/listening/speaking mode 唔再顯示空行
- StickS3 OTA workflow — 成功 compile + upload v18
- `gh` CLI PATH 遮擋問題 — `/usr/bin/gh` (official) 被 `~/.local/bin/gh` (舊 Python script) 搶先，已 rename

## 技術細節

- SOUL.md 新增 §「自我修改 Config」— 完整 config schema + 修改方法 + 權限表
- `noise_suppression_level: 2`, `volume_multiplier: 2.0` 優化收音質素
- Button-triggered 取代 wake word — 避免 StickS3 memory 壓力
- Release 自動化 — `gh` CLI v2.45.0 + PAT auth 已就緒，PATH fix 永久生效
- StickS3 ES8311 I2S pins: MCLK=18, BCLK=17, WS=15, DOUT=14, DIN=16（無 conflict with display CS GPIO41）
