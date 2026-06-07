"""
BAW — File History & Version Tracker
Timestamp-based file audit trail, independent of git.

Every file write/create operation is logged with:
  - ISO timestamp
  - File path
  - Action (create/update/delete)
  - Content hash (SHA256 of first 1KB for quick diff)
  - Previous version backup (stored in ~/.baw/history/)

BAW never overwrites files silently — always keeps a version trail.
"""

from __future__ import annotations
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone


class FileHistory:
    """Track file versions with timestamps and backups."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or Path.home() / ".baw"
        self.history_dir = self.data_dir / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.history_dir / "manifest.jsonl"
        self._cache: list[dict] = []
        self._load_manifest()

    def _load_manifest(self):
        """Load existing manifest entries."""
        self._cache = []
        if self._manifest_path.exists():
            for line in self._manifest_path.read_text().strip().split("\n"):
                if line.strip():
                    try:
                        self._cache.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    def _save_entry(self, entry: dict):
        """Append one entry to the manifest."""
        with open(self._manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._cache.append(entry)

    def _content_hash(self, content: str) -> str:
        """SHA256 of content (first 1KB for quick compare)."""
        return hashlib.sha256(content[:1024].encode()).hexdigest()[:16]

    def _timestamp(self) -> str:
        """ISO 8601 timestamp with timezone."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def record_write(self, path: Path | str, content: str,
                     action: str = "update", metadata: dict = None):
        """Record a file write with backup of previous version.

        Args:
            path: Absolute or relative file path
            content: New content being written
            action: "create" | "update" | "delete"
            metadata: Optional extra info (e.g. {"source": "fact_checker"})
        """
        p = Path(path).expanduser().resolve()
        ts = self._timestamp()

        # Backup previous version if it exists
        backup_path = None
        if p.exists() and action != "create":
            rel = str(p.relative_to(p.anchor) if p.is_absolute() else p)
            safe_name = f"{ts}_{rel}".replace("/", "_").replace("\\", "_")
            backup_path = self.history_dir / safe_name
            try:
                shutil.copy2(p, backup_path)
            except Exception:
                backup_path = None

        entry = {
            "timestamp": ts,
            "path": str(p),
            "action": action,
            "hash": self._content_hash(content),
            "size_bytes": len(content.encode()),
            "backup": str(backup_path) if backup_path else None,
            "metadata": metadata or {},
        }
        self._save_entry(entry)
        return entry

    def record_delete(self, path: Path | str, metadata: dict = None):
        """Record a file deletion."""
        p = Path(path).expanduser().resolve()
        ts = self._timestamp()

        # Backup before deleting
        backup_path = None
        if p.exists():
            safe_name = f"{ts}_DELETED_{p.name}"
            backup_path = self.history_dir / safe_name
            try:
                shutil.copy2(p, backup_path)
            except Exception:
                backup_path = None

        entry = {
            "timestamp": ts,
            "path": str(p),
            "action": "delete",
            "hash": None,
            "size_bytes": 0,
            "backup": str(backup_path) if backup_path else None,
            "metadata": metadata or {},
        }
        self._save_entry(entry)
        return entry

    def get_history(self, path: Path | str, limit: int = 20) -> list[dict]:
        """Get version history for a specific file, newest first."""
        p = str(Path(path).expanduser().resolve())
        entries = [e for e in self._cache if e["path"] == p]
        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        return entries[:limit]

    def get_timeline(self, since: str = "", limit: int = 50) -> list[dict]:
        """Get global timeline of all file changes.

        Args:
            since: ISO timestamp, e.g. "2026-06-07T00:00:00Z"
            limit: Max entries
        """
        entries = self._cache
        if since:
            entries = [e for e in entries if e["timestamp"] >= since]
        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        return entries[:limit]

    def latest_version(self, path: Path | str) -> Optional[dict]:
        """Get the latest version entry for a file."""
        history = self.get_history(path, limit=1)
        return history[0] if history else None

    def stats(self) -> dict:
        """Get file history statistics."""
        total = len(self._cache)
        files = len(set(e["path"] for e in self._cache))
        by_action = {}
        for e in self._cache:
            by_action[e["action"]] = by_action.get(e["action"], 0) + 1
        return {
            "total_entries": total,
            "unique_files": files,
            "by_action": by_action,
        }

    def to_html(self, limit: int = 20) -> str:
        """Render recent history as HTML."""
        entries = self.get_timeline(limit=limit)
        if not entries:
            return "<p><i>No file history yet.</i></p>"

        rows = []
        for e in entries:
            action = e["action"]
            action_class = {
                "create": "create",
                "update": "update",
                "delete": "delete",
            }.get(action, "")
            ts = e["timestamp"][:19].replace("T", " ")
            rows.append(
                f'<tr class="{action_class}">'
                f'<td class="ts">{ts}</td>'
                f'<td class="action">{action}</td>'
                f'<td class="path"><code>{e["path"]}</code></td>'
                f'<td class="size">{e["size_bytes"]}B</td>'
                f'<td class="hash">{e["hash"] or "-"}</td>'
                f'</tr>'
            )

        html = f"""<table class="file-history">
<thead><tr>
<th>Timestamp</th><th>Action</th><th>Path</th><th>Size</th><th>Hash</th>
</tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>"""
        return html
