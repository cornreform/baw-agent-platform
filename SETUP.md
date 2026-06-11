# BAW — Quick Start (First-time Install)

## Prerequisites
- Python 3.11+
- Docker + Docker Compose
- API key from at least one provider (Stepfun recommended)

## Install

```bash
git clone https://github.com/cornreform/baw-agent-platform.git
cd baw-agent-platform
```

## Configure

```bash
# Run the setup wizard (interactive):
python3 baw --setup

# Or quick-configure with a single provider:
python3 baw --cfg set model.default step-3.7-flash
python3 baw --cfg set model.fallback deepseek-v4-flash
```

The setup wizard will walk you through:
1. **Telegram token** — optional, for bot mode
2. **Default model** — step-3.7-flash recommended
3. **API keys** — enter one or more provider keys
4. **Auto-configures** — providers, STT, TTS, vision based on your keys
5. **Behavior** — mode, tone, court, fact check

## Verify

```bash
baw --doctor          # Health check (config, deps, Docker, disk)
baw --version         # Version + build info
baw "hello"           # Test the agent
```

## Docker (for Telegram bot)

```bash
# Add your Telegram token:
echo "BAW_TELEGRAM_TOKEN=your_bot_token" >> ~/.baw/.env

# Start:
docker compose build baw-telegram
docker compose up -d baw-telegram

# Check:
docker ps --filter name=baw
```

## CLI Commands

| Command | Purpose |
|---------|---------|
| `baw <prompt>` | Run agent |
| `baw --doctor [--fix]` | Health check |
| `baw --setup` | Interactive setup wizard |
| `baw --update` | Pull latest + rebuild Docker |
| `baw --backup` | Backup all data |
| `baw --restore [path]` | Restore from backup |
| `baw --profile-*` | Profile management |
| `baw --diagnostics` | System debug info |
| `baw --logs [N]` | View Docker logs |
| `baw --cfg list/get/set` | Config management |
| `baw --cfg edit/check` | Edit/validate config |
| `baw --reset` | Factory reset |
| `baw --completion bash|zsh` | Shell completion |
| `baw --tools-list` | List tools |
| `baw --memory-stats` | Memory statistics |

## Need Help?

- `baw --help` — full CLI reference
- `baw --doctor` — run health check
- Telegram: `@BAWtestonlybot`
