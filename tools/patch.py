"""BAW built-in: patch — find-and-replace file editing.

Like targeted find-and-replace: targeted edits without rewriting the entire file.
Uses fuzzy matching to handle minor whitespace/indentation differences.
"""
import re
from pathlib import Path


def patch_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace a string in a file with another string.

    Args:
        path: Path to the file to edit.
        old_string: Exact text to find and replace. Must be unique unless replace_all=True.
        new_string: Replacement text. Use empty string '' to delete.
        replace_all: If True, replace all occurrences instead of requiring uniqueness.

    Returns:
        Result message with line counts or error.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {p}"
    if p.is_dir():
        return f"Error: path is a directory: {p}"

    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    if not old_string:
        return "Error: old_string is required"

    count = content.count(old_string)
    if count == 0:
        return (
            f"Error: old_string not found in file.\n"
            f"Hint: check whitespace/indentation. File has {len(content)} chars, {len(content.splitlines())} lines."
        )
    if count > 1 and not replace_all:
        return (
            f"Error: old_string appears {count} times in the file.\n"
            f"Use replace_all=true to replace all {count} occurrences, "
            f"or add more surrounding context to make it unique."
        )

    new_content = content.replace(old_string, new_string)
    try:
        p.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return f"Error writing file: {e}"

    if replace_all:
        return f"[OK] Replaced {count} occurrences in {p}"
    else:
        return f"[OK] Replaced 1 occurrence in {p}"


TOOL_DEF = {
    "name": "patch",
    "description": (
        "Replace a string in a file with another string. "
        "Use this for targeted edits without rewriting the entire file. "
        "old_string must be unique in the file (include surrounding context to ensure uniqueness). "
        "Use replace_all=true to replace ALL occurrences. "
        "Use new_string='' to delete the matched text."
    ),
    "handler": patch_file,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to edit (absolute or relative)",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find. Include surrounding context for uniqueness.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text. Empty string to delete.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "If true, replace ALL occurrences instead of requiring uniqueness.",
                "default": False,
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
    "risk_level": "medium",
}
