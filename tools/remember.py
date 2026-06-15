"""BAW built-in: lightweight remember/recall tool for quick fact storage.

Simpler than the full memory system — optimized for fast note-taking
like "package X installed successfully" or "tried method Y, failed with Z".
Stored in ~/.baw/notes.jsonl
"""

import json
from datetime import datetime
from pathlib import Path


_NOTES_FILE = Path.home() / ".baw" / "notes.jsonl"


def _ensure_file():
    _NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _NOTES_FILE.exists():
        _NOTES_FILE.touch()


def remember_fact(fact: str, category: str = "general") -> str:
    """Save a quick fact/note.

    Args:
        fact: What to remember (keep concise — max 500 chars).
        category: Category for grouping (e.g., 'install', 'config', 'bug', 'discovery').

    Returns:
        Confirmation message.
    """
    fact = fact.strip()[:500]
    if not fact:
        return "Error: fact cannot be empty"

    _ensure_file()

    entry = {
        "timestamp": datetime.now().isoformat(),
        "category": category.strip() or "general",
        "fact": fact,
    }

    with open(_NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return f"✅ Noted [{entry['category']}]: {fact[:100]}"


def recall_facts(category: str = "", limit: int = 10) -> str:
    """Recall recent facts, optionally filtered by category.

    Args:
        category: Optional — only show facts from this category.
        limit: Max entries to return (default: 10).

    Returns:
        Formatted list of facts.
    """
    _ensure_file()

    entries = []
    try:
        with open(_NOTES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        return "No facts recorded yet."

    if category:
        entries = [e for e in entries if e.get("category") == category.strip()]

    if not entries:
        tag = f" in category '{category}'" if category else ""
        return f"No facts found{tag}."

    entries = entries[-limit:]  # most recent N
    lines = []
    for e in reversed(entries):
        ts = e.get("timestamp", "")[:19]
        cat = e.get("category", "general")
        fact = e.get("fact", "")[:200]
        lines.append(f"- [{cat}] {fact}  _(at {ts})_")

    header = "## Recent Facts"
    if category:
        header += f" (category: {category})"
    return header + "\n" + "\n".join(lines)


def forget_fact(index: int = -1) -> str:
    """Remove a fact by index (1-based, from most recent).

    Args:
        index: 1-based index from most recent. -1 = delete all.

    Returns:
        Result message.
    """
    _ensure_file()

    try:
        with open(_NOTES_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return "No facts to forget."

    if index == -1:
        _NOTES_FILE.write_text("")
        return f"🗑️ Forgot all {len(lines)} facts."

    # Find by index (1-based, newest first)
    lines = [l for l in lines if l.strip()]
    if index < 1 or index > len(lines):
        return f"Error: index {index} out of range (1-{len(lines)})"

    removed = lines.pop(-index)
    with open(_NOTES_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

    try:
        entry = json.loads(removed)
        return f"🗑️ Removed [{entry.get('category')}]: {entry.get('fact', '')[:80]}"
    except Exception:
        return "🗑️ Removed fact."


def _dispatcher(action: str, fact: str = "", category: str = "",
                limit: int = 10, index: int = -1) -> str:
    """Dispatch remember actions."""
    if action == "remember":
        return remember_fact(fact, category)
    elif action == "recall":
        return recall_facts(category, limit)
    elif action == "forget":
        return forget_fact(index)
    else:
        return f"Error: unknown action '{action}'. Use 'remember', 'recall', or 'forget'."


TOOL_DEF = {
    "name": "remember",
    "description": (
        "Lightweight fact storage — quickly save and recall short facts. "
        "Use 'action=remember' with a 'fact' string to save (e.g., "
        "'mmx-cli package name is correct, mmx — not @minimax/mcp'). "
        "Use 'action=recall' to retrieve recent facts. "
        "Use 'action=forget' to remove. "
        "Each fact is just a string — no scoring, no embedding, instant save."
    ),
    "handler": _dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["remember", "recall", "forget"],
                "description": "'remember' saves a fact, 'recall' retrieves, 'forget' removes.",
            },
            "fact": {
                "type": "string",
                "description": "The fact to save (required for 'remember'). Keep concise — max 500 chars.",
            },
            "category": {
                "type": "string",
                "description": "Category for grouping: 'install', 'config', 'bug', 'discovery', 'preference', or custom.",
                "default": "general",
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to return for 'recall' (default: 10).",
                "default": 10,
            },
            "index": {
                "type": "integer",
                "description": "For 'forget': 1-based index from most recent. -1 deletes all.",
                "default": -1,
            },
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
