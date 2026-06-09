# Messaging Connectors Docs

> **Read before editing `core/messaging/`.**

## Connector Map

```
__init__.py     ← Base connector + message router
telegram.py     ← Telegram Bot API (primary)
discord.py      ← Discord Bot
matrix.py       ← Matrix protocol
signal.py       ← Signal messenger
whatsapp.py     ← WhatsApp Business API
```

## Message Flow

```
User message → Platform connector → __init__.py router
    → handle_slash() or run_agent()
    → Response → Platform connector → User
```

## Adding a New Platform

1. Create `core/messaging/new_platform.py`
2. Implement connector class with:
   - `start()`: Begin listening
   - `stop()`: Clean shutdown
   - `send(chat_id, text)`: Send message
   - `_poll()`: Message polling loop
3. Register in `baw-bot` entry point

## Key Conventions

- **Progress callbacks**: `_on_progress(step_type, name, args)` for real-time updates
- **Telegram is primary**: New features land here first, then port
- **Message queue**: 3 parallel slots + queue on full
- **Timeout**: 180s idle, 1800s max per run

## Edit Rules

- Don't break the `_on_progress` callback signature
- Keep platform-specific code isolated in its own file
- Test new platform with at least 3 messages before merge
