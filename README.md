
<!--
╔══════════════════════════════════════════════════════════╗
║  BAW — Black And White Agent Platform                   ║
║  Bilingual README (Traditional Chinese + English)        ║
╚══════════════════════════════════════════════════════════╝
-->

<br>
<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-blueviolet" alt="v1.0.0">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macOS-lightgrey" alt="Linux | macOS">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

<h1 align="center">⚫ BAW — Black And White ⚪</h1>
<p align="center"><strong>由零打造嘅 Agent Platform • Built from scratch agent platform</strong></p>
<p align="center">
  🤍🖤 Angel/Devil 雙魂法庭 • Protocol-agnostic LLM • 永不放棄哲學<br>
  🤍🖤 Angel/Devil Dual-Soul Court • Protocol-agnostic LLM • Never Surrender
</p>

---

<!-- ═══ 繁體中文 ═══ -->
<h2>📖 繁體中文</h2>

<h3>BAW 係咩？</h3>

<p><strong>BAW (Black And White)</strong> 係一套由零開始構建嘅 Agent Platform，唔依賴 LangChain、AutoGPT、或任何現有 framework。個名嚟自兩隻狗仔（黑白配），代表系統嘅核心哲學：<strong>🤍 Angel（執行者）</strong> vs <strong>🖤 Devil（反對派）</strong> 嘅法庭式對抗。</p>

<h3>🚀 Quick Start</h3>

<pre>
# 安裝
git clone https://github.com/cornreform/baw-agent-platform.git
cd baw-agent-platform
pip install pyyaml duckduckgo-search
ln -sf $PWD/baw ~/.local/bin/baw

# 設定 API Key（~/.baw/.env）
echo "DEEPSEEK_API_KEY=sk-your-key" >> ~/.baw/.env

# ✨ 即刻用！
baw "list files in current directory"
baw --tone business "寫一份合作提案"
baw --btw "而家幾點？"
baw --setup           # 互動式設定精靈
baw                   # 互動式 Chat 模式
</pre>

<h3>🎯 核心特色</h3>

<table>
  <tr><th>特色</th><th>說明</th></tr>
  <tr><td>⚖️ Angel/Devil 法庭</td><td>Devil 永遠先發言，零執行權限；Angel 聽完再行。Devil 分數 > Angel → STOP</td></tr>
  <tr><td>🧠 永不放棄哲學</td><td>失敗 → retry → replan → rollback。6 種策略用盡先上報，唔問用戶</td></tr>
  <tr><td>🔌 協議無關 LLM</td><td>OpenAI / Anthropic / Google 協議通吃。一行 config 轉模型，內置 cost tracking</td></tr>
  <tr><td>⚙️ 三種執行模式</td><td>Quick（最快）/ Hybrid（平衡）/ Tight（完整 court+plan+verify）</td></tr>
  <tr><td>🗣️ 6 種語氣 Profile</td><td>casual / business / teaching / client-doc / ot-rt / stepwise，隨時切換</td></tr>
  <tr><td>📝 自我學習技能</td><td><code>--learn-skill "描述"</code> 自動分析拆解生成 YAML skill</td></tr>
  <tr><td>🔄 Scheduler 排程</td><td>Cron 表達式定時任務，60s 背景 daemon</td></tr>
  <tr><td>📁 背景 Async Task</td><td><code>--delegate</code> 背景執行，主 terminal 即時 free，max 3 concurrent</td></tr>
  <tr><td>🐙 GitHub 整合</td><td>issues / PRs / CI / repos 直接操作</td></tr>
  <tr><td>🔍 開放 Search Provider</td><td>內置 DuckDuckGo（免費），可 pluggable 升級</td></tr>
  <tr><td>💾 統一記憶 + 事實查證</td><td>JSONL append‑only + 內部評分 + 三級 fact check</td></tr>
  <tr><td>🛡️ 三級權限引擎</td><td>High（禁止 sudo/rm -rf）/ Medium（提示）/ Low（允許）</td></tr>
  <tr><td>📊 HTML Dashboard</td><td><code>--board</code> 一鍵生成深色主題系統儀錶板</td></tr>
  <tr><td>💬 互動式 CLI Chat</td><td>彩色 banner + Tab 補全 slash commands + 20 個指令</td></tr>
  <tr><td>⚙️ Setup Wizard + Config CLI</td><td><code>--setup</code> / <code>--cfg set/get/list</code> 即時設定</td></tr>
  <tr><td>📁 檔案版本歷史 + Auto Git</td><td>每次寫入 SHA256 + ISO timestamp + 自動 commit</td></tr>
</table>

<h3>⚙️ 完整指令一覽</h3>

<pre>
baw "prompt"                     # 執行 agent（單句模式）
baw                              # 互動式 Chat 模式
baw --mode quick/hybrid/tight    # 執行模式
baw --tone &lt;profile&gt;             # 語氣 override
baw --model &lt;id&gt;                # 模型 override
baw --verbose                    # 詳細輸出 + 成本
baw --dry-run                    # 試行（唔改 file）
baw --btw "question"             # 快速 LLM 一問一答
baw --delegate "task"            # 背景執行任務
baw --task-id &lt;id&gt;              # 檢查背景任務
baw --tasks                      # 列表背景任務
baw --task-cancel &lt;id&gt;          # 取消背景任務
baw --version                    # 版本
baw --status                     # 系統狀態
baw --remember "text"            # 儲存記憶
baw --search "query"             # 搜尋記憶
baw --dream                      # 自我整理
baw --setup                      # 互動設定精靈
baw --cfg list|get|set|help      # Config CLI
baw --board                      # HTML Dashboard
baw --gh issues|prs|ci|repos     # GitHub 操作
baw --schedule-list|add|rm       # 排程管理
baw --skill-list|run             # Skills 管理
baw --learn-skill "desc"         # 自我學習技能
baw --learn-url &lt;url&gt;           # 從 URL 學技能
baw --search-provider list|test  # Search provider 管理
</pre>

<h3>🏗️ 架構</h3>

<pre>
baw/                        ← Code repo
├── baw                     CLI entry point（Python）
├── core/                   核心模組
│   ├── loop.py             Agent loop（plan → execute → report）
│   ├── llm.py              多協議 LLM abstraction
│   ├── adversarial.py      Angel/Devil 雙魂法庭
│   ├── tools.py            Tool registry
│   ├── permission.py       三級權限引擎
│   ├── memory.py           JSONL 記憶 + 評分
│   ├── fact_checker.py     事實查證（三級）
│   ├── tone.py             語氣 profile
│   ├── scheduler.py        Cron 排程 daemon
│   ├── skills.py           YAML skill 系統
│   ├── learn.py            自我學習技能
│   ├── board.py            HTML Dashboard
│   ├── task_manager.py     背景 Task 管理
│   ├── github.py           GitHub 整合
│   ├── search.py           開放 search provider
│   ├── setup.py            互動式設定精靈 + Config CLI
│   ├── commands.py         Slash commands
│   ├── display.py          步驟顯示格式化
│   ├── dream.py            每週自我整理
│   ├── checkpoint.py       Checkpoint / rollback
│   ├── degradation.py      Tool degradation chains
│   ├── file_history.py     檔案版本 SHA256
│   ├── autosave.py         自動 git commit
│   ├── render.py           HTML renderer
│   └── verifier.py         Per-step verify
├── tools/                  內置工具
│   ├── bash.py             Shell 執行
│   ├── read_file.py        讀檔案
│   ├── write_file.py       寫檔案
│   └── web_search.py       Web search
├── config.yaml             預設配置
└── docs/                   GitHub Pages 文檔

~/.baw/                     ← 用戶設定目錄
├── config.yaml             用戶配置
├── SOUL.md                 Soul / 行為規則
├── .env                    API keys
├── memory/store.jsonl      記憶儲存
├── skills/*.yaml           自訂 skills
└── tasks/                  背景任務輸出
</pre>

<h3>🔧 設定</h3>

<pre>
# 互動式設定精靈
baw --setup

# 即時 Config CLI
baw --cfg list                    # 顯示所有設定
baw --cfg get model.default       # 查某個設定
baw --cfg set mode hybrid         # 即時修改
baw --cfg set tone.default business
baw --cfg set adversarial.enabled false  # 熄咗法庭

# 直接編輯 YAML 都得
vim ~/.baw/config.yaml
</pre>

<hr>

<!-- ═══ English ═══ -->
<h2>📖 English</h2>

<h3>What is BAW?</h3>

<p><strong>BAW (Black And White)</strong> is an agent platform built entirely from scratch — no LangChain, no AutoGPT, no vendor framework. Named after two dogs (black & white), it embodies the core philosophy of <strong>🤍 Angel (executor)</strong> vs <strong>🖤 Devil (opposition)</strong> courtroom-style adversarial debate.</p>

<h3>🚀 Quick Start</h3>

<pre>
# Install
git clone https://github.com/cornreform/baw-agent-platform.git
cd baw-agent-platform
pip install pyyaml duckduckgo-search
ln -sf $PWD/baw ~/.local/bin/baw

# Set API Key (~/.baw/.env)
echo "DEEPSEEK_API_KEY=sk-your-key" >> ~/.baw/.env

# ✨ Go!
baw "list files in current directory"
baw --tone business "write a proposal"
baw --btw "What time is it?"
baw --setup           # Interactive setup wizard
baw                   # Interactive Chat mode
</pre>

<h3>🎯 Core Features</h3>

<table>
  <tr><th>Feature</th><th>Description</th></tr>
  <tr><td>⚖️ Angel/Devil Court</td><td>Devil speaks first with ZERO execution power. Angel listens then acts. Devil score > Angel → STOP</td></tr>
  <tr><td>🧠 Never Surrender</td><td>Fail → retry → replan → rollback. Exhausts 6 strategies before reporting — never asks user</td></tr>
  <tr><td>🔌 Protocol-agnostic LLM</td><td>OpenAI / Anthropic / Google protocols. One-line config switch, built-in cost tracking</td></tr>
  <tr><td>⚙️ 3 Execution Modes</td><td>Quick (fastest) / Hybrid (balanced) / Tight (full court+plan+verify)</td></tr>
  <tr><td>🗣️ 6 Tone Profiles</td><td>casual / business / teaching / client-doc / ot-rt / stepwise, switch anytime</td></tr>
  <tr><td>📝 Self-Learning Skills</td><td><code>--learn-skill "description"</code> auto-analyzes and generates YAML skill</td></tr>
  <tr><td>🔄 Cron Scheduler</td><td>Cron-expression scheduled tasks, 60s background daemon</td></tr>
  <tr><td>📁 Async Background Tasks</td><td><code>--delegate</code> runs in background, main terminal freed instantly, max 3 concurrent</td></tr>
  <tr><td>🐙 GitHub Integration</td><td>issues / PRs / CI / repos directly from CLI</td></tr>
  <tr><td>🔍 Open Search Provider</td><td>Built-in DuckDuckGo (free, no key), pluggable upgrade</td></tr>
  <tr><td>💾 Unified Memory + Fact Check</td><td>JSONL append-only + internal scoring + 3-level fact verification</td></tr>
  <tr><td>🛡️ 3-Level Permission Engine</td><td>High (block sudo/rm -rf) / Medium (warn) / Low (allow)</td></tr>
  <tr><td>📊 HTML Dashboard</td><td><code>--board</code> generates a dark-themed system dashboard</td></tr>
  <tr><td>💬 Interactive CLI Chat</td><td>Colored banner + Tab completion + 20 slash commands</td></tr>
  <tr><td>⚙️ Setup Wizard + Config CLI</td><td><code>--setup</code> / <code>--cfg set/get/list</code> — real-time config</td></tr>
  <tr><td>📁 File History + Auto Git</td><td>Every write logged with SHA256 + ISO timestamp + auto commit</td></tr>
</table>

<h3>⚙️ Full Command Reference</h3>

<pre>
baw "prompt"                     # Run agent (single-shot)
baw                              # Interactive Chat mode
baw --mode quick/hybrid/tight    # Execution mode
baw --tone &lt;profile&gt;             # Tone override
baw --model &lt;id&gt;                # Model override
baw --verbose                    # Verbose output + cost
baw --dry-run                    # Dry run (no changes)
baw --btw "question"             # Quick LLM question
baw --delegate "task"            # Background task
baw --task-id &lt;id&gt;              # Check task status
baw --tasks                      # List background tasks
baw --task-cancel &lt;id&gt;          # Cancel background task
baw --version                    # Show version
baw --status                     # System status
baw --remember "text"            # Save memory
baw --search "query"             # Search memory
baw --dream                      # Self-curation
baw --setup                      # Setup wizard
baw --cfg list|get|set|help      # Config CLI
baw --board                      # HTML Dashboard
baw --gh issues|prs|ci|repos     # GitHub operations
baw --schedule-list|add|rm       # Schedule management
baw --skill-list|run             # Skill management
baw --learn-skill "desc"         # Self-learn skill
baw --learn-url &lt;url&gt;           # Learn from URL
baw --search-provider list|test  # Search provider mgmt
</pre>

<h3>🏗️ Architecture</h3>

<pre>
baw/                        ← Code repo
├── baw                     CLI entrypoint (Python)
├── core/                   Core modules
│   ├── loop.py             Agent loop (plan → execute → report)
│   ├── llm.py              Multi-protocol LLM abstraction
│   ├── adversarial.py      Angel/Devil dual-soul court
│   ├── tools.py            Tool registry
│   ├── permission.py       3-level permission engine
│   ├── memory.py           JSONL memory + scoring
│   ├── fact_checker.py     3-mode fact verification
│   ├── tone.py             Tone profiles
│   ├── scheduler.py        Cron scheduler daemon
│   ├── skills.py           YAML skill system
│   ├── learn.py            Self-learning skills
│   ├── board.py            HTML Dashboard generator
│   ├── task_manager.py     Async task manager
│   ├── github.py           GitHub integration
│   ├── search.py           Open search provider registry
│   ├── setup.py            Setup wizard + Config CLI
│   ├── commands.py         Slash commands
│   ├── display.py          Step display formatter
│   ├── dream.py            Weekly self-curation
│   ├── checkpoint.py       Checkpoint / rollback
│   ├── degradation.py      Tool degradation chains
│   ├── file_history.py     File SHA256 history
│   ├── autosave.py         Auto git commit
│   ├── render.py           HTML renderer
│   └── verifier.py         Per-step LLM verification
├── tools/                  Built-in tools
│   ├── bash.py             Shell execution
│   ├── read_file.py        File reading
│   ├── write_file.py       File writing
│   └── web_search.py       Web search
├── config.yaml             Default config
└── docs/                   GitHub Pages documentation

~/.baw/                     ← User data directory
├── config.yaml             User config
├── SOUL.md                 Soul / behaviour rules
├── .env                    API keys
├── memory/store.jsonl      Memory storage
├── skills/*.yaml           Custom skills
└── tasks/                  Background task output
</pre>

<h3>🔧 Configuration</h3>

<pre>
# Interactive setup wizard
baw --setup

# Real-time Config CLI
baw --cfg list                    # Show all settings
baw --cfg get model.default       # Query a setting
baw --cfg set mode hybrid         # Change immediately
baw --cfg set tone.default business
baw --cfg set adversarial.enabled false  # Disable court

# Or edit YAML directly
vim ~/.baw/config.yaml
</pre>

<hr>

<p align="center">
  <a href="https://github.com/cornreform/baw-agent-platform">GitHub</a> •
  <a href="https://cornreform.github.io/baw-agent-platform/">Documentation</a> •
  <a href="BAW-PLAN.html">Design Doc</a>
</p>

<p align="center"><sub>Built from scratch — no framework, no vendor lock-in, no surrender.</sub></p>
