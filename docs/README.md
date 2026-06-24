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

| Num | File | Purpose | Edit Rule | Applicable | How to check |
|-----|------|---------|-----------|------------|--------------|
| 01 | `baw` | CLI entry point | All 35+ subcommands; add new ones after existing handlers | All edits | Edit and test flags individually |
| 02 | `core/loop.py` | Main agent loop | Court → Plan → Execute → Report pipeline | All edits affecting agent flow | `python3 -m pytest tests/ -x` |
| 03 | `core/llm.py` | LLM abstraction | Add protocols via `register_protocol()` | When adding LLM providers | Smoke test with `baw --btw "ping"` |
| 04 | `core/adversarial.py` | Angel/Devil court | v2: parallel independent analysis | Court modifications | Wiretap log check |
| 05 | `core/memory.py` | JSONL memory store | Append-only; scores decay over time | Memory changes | Load .jsonl and check shapes |
| 06 | `core/commands.py` | Slash commands | 60s cache on static commands | Bot commands | /command test on bot |
| 07 | `core/setup.py` | Setup wizard + Config CLI | Interactive English wizard, plan-based endpoints | Setup/config changes | `baw --setup` dry run |
| 08 | `core/doctor.py` | Health check (--doctor) | Validates config, deps, Docker, disk, API keys | Health diagnostics | `baw --doctor --fix` |
| 09 | `core/tribunal.py` | Multi-model consensus | Judges evaluate independently; Chief Justice unifies | Consensus engine | `baw --tribunal "test"` |
| 10 | `core/validator.py` | Real-world validation | Zero-mock: real API calls, file writes, execution | Validation changes | --validate subcommands |
| 11 | `core/test_runner.py` | Telegram test suite | /test /validate /tribunal bot commands | Test infrastructure | Bot test commands |
| 12 | `core/watchdog.py` | Health monitor | Resource tracking, emergency cleanup | Monitoring | Set env dummy thresholds |
| 13 | `core/update.py` | Self-update (--update) | Git pull + Docker build + restart | Update mechanism | --update dry run |
| 14 | `core/backup.py` | Backup & restore | tar.gz of config/.env/memory/sessions | Backup changes | --backup / --restore |
| 15 | `core/profile.py` | Profile management | Isolated config/memory/sessions per profile | Profile functionality | --profile-* commands |
| 16 | `core/diagnostics.py` | System diagnostics | Collect debug info for troubleshooting | Diagnostics | --diagnostics |
| 17 | `core/tools.py` | Tool registry + auto-heal | Tool execution with ModuleNotFoundError auto-install | Tool infra | Tool call → missing dep → auto pip install |
| 18 | `core/tone.py` | Tone profiles | 6 profiles: casual, business, teaching, client-doc, ot-rt, stepwise | Tone system | --tone <profile> test |

## Don't

- Don't add new top-level files without docs entry
- Don't bypass `call_llm_with_fallback()` for LLM calls
- Don't skip permission checks
- Don't remove cost tracking
- **Never call `logOut()` API** — permanently invalidates bot tokens irreversibly
