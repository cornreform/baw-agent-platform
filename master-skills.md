# Master Skills — Routing

## Triggers
- `/` commands → route to specific handler
- "記低" / "remember" → `remember` skill
- "search memory" / "搜尋" → `memory-search` skill
- chat / questions → direct LLM (no tools needed)
- system ops (git, docker, file) → `run_baw` with tools

## Sub-skill loading
Load sub-skills on demand:
- `authorization` — what BAW can/cannot do
- `hard-gates` — accuracy rules
- `model-routing` — which model for which task
- `delegation` — multi-step plan execution
