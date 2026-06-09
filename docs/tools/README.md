# Built-in Tools Docs

> **Read before editing `tools/` or adding new tools.**

## Tool List

| Tool | File | Risk Level | Description |
|------|------|------------|-------------|
| `read_file` | `tools/read_file.py` | low | Read file contents |
| `write_file` | `tools/write_file.py` | medium | Write/overwrite files |
| `bash` | `tools/bash.py` | medium | Shell command execution |
| `web_search` | `tools/web_search.py` | low | Web search via DuckDuckGo |

## Adding a New Tool

1. Create `tools/my_tool.py`
2. Implement `register_tool()` with:
   - `name`: tool identifier
   - `description`: what it does
   - `parameters`: JSON Schema for arguments
   - `handler`: the actual function
3. Add to `permissions.risk_levels` in config.yaml
4. Optionally add degradation chain in `core/degradation.py`

## Degradation Chains

Each tool has automatic fallbacks on failure:

| Tool | Chain |
|------|-------|
| `bash` | Double timeout → parent dir → /tmp |
| `write_file` | Parent dir → /tmp → alt path |
| `web_search` | Simplify query → different provider |

## Edit Rules

- New tools must be self-contained in `tools/`
- Register via `register_tool()` — never add to loop.py directly
- Always add permission level
