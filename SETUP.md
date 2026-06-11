# BAW — First-time Install Quick Start

```bash
git clone https://github.com/cornreform/baw-agent-platform.git
cd baw-agent-platform

# Run setup wizard:
python3 baw --setup
```

During setup you'll be asked for:
1. **Region** (國際版 / 內地版) — determines API endpoint
2. **Telegram token** (optional)
3. **API keys**
4. Other preferences

After setup:
```bash
baw --doctor        # Verify everything works
baw "hello"         # Test the agent
```

For Telegram bot:
```bash
echo "BAW_TELEGRAM_TOKEN=*** >> ~/.baw/.env
docker compose build baw-telegram
docker compose up -d baw-telegram
```

Full CLI: `baw --help`
