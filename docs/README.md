# BAW — Project Root Docs

> **Read before ANY edit to this project.**
> Auto-loaded by BAW's doc-chain hook on write_file / bash modifications.

## Architecture

```
baw (CLI) → core/ (engine) → tools/ (built-ins)
                            → messaging/ (connectors)
```

## Conventions

- **No hardcoding**: All provider/model config lives in `~/.baw/config.yaml`
- **Protocol-agnostic**: LLM calls use `call_llm_with_fallback()`, never direct HTTP
- **Permission-first**: Every tool call checks `PermissionEngine` before execution
- **Cost tracking**: All LLM calls go through `record_cost()` in `core/loop.py`
- **Never ask user**: Errors auto-recover; only report after exhausting all strategies
- **English-first**: All docs, code comments, commit messages in English; Chinese secondary

## Key Files

| File | Purpose | Edit Rule |
|------|---------|-----------|
| `baw` | CLI entry point | All 35+ subcommands; add new ones after existing handlers |
| `core/loop.py` | Main agent loop | Court → Plan → Execute → Report pipeline |
| `core/llm.py` | LLM abstraction | Add protocols via `register_protocol()` |
| `core/adversarial.py` | Angel/Devil court | v2: parallel independent analysis |
| `core/memory.py` | JSONL memory store | Append-only; scores decay over time |
| `core/commands.py` | Slash commands | 60s cache on static commands |
| `core/setup.py` | Setup wizard + Config CLI | Interactive English wizard, plan-based endpoints |
| `core/doctor.py` | Health check (--doctor) | Validates config, deps, Docker, disk, API keys |
| `core/tribunal.py` | Multi-model consensus | Judges evaluate independently; Chief Justice unifies |
| `core/validator.py` | Real-world validation | Zero-mock: real API calls, file writes, execution |
| `core/test_runner.py` | Telegram test suite | /test /validate /tribunal bot commands |
| `core/watchdog.py` | Health monitor | Resource tracking, emergency cleanup |
| `core/update.py` | Self-update (--update) | Git pull + Docker build + restart |
| `core/backup.py` | Backup & restore | tar.gz of config/.env/memory/sessions |
| `core/profile.py` | Profile management | Isolated config/memory/sessions per profile |
| `core/diagnostics.py` | System diagnostics | Collect debug info for troubleshooting |

## Don't

- Don't add new top-level files without docs entry
- Don't bypass `call_llm_with_fallback()` for LLM calls
- Don't skip permission checks
- Don't remove cost tracking
