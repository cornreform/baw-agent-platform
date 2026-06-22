# YAGNI Decision Ladder / Ponytail Pattern

> Inspired by [Ponytail](https://github.com/DietrichGebert/ponytail) (DietrichGebert, 47.5K ⭐) — makes AI agents think like the laziest senior dev.

## The Ladder

Before writing **any** code, climb this ladder. **Stop at the first rung that holds.**

```
1. Does this code need to exist?          → NO: skip it (YAGNI)
2. Can stdlib / built-ins handle it?      → YES: use them
3. Is there a native platform feature?     → YES: use it
4. Is there already an installed dep?      → YES: use it
5. Can it be a one-liner?                 → YES: one line
6. Only then: write the minimum that works
```

**Lazy, not negligent.** Never skip:
- Trust-boundary validation (input sanitization)
- Data-loss handling (confirmation before destructive ops)
- Security (auth, injection prevention)
- Accessibility (for UI code)

## Deferred Decisions / Ponytail Comments

When you skip something (rung 1-5), leave a comment explaining why:

```python
# [YAGNI] Using stdlib pathlib instead of installing path.py — revisit if path ops grow complex
```

```html
<!-- [YAGNI] Browser native <dialog> does this — no modal library needed -->
```

This creates a **debt ledger** — when the need arises, you know exactly where to upgrade.

## Example: Modal Dialog (Ponytail vs Typical)

| Typical Agent | Ponytail Agent |
|---------------|----------------|
| Install Radix UI → wrapper → portal → overlay → trigger → focus management | `<dialog>` element — 8 lines, zero deps |

## Commands

| Command | What it does |
|---------|-------------|
| `review my code for over-engineering` | Scan recent changes, flag unnecessary complexity |
| `audit this repo for YAGNI violations` | Full repo scan for over-engineered patterns |
| `harvest deferred decisions` | Collect all `[YAGNI]` comments into a structured ledger |

## BAW Integration

BAW applies the ladder automatically in:
- `tool_generate` — code generation prompt (system prompt)
- `codebase_doc` — documentation generation
- All coding tasks via system prompt rule

## References

| Item | Link |
|------|------|
| Ponytail GitHub | https://github.com/DietrichGebert/ponytail |
| Ponytail video walkthrough | https://www.youtube.com/watch?v=2xuFcmUAQUc |
| YAGNI principle | https://en.wikipedia.org/wiki/You_aren%27t_gonna_need_it |
