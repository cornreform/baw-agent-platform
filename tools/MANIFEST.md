# BAW Tools — Manifest

Inventory of every file in `tools/` as of 2026-06-12. Two categories:
**registered** (wired up at boot) and **imported-on-demand** (used by specific
subsystems like `court` or `loop`).

## Registered at boot

Loaded by `tools/__init__.py::register_all()`, which is called by
`core/commands.py` (4 entry points) and `bench_3mode.py`.

- `bash` — shell command execution. Medium risk.
- `read_file` — paginated file read. Low risk.
- `write_file` — overwrite file (creates parents). Medium risk.
- `web_search` — multi-provider web search. Low risk.
- `image_generate` — DALL-E image generation. Low risk.
- `tts` — multi-provider text-to-speech (MiniMax / Stepfun / Edge). Low risk.
- `todo` — persistent task / thought / follow-up list across sessions. Low risk.

## Imported on demand

Not in `register_all()` — imported directly by their consumer modules.

- `delegate_task` — imported by `core/court.py` (3 sites) and
  referenced by `core/loop.py`. Spawns a sub-agent via MiniMax executor.
- `vision` — image understanding via `mmx` CLI. Imported by image-flow code.
- `memory` — persistent memory read/write/search. Imported by memory subsystem.
- `search_files` — ripgrep-backed content + file search. Imported by file subsystem.
- `patch` — fuzzy find-and-replace edits. Imported by edit flow.
- `todo` — task list management. Imported by task manager.
- `web_extract` — page text extraction (httpx + BeautifulSoup). Imported by web flow.

## Stubs (not yet implemented)

- `browser` — marked "stub" in docstring. Future web-automation.
- `execute_code` — marked "stub" in docstring. Future Python sandbox.

## Adding a new tool

1. Create `tools/<name>.py` with a `TOOL_DEF = {name, description, handler, parameters, risk_level}`.
2. To register at boot: add `from . import <name>` and `register(**<name>.TOOL_DEF)` in
   `tools/__init__.py::register_all()`.
3. To use on-demand only: just `import` it where needed; no registry entry required.
4. Update this manifest when adding/moving tools.

## Tool count over time

- 2026-06-12 — Registered: 6 / On-demand: 7 / Stubs: 2 / Total: 15
- 2026-06-12 (todo system) — Registered: 7 / On-demand: 7 / Stubs: 2 / Total: 16
