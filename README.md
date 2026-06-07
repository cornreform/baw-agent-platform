# BAW — Black And White Agent Platform

由零開始構建嘅 agent platform，Unified memory、built-in fact checking、transparent cost、adversarial 雙重靈魂。

## Quick Start

```bash
baw "你的 prompt"
baw --tone business "寫一份合作提案"
baw --status
```

## 架構

```
baw/                    ← Code repo
  baw                   CLI entry point
  core/
    llm.py              Multi-protocol LLM abstraction
    loop.py             Agent loop with courtroom
    adversarial.py      Angel/Devil dual-soul system
    permission.py       Risk-based permission engine
    memory.py           Unified memory (JSONL + scoring)
    context.py          Conversation context manager
    tools.py            Tool registry
    fact_checker.py     Built-in fact verification
    tone.py             Runtime tone switching
    dream.py            Weekly self-curation
  tools/
    bash.py             Shell execution tool
    read_file.py        File reading tool
    write_file.py       File writing tool
  BAW-PLAN.html         Full design document

~/.baw/                 ← User config
  config.yaml           Model, permission, tone, fact check config
  SOUL.md               Soul & behavioral rules
  .env                  API keys (NOT committed)
  memory/store.jsonl    Memory store
```

## Configuration

See `~/.baw/config.yaml` and `~/.baw/SOUL.md` for full configuration options.

## Dual-Soul Architecture

BAW 使用法庭式對抗機制：
- **👿 Devil** — 先發言，零執行權限，天生反對派
- **😇 Angel** — 聽完魔鬼後回應，有執行權限

Devil 分數 > Angel 分數 → ⛔ STOP
