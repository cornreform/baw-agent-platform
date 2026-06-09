# Core Module Docs

> **Read before editing any file in `core/`.**

## Module Map

```
llm.py          ← LLM abstraction (protocol-agnostic, retry, routing)
loop.py         ← Main agent loop (court → plan → execute → report)
adversarial.py  ← Angel/Devil court (v2: parallel independent)
tools.py        ← Tool registry
permission.py   ← 3-tier permission engine (high/medium/low)
memory.py       ← JSONL memory store
context.py      ← Context manager (message history)
commands.py     ← Slash commands (60s TTL cache)
display.py      ← Step display formatter
tone.py         ← 6 tone profiles
scheduler.py    ← Cron daemon
skills.py       ← YAML skill system
search.py       ← Open search provider registry
fact_checker.py ← Fact checking
verifier.py     ← Per-step LLM verify
degradation.py  ← Tool degradation chains
checkpoint.py   ← Checkpoint / rollback
file_history.py ← File versioning (SHA256)
autosave.py     ← Auto git commit
render.py       ← HTML renderers
learn.py        ← Self-learning
dream.py        ← Weekly self-curation
board.py        ← HTML dashboard
github.py       ← GitHub integration
task_manager.py ← Async task manager
setup.py        ← Setup wizard + Config CLI
evolve.py       ← Behavior evolution
```

## Dependencies

- `loop.py` imports: llm, tools, permission, memory, context, checkpoint, file_history, autosave, display, render, adversarial
- `adversarial.py` imports: llm
- `commands.py` imports: memory, llm, dream, search
- Everything depends on `llm.py` for LLM calls

## Edit Rules

- **Never bypass `call_llm_with_fallback()`** — use it for all LLM calls
- **Add retry**: Transient errors auto-retry via exponential backoff in llm.py
- **Add routing**: model.route config controls short/long query routing
- **Permission**: New tools must register in `permission.py`
- **Cache**: Static commands use `_cached()` in commands.py
