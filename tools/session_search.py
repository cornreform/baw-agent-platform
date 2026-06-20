"""
BAW built-in: session_search — FTS5 cross-session search.

Search past conversations by keywords, roles, or content.
Uses SQLite FTS5 (stdlib, zero external deps).

M3 review consensus:
- mtime-based dedup tracking (session_fts_meta table)
- Thread lock for concurrent safety
- Incremental indexing (only changed files)
"""

import json
import sqlite3
import time
import threading
from pathlib import Path


_DB_LOCK = threading.Lock()


def _get_db(data_dir: Path) -> sqlite3.Connection:
    """Get or create FTS5 database connection for session search."""
    db_path = data_dir / "sessions" / ".session_fts.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    # Session message table (dedup key: session_id + timestamp)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_fts(
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL
        )
    """)
    # FTS5 virtual table for full-text search
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS session_fts_v USING fts5(
            session_id, role, content, timestamp
        )
    """)
    # Index tracking: which files have been indexed, at what mtime
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_fts_meta(
            filename TEXT PRIMARY KEY,
            indexed_mtime REAL
        )
    """)
    # Index on session_id for fast DELETE + SELECT
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fts_sid ON session_fts(session_id)")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    return conn


def _index_session(conn: sqlite3.Connection, path: Path, meta: dict) -> bool:
    """Index a single session file. Returns True if new data was indexed.

    Uses mtime tracking to avoid re-indexing unchanged files.
    """
    try:
        mtime = path.stat().st_mtime
        last = meta.get(path.name)
        if last is not None and mtime <= last:
            return False  # Already indexed, unchanged
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, IOError, OSError) as e:
        return False

    sid = path.stem  # filename without .json = session ID

    # Remove old entries for this session (session may have been updated)
    conn.execute("DELETE FROM session_fts WHERE session_id = ?", (sid,))

    messages = data.get("messages", [])
    if not messages:
        conn.execute(
            "INSERT OR REPLACE INTO session_fts_meta VALUES (?, ?)",
            (path.name, mtime),
        )
        conn.commit()
        return False

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        ts = msg.get("timestamp", 0)
        if not content:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO session_fts VALUES (?, ?, ?, ?)",
            (sid, role, content, ts),
        )

    conn.execute(
        "INSERT OR REPLACE INTO session_fts_meta VALUES (?, ?)",
        (path.name, mtime),
    )
    conn.commit()
    return True


def session_search(query: str, limit: int = 5, data_dir: str | None = None) -> str:
    """Search past sessions using FTS5 full-text search.

    Args:
        query: FTS5 query string (keywords, phrases in FTS5 syntax).
        limit: Max results to return (default 5, max 20).
        data_dir: BAW data directory (default: ~/.baw).

    Returns:
        Formatted search results with session IDs and snippets.
    """
    base = Path(data_dir).expanduser() if data_dir else Path.home() / ".baw"
    sessions_dir = base / "sessions"
    if not sessions_dir.exists():
        return "[session_search] No sessions directory found."

    with _DB_LOCK:
        conn = _get_db(base)

        # ── Load meta table into dict for fast lookup ──
        meta = {}
        for row in conn.execute("SELECT filename, indexed_mtime FROM session_fts_meta"):
            meta[row[0]] = row[1]

        # ── Index any unindexed or changed session files ──
        session_files = sorted(
            sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        indexed_count = 0
        for f in session_files[:100]:  # Max 100 files per search
            if _index_session(conn, f, meta):
                indexed_count += 1

        # Sync FTS5 virtual table with base table (full re-sync for FTS5)
        # FTS5 doesn't support UPDATE/DELETE on content tables easily,
        # so we rebuild from base
        conn.execute("DELETE FROM session_fts_v")
        conn.execute("""
            INSERT INTO session_fts_v(session_id, role, content, timestamp)
            SELECT session_id, role, content, timestamp FROM session_fts
        """)
        conn.commit()

        # ── Run FTS5 search ──
        try:
            cursor = conn.execute(
                "SELECT session_id, snippet(session_fts_v, 2, '<b>', '</b>', '...', 40) "
                "FROM session_fts_v WHERE content MATCH ? "
                "ORDER BY rank LIMIT ?",
                (query, min(limit, 20)),
            )
            results = cursor.fetchall()
        except sqlite3.OperationalError as e:
            return f"[session_search] Query error: {e}\nTry simpler keywords (FTS5 syntax: +required -excluded \"exact phrase\")"

    if not results:
        header = f"[session_search] No matches for '{query}'."
        if indexed_count:
            header += f" Indexed {indexed_count} new session(s)."
        return header

    output = []
    if indexed_count:
        output.append(f"<b>Session Search: `{query}`</b> ({len(results)} hits, {indexed_count} new indexed)")
    else:
        output.append(f"<b>Session Search: `{query}`</b> ({len(results)} hits)")

    for sid, snippet in results:
        output.append(f"  • `{sid[:12]}…` — {snippet}")

    if indexed_count:
        output.append(f"\n_Indexed {indexed_count} new/changed session(s)_")

    return "\n".join(output)


TOOL_DEF = {
    "name": "session_search",
    "description": (
        "Search past conversations using full-text search. "
        "Use this to find what was discussed in previous sessions, "
        "recover context, or find decisions made earlier. "
        "Supports FTS5 syntax: +required -excluded \"exact phrase\". "
        "Auto-indexes new session files on each search."
    ),
    "handler": session_search,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. Supports FTS5 syntax: "
                               "'keyword1 keyword2' (AND), "
                               "'+required -excluded' (required/excluded), "
                               "'\"exact phrase\"' (phrase match), "
                               "'keyword*' (prefix wildcard).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 5, max: 20).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    "risk_level": "low",
}
