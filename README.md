# BAW (Black And White) — Agent Platform

**Autonomous AI agent that thinks independently, challenges you, then executes relentlessly.**

BAW runs in a Docker container on ARM64 Linux (Dragon Q6A). Named after Sunny's two black-and-white dogs — loyal, companionable, and always learning.

## Quick Start

```bash
# Enter interactive chat
baw

# Full CLI reference
baw --help
```

## CLI Commands

### 💬 Interact
| Command | Description |
|---|---|
| `baw` / `baw chat` | Open interactive chat REPL with streaming AI responses |

### ⚡ Monitor
| Command | Description |
|---|---|
| `baw status` / `baw st` | System health overview — uptime, sessions, memory, connectors |
| `baw models` | AI model catalogue with context windows + capability routing matrix |
| `baw memory` / `baw mem` | Persistent memory store statistics and score distribution |
| `baw sessions` / `baw sess` | Browse past chat session transcripts |
| `baw logs` / `baw log` | Live Docker log tail (`docker logs -f`) |
| `baw dashboard` / `baw dash` | Full-screen Textual TUI with 6 live-updating panels |

### 🔧 Manage
| Command | Description |
|---|---|
| `baw config [show¦edit¦get <k>¦set <k> <v>]` | View or modify configuration |
| `baw soul [show¦edit]` | Read or edit BAW's core identity (SOUL.md) |
| `baw skill [list¦install <n>¦remove <n>]` | Manage autonomous skill definitions |

### ⚙️ System
| Command | Description |
|---|---|
| `baw restart` | Gracefully restart the Docker container |

## In-Chat Slash Commands

| Command | Action |
|---|---|
| `/help` | Show in-chat help |
| `/model [name]` | Switch active AI model |
| `/soul` | View SOUL.md |
| `/config` | View current config |
| `/session` | Show session info |
| `/clear` | Reset chat history |
| `/exit` | Quit chat |

## Architecture

```
~/baw/                          # Source code (GitHub: cornreform/baw-agent-platform)
├── cli/                        # Purple+Gold CLI (Rich + Textual)
│   ├── main.py                 # Entry point + --help
│   └── commands/               # Subcommand modules
├── core/                       # Agent loop, LLM, memory, tools
├── bin/baw                     # Shell wrapper → symlinked to ~/.local/bin/
├── Dockerfile                  # python:3.11-slim, self-contained
├── docker-compose.yml          # Volume mount ~/.baw/ for data persistence
└── requirements.txt            # All Python deps (no Hermes dependency)

~/.baw/                         # Persistent data (Docker volume)
├── config.yaml                 # Models, providers, tone, routing
├── .env                        # API keys (masked)
├── telegram.env                # Telegram bot token (BAW only — not Sticky's)
├── SOUL.md                     # Identity and behavioural rules
├── sessions/                   # Chat transcripts (JSONL)
├── memory/                     # Persistent memory store
├── skills/                     # Autonomous task definitions (YAML)
├── tasks/                      # Task queue
└── logs/                       # Agent logs
```

## Key Files

| File | Purpose |
|---|---|
| `~/.baw/config.yaml` | Models, providers, capability routing, tone, fact-check, adversarial |
| `~/.baw/.env` | API keys — DeepSeek, MiniMax, Kimi, OpenAI, etc. |
| `~/.baw/SOUL.md` | BAW's identity, core philosophy, hard gates, communication rules |
| `~/.baw/telegram.env` | BAW's Telegram bot token (isolated from Sticky/Hermes) |

## Deployment

```bash
cd ~/baw
docker compose up -d          # Start BAW container
baw chat                       # Enter chat
baw dashboard                  # Live TUI
```

BAW runs as `baw-docker.service` (systemd), auto-starts on boot.

## Identity

- **Name:** BAW (Black And White)
- **Colors:** Purple (`magenta`) + Gold (`yellow`)
- **Language:** Traditional Chinese (繁體中文)
- **Personality:** Independent thinker, challenges user, executes relentlessly
- **Dual Soul:** Angel 😇 (supporter) + Devil 👿 (critic) — simultaneous independent analysis
- **Court Phase:** Devil + Angel analyze independently → debate with user → reach consensus
- **Execution Phase:** Plan → Step → Verify → Recover — no re-opening court

## Isolation

BAW is fully isolated from Hermes/Sticky:
- Own Docker container (separate process, network, filesystem)
- Own Python environment (`python:3.11-slim`)
- Own Telegram bot token (`BAW_TELEGRAM_TOKEN` in `~/.baw/telegram.env`)
- Zero Hermes path references in codebase
- No shared venv — no cross-contamination of credentials
