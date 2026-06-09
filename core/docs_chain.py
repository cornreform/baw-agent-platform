"""
BAW — Docs Chain Reader

Implements the "docs chain" pattern: before editing any file,
the agent reads root → parent dir → file-level documentation.

Inspired by Agent Zero / Space Agent's agents.md pattern.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional


def find_docs_chain(file_path: str, project_root: str | None = None) -> list[Path]:
    """Find the doc chain for a given file path.

    Returns [root_doc, dir_doc, file_doc] (filtered to existing files).
    """
    path = Path(file_path).resolve()
    root = Path(project_root).resolve() if project_root else Path.cwd()
    # If file is outside project root, use file's parent tree
    if not str(path).startswith(str(root)):
        # Try common project roots
        for trial in [Path.home() / "baw", Path.home() / ".baw", Path.cwd()]:
            if str(path).startswith(str(trial)):
                root = trial
                break

    chain: list[Path] = []

    # 1. Root doc
    for name in ["docs/README.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"]:
        root_doc = root / name
        if root_doc.exists():
            chain.append(root_doc)
            break

    # 2. Directory docs (walk up from file to root)
    current = path.parent
    dir_docs: list[Path] = []
    while str(current) >= str(root):
        # Check for README.md in the directory itself
        for name in ["README.md", "AGENTS.md", "docs.md"]:
            dir_doc = current / name
            if dir_doc.exists() and dir_doc not in chain:
                dir_docs.append(dir_doc)
                break
        # Also check docs/<dirname>/README.md (structured docs folder)
        try:
            rel = current.relative_to(root)
        except ValueError:
            rel = current.name
        docs_subdir = root / "docs" / rel / "README.md"
        if docs_subdir.exists() and docs_subdir not in chain and docs_subdir not in dir_docs:
            dir_docs.append(docs_subdir)
        if current == root:
            break
        current = current.parent
    # Reverse so closest-to-root comes first
    chain.extend(reversed(dir_docs))

    # 3. File-level doc (sibling markdown)
    file_stem = path.stem
    for suffix in [".md", ".docs.md"]:
        file_doc = path.parent / f"{file_stem}{suffix}"
        if file_doc.exists() and file_doc not in chain:
            chain.append(file_doc)
            break

    return chain


def read_docs_chain(file_path: str, project_root: str | None = None) -> str:
    """Read the full docs chain for a file and return as context string.

    Returns empty string if no docs found.
    """
    chain = find_docs_chain(file_path, project_root)
    if not chain:
        return ""

    parts = []
    for doc in chain:
        try:
            content = doc.read_text()
            # Determine label
            if doc.name in ("AGENTS.md", "CLAUDE.md", ".cursorrules"):
                label = f"Project Root ({doc.name})"
            elif doc.parent == Path(file_path).resolve().parent:
                label = f"File-level ({doc.relative_to(doc.parent.parent.parent) if len(doc.parents) > 2 else doc.name})"
            else:
                label = f"Directory ({doc.relative_to(doc.parents[2]) if len(doc.parents) > 2 else doc.parent.name}/{doc.name})"
            parts.append(f"## {label}\n\n{content}")
        except Exception:
            continue

    if not parts:
        return ""

    return (
        "--- DOCS CHAIN ---\n"
        "Relevant project documentation (read before editing):\n\n"
        + "\n\n---\n\n".join(parts)
        + "\n\n--- END DOCS CHAIN ---"
    )


def inject_docs_context(messages: list[dict], file_path: str,
                        project_root: str | None = None) -> list[dict]:
    """Inject docs chain context into messages before an edit operation.

    Returns modified messages list with docs context appended as system message.
    """
    docs_text = read_docs_chain(file_path, project_root)
    if not docs_text:
        return messages

    # Add as system message before the last user message
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            original_user = messages[i]
            messages[i] = {
                "role": "user",
                "content": docs_text + "\n\n---\n\n" + original_user.get("content", "")
            }
            break

    return messages
