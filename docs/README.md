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
| `baw` | CLI entry point | Small edits only; add commands in `commands.py` |
| `core/loop.py` | Main agent loop | Court → Plan → Execute → Report pipeline |
| `core/llm.py` | LLM abstraction | Add protocols via `register_protocol()` |
| `core/adversarial.py` | Angel/Devil court | v2: parallel independent analysis |
| `core/memory.py` | JSONL memory store | Append-only; scores decay over time |
| `core/commands.py` | Slash commands | 60s cache on static commands |

## Don't

- Don't add new top-level files without docs entry
- Don't bypass `call_llm_with_fallback()` for LLM calls
- Don't skip permission checks
- Don't remove cost tracking
