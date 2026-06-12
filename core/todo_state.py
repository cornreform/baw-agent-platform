"""BAW — Persistent Todo / Thought / Follow-up Store

Three item types:
  - task:     standard checklist item (pending / in_progress / completed / cancelled)
  - thought:  BAW's self-reflection or idea (always visible, no "done" — captured,
              not closed)
  - followup: action that must be taken in a FUTURE turn or session. Persists across
              sessions and is surfaced at boot.

Persistence:
  Per-session:    ~/.baw/todos/<session_id>.json
  Follow-ups are also written to ~/.baw/todos/_followups.jsonl (append-only) so
  they survive even if the originating session file is pruned.

Used by:
  - tools/todo.py        (LLM-callable)
  - core/loop.py         (auto-inject at boot)
  - cli/commands/todo.py (human CLI)
"""
from __future__ import annotations

import json
import time
import uuid
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Literal
from datetime import datetime

ItemType = Literal["task", "thought", "followup"]
ItemStatus = Literal["pending", "in_progress", "completed", "cancelled"]

_FOLLOWUP_LOG = "_followups.jsonl"
_session_lock = threading.Lock()


@dataclass
class TodoItem:
    id: str
    content: str
    type: ItemType = "task"
    status: ItemStatus = "pending"
    created_at: str = ""
    updated_at: str = ""
    session_id: str = ""
    note: str = ""           # optional: BAW's reason / context
    parent_id: str = ""      # optional: link to a thought that spawned a followup

    def __post_init__(self):
        if not self.id:
            self.id = f"t-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
        now = datetime.now().isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)


class TodoState:
    """Persistent per-session todo store. Thread-safe."""

    def __init__(self, data_dir: Path | str | None = None, session_id: str = "default"):
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".baw"
        self.todos_dir = self.data_dir / "todos"
        self.todos_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.path = self.todos_dir / f"{session_id}.json"
        self._items: list[TodoItem] = []
        self._load()

    # ── I/O ────────────────────────────────────────────────────

    def _load(self):
        with _session_lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self._items = [TodoItem(**it) for it in raw]
                except (json.JSONDecodeError, TypeError):
                    self._items = []
            else:
                self._items = []

    def _save(self):
        with _session_lock:
            self.path.write_text(
                json.dumps([it.to_dict() for it in self._items], ensure_ascii=False, indent=1),
                encoding="utf-8",
            )

    def _touch(self, it: TodoItem):
        it.updated_at = datetime.now().isoformat(timespec="seconds")
        if it.type == "followup":
            self._append_followup_log(it)

    def _append_followup_log(self, it: TodoItem):
        """Mirror follow-ups to an append-only cross-session log."""
        log = self.todos_dir / _FOLLOWUP_LOG
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")

    # ── CRUD ───────────────────────────────────────────────────

    def add(self, content: str, type: ItemType = "task", note: str = "",
            parent_id: str = "") -> TodoItem:
        it = TodoItem(
            id="",
            content=content,
            type=type,
            session_id=self.session_id,
            note=note,
            parent_id=parent_id,
        )
        self._items.append(it)
        self._touch(it)
        self._save()
        return it

    def update(self, item_id: str, **fields) -> Optional[TodoItem]:
        for it in self._items:
            if it.id == item_id:
                for k, v in fields.items():
                    if hasattr(it, k) and k != "id":
                        setattr(it, k, v)
                self._touch(it)
                self._save()
                return it
        return None

    def complete(self, item_id: str) -> Optional[TodoItem]:
        return self.update(item_id, status="completed")

    def cancel(self, item_id: str) -> Optional[TodoItem]:
        return self.update(item_id, status="cancelled")

    def start(self, item_id: str) -> Optional[TodoItem]:
        return self.update(item_id, status="in_progress")

    def remove(self, item_id: str) -> bool:
        before = len(self._items)
        self._items = [it for it in self._items if it.id != item_id]
        if len(self._items) < before:
            self._save()
            return True
        return False

    def get(self, item_id: str) -> Optional[TodoItem]:
        for it in self._items:
            if it.id == item_id:
                return it
        return None

    # ── Queries ────────────────────────────────────────────────

    def list(self, active_only: bool = False, type: Optional[ItemType] = None) -> list[TodoItem]:
        items = self._items
        if active_only:
            items = [it for it in items if it.status in ("pending", "in_progress")]
        if type:
            items = [it for it in items if it.type == type]
        return sorted(items, key=lambda it: (it.status != "in_progress", it.created_at))

    def stats(self) -> dict:
        out = {"task": {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0},
               "thought": {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0},
               "followup": {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}}
        for it in self._items:
            if it.type in out and it.status in out[it.type]:
                out[it.type][it.status] += 1
        return out

    # ── Cross-session follow-up loader ────────────────────────

    def load_pending_followups(self) -> list[TodoItem]:
        """Read all followups still marked pending across all session files.

        Returns TodoItem instances (not bound to self._items). Useful at boot
        to surface work carried over from a previous session.
        """
        out: list[TodoItem] = []
        for f in self.todos_dir.glob("*.json"):
            if f.name == _FOLLOWUP_LOG:
                continue
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                for it in raw:
                    if it.get("type") == "followup" and it.get("status") in ("pending", "in_progress"):
                        out.append(TodoItem(**it))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return sorted(out, key=lambda it: it.created_at)


# ── Module-level helpers ──────────────────────────────────────

def get_default_session_id() -> str:
    """Derive a session id from timestamp + simple hash of recent activity."""
    from datetime import datetime
    return f"ses-{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def get_state(session_id: Optional[str] = None, data_dir: Optional[Path] = None) -> TodoState:
    return TodoState(data_dir=data_dir, session_id=session_id or "default")
