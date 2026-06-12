# BAW Config — Two Sources

**TL;DR**: `~/.baw/config.yaml` is the **active runtime config**. The repo
`/home/radxa/baw/config.yaml` is an **abandoned draft** kept only for diff reference.
On 2026-06-12 it was decided NOT to sync them — the runtime config is the source
of truth.

## Why two files exist

BAW follows a "config lives with the data" convention: runtime state goes to
`~/.baw/` (the `BAW_HOME` directory), and the repo holds the source code +
tooling. When `baw` boots, it loads `~/.baw/config.yaml` if present; otherwise
it falls back to a built-in default.

A repo-level `config.yaml` was once used to ship a working sample alongside
the code. That sample has drifted from the real runtime config since it was
last edited on 2026-06-12 02:19. Key differences:

| Concern | Repo draft | Active runtime |
|---|---|---|
| Default model | `deepseek-v4-flash` | `step-3.7-flash` |
| Fallback | `deepseek-reasoner` | `MiniMax-M3` |
| TTS provider | `MiniMax-M3` (no config block) | `stepaudio-2.5-tts` + stepfun |
| TTS voice | none | `Cantonese_GentleLady` |
| ASR | `stepfun-asr` (method) | `auto-asr` (method) |
| Adversarial | default model both | `angel=step-3.7-flash`, `devil=kimi-k2.6` |
| max_concurrency | absent | `10` |
| max_tokens | absent | `default=8192`, `reasoning=16384` |
| mode | absent | `hybrid` |
| model.route | absent | enabled, threshold 8000 |
| Providers | deepseek, MiniMax | + kimi, agnes, stepfun, openai, Azure |
| `dreaming`, `self_repair`, `on_hold_tasks` | absent | present |

The active config is ~50% larger and has 16 top-level sections vs. 8 in the draft.

## The sample file

For a fresh install, copy `config.sample.yaml` (regenerated 2026-06-12 from the
active config, with secret values redacted) to `~/.baw/config.yaml` and tweak.

The repo-level `config.yaml` is now **legacy**. Do not edit it. If you need
schema examples, look at `config.sample.yaml` instead.

## Removing the legacy file

Out of scope for the 2026-06-12 cleanup. Leaving in place so anyone diffing
historical issues (e.g. BAW_SYSTEM_AUDIT_P4_v2) can still see what the draft
used to claim.

## Diff summary (drift direction)

The active runtime config has **more** sections than the repo draft:

- New in runtime: `max_concurrency`, `max_tokens`, `mode`, `model.route`,
  `tools` (browser/execute_code/image_generate/tts enable flags),
  `tts` (legacy Azure-era block, retained for back-compat),
  `dreaming`, `self_repair`, `on_hold_tasks`.
- Lost from draft (i.e. runtime replaced it): just `task_rules` — runtime has
  its own `task_rules` AND a `model.route` long/short split, so the routing
  logic is duplicated. Future cleanup: pick one.
