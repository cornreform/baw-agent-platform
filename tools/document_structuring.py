"""BAW — Document Structuring Tool

Learned from barnetwang/document_structuring (Hermes Agent Skill).
Parses PDF/DOCX, splits by headings into structured Markdown chunks,
stores in SQLite with FTS5 search, supports TOC/chunk/search/delete.

Designed for BAW's runtime environment — uses core.paths for data dir.
"""

import re
import json
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any

# ── BAW locators ────────────────────────────────────────────────
from core.paths import data_dir

# ── Third-party imports (installed by the tool on first use) ────
_HAS_FITZ = False
_HAS_PYMUPDF4LLM = False
_HAS_DOCX = False

try:
    import fitz
    _HAS_FITZ = True
except ImportError:
    pass

try:
    import pymupdf4llm
    _HAS_PYMUPDF4LLM = True
except ImportError:
    pass

try:
    from docx import Document
    _HAS_DOCX = True
except ImportError:
    pass


# ── Constants ────────────────────────────────────────────────────

_STORE_DIR = data_dir() / "documents"
_CHUNKS_DIR = _STORE_DIR / "chunks"
_DB_PATH = _STORE_DIR / "documents.db"

VALID_MAJOR_RANGE = range(1, 100)
MD_HEADING_REGEX = re.compile(r"^(#+)\s*(?:\*\*\s*)?(.*?)(?:\s*\*\*)?$")
EXPLICIT_NUM_REGEX = re.compile(
    r"^(?:Chapter|Section)?\s*(\d+(?:\.\d+)*)\.?(?:[\s:-]+(.*))?$", re.IGNORECASE
)
TOC_IGNORE_REGEX = re.compile(r"\.{3,}\s*\d+$")
IGNORE_PATTERNS = [
    re.compile(r"^AMD Confidential.*$", re.IGNORECASE),
    re.compile(r"^Page\s+\d+.*$", re.IGNORECASE),
    re.compile(r"^Table of Contents$", re.IGNORECASE),
]

SCHEMA_VERSION = 1


# ── DB helpers ───────────────────────────────────────────────────

def _get_db():
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _init_db():
    conn = _get_db()
    c = conn.cursor()
    meta_exists = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_meta'"
    ).fetchone()
    if meta_exists:
        row = c.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()
        if row and int(row["value"]) >= SCHEMA_VERSION:
            conn.close()
            return

    c.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            upload_time TEXT NOT NULL,
            chunk_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success'
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            section_number TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            file_path TEXT,
            FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            title, content,
            content='chunks',
            content_rowid='id',
            tokenize='unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content);
            INSERT INTO chunks_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
        END;
        CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    c.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),)
    )
    conn.commit()
    conn.close()


# ── Parsing (ported from Hermes skill) ──────────────────────────

class SectionNumberTracker:
    def __init__(self, max_depth=10):
        self.current_nums = [0] * max_depth

    def sync(self, section_num: str):
        parts = section_num.split(".")
        for i, part in enumerate(parts):
            if i < len(self.current_nums):
                try:
                    self.current_nums[i] = int(part)
                except ValueError:
                    self.current_nums[i] = 1
        for i in range(len(parts), len(self.current_nums)):
            self.current_nums[i] = 0

    def generate(self, level: int) -> str:
        idx = level - 1
        if idx >= len(self.current_nums):
            idx = len(self.current_nums) - 1
        self.current_nums[idx] += 1
        for i in range(idx + 1, len(self.current_nums)):
            self.current_nums[i] = 0
        parts = []
        for i in range(level):
            val = max(self.current_nums[i], 1)
            parts.append(str(val))
        return ".".join(parts)


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[\\/*?:\"<>|]", "", name.replace("*", "").replace("#", "")).replace(" ", "_")


def _is_ignored(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return True
    if TOC_IGNORE_REGEX.search(clean):
        return True
    for pat in IGNORE_PATTERNS:
        if pat.match(clean):
            return True
    if re.match(r"^\d+$", clean):
        return True
    return False


def _extract_pdf_lines(file_path: str) -> list[dict]:
    import fitz
    import pymupdf4llm

    doc = fitz.open(file_path)
    lines = []

    # Build heading→page map from PDF TOC
    try:
        toc_entries = doc.get_toc()
        toc_page_map = {}
        num_strip = re.compile(r"^\d+(\.\d+)*\s*", re.ASCII)
        for _lvl, title, page in toc_entries:
            t = title.strip()
            if t:
                toc_page_map[t] = page
                no_num = num_strip.sub("", t).strip()
                if no_num and no_num != t:
                    toc_page_map[no_num] = page
    except Exception:
        toc_page_map = {}

    total_pages = len(doc)
    for start in range(0, total_pages, 50):
        end = min(start + 50, total_pages)
        md_text = pymupdf4llm.to_markdown(file_path, pages=list(range(start, end)),
                                            show_progress=False)
        page_num = start + 1
        for raw_line in md_text.split("\n"):
            stripped = raw_line.strip()
            if not stripped:
                continue
            lines.append({
                "text": stripped,
                "page": page_num,
                "is_md": True,
            })
    doc.close()
    return lines


def _extract_docx_lines(file_path: str) -> list[dict]:
    from docx import Document
    doc = Document(file_path)
    lines = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name.lower() if para.style else ""
        is_heading = style.startswith("heading")
        lines.append({
            "text": text,
            "page": 1,
            "is_md": is_heading,
            "style": style,
        })
    return lines


def _parse_into_chunks(extracted: list[dict], source_name: str) -> list[dict]:
    chunks = []
    tracker = SectionNumberTracker()
    current_chunk = None

    def flush():
        nonlocal current_chunk
        if current_chunk and current_chunk["content"].strip():
            chunks.append(current_chunk)
            current_chunk = None

    for entry in extracted:
        text = entry["text"]
        page = entry["page"]

        # Try markdown heading
        md_match = MD_HEADING_REGEX.match(text) if entry.get("is_md", False) else None
        # Try explicit number heading
        num_match = EXPLICIT_NUM_REGEX.match(text)

        heading_level = None
        section_num = None
        heading_title = None

        if md_match:
            heading_level = len(md_match.group(1))
            heading_title = md_match.group(2).strip()
        elif num_match:
            heading_level = 1
            section_num = num_match.group(1)
            heading_title = (num_match.group(2) or "").strip()

        if heading_level and heading_title:
            if section_num:
                tracker.sync(section_num)
            else:
                section_num = tracker.generate(heading_level)

            flush()
            current_chunk = {
                "number": section_num,
                "title": heading_title,
                "content": "",
                "page_start": page,
                "source": source_name,
            }
        elif current_chunk is not None:
            if current_chunk["content"]:
                current_chunk["content"] += "\n" + text
            else:
                current_chunk["content"] = text

    flush()
    return chunks


# ── Document operations ──────────────────────────────────────────

def _check_deps():
    missing = []
    if not _HAS_FITZ:
        missing.append("PyMuPDF")
    if not _HAS_PYMUPDF4LLM:
        missing.append("pymupdf4llm")
    if not _HAS_DOCX:
        missing.append("python-docx")
    if missing:
        return f"Missing dependencies: {', '.join(missing)}"
    return None


def parse_document(file_path: str) -> dict:
    """Parse a PDF or DOCX file, store chunks in the database."""
    deps_err = _check_deps()
    if deps_err:
        return {"success": False, "error": deps_err}

    p = Path(file_path)
    if not p.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    ext = p.suffix.lower()
    if ext not in (".pdf", ".docx"):
        return {"success": False, "error": f"Unsupported file type: {ext}. Only PDF and DOCX."}

    source_name = p.name

    try:
        if ext == ".pdf":
            extracted = _extract_pdf_lines(str(p))
        else:
            extracted = _extract_docx_lines(str(p))

        chunks = _parse_into_chunks(extracted, source_name)
        if not chunks:
            return {"success": False, "error": "No content extracted from document."}

        _init_db()
        conn = _get_db()
        c = conn.cursor()

        # Remove old entries with same filename
        c.execute("SELECT id FROM documents WHERE filename = ?", (source_name,))
        for row in c.fetchall():
            delete_document(row["id"])

        # Insert document
        now = datetime.now().isoformat()
        c.execute(
            "INSERT INTO documents (filename, upload_time, chunk_count, status) VALUES (?, ?, ?, ?)",
            (source_name, now, len(chunks), "success"),
        )
        doc_id = c.lastrowid

        # Write chunk markdown files
        _CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
        doc_chunk_dir = _CHUNKS_DIR / str(doc_id)
        doc_chunk_dir.mkdir(parents=True, exist_ok=True)

        for chunk in chunks:
            num = chunk["number"]
            title = chunk["title"]
            fname = f"{num}_{_sanitize_filename(title)}.md"
            fpath = doc_chunk_dir / fname

            md_content = f"# {num} {title}\n\n"
            md_content += "metadata:\n"
            md_content += f"- source file: {chunk['source']}\n"
            md_content += f"- section number: {num}\n"
            md_content += f"- page start: {chunk['page_start']}\n\n"
            md_content += "content:\n"
            md_content += chunk["content"]

            fpath.write_text(md_content, encoding="utf-8")

            c.execute(
                "INSERT INTO chunks (document_id, section_number, title, content, page_start, file_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, num, title, chunk["content"], chunk["page_start"], str(fpath)),
            )

        conn.commit()
        conn.close()

        return {
            "success": True,
            "document_id": doc_id,
            "filename": source_name,
            "chunk_count": len(chunks),
        }

    except Exception as e:
        return {"success": False, "error": f"Parse error: {e}"}


def list_documents() -> dict:
    """List all parsed documents."""
    _init_db()
    conn = _get_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, filename, upload_time, chunk_count, status "
        "FROM documents ORDER BY upload_time DESC"
    )
    docs = [dict(row) for row in c.fetchall()]
    conn.close()
    return {"documents": docs, "count": len(docs)}


def get_toc(document_id: int) -> dict:
    """Get Table of Contents for a document."""
    _init_db()
    conn = _get_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, section_number, title, page_start, file_path "
        "FROM chunks WHERE document_id = ? ORDER BY section_number ASC",
        (document_id,),
    )
    toc = [dict(row) for row in c.fetchall()]
    conn.close()
    return {"document_id": document_id, "toc": toc, "count": len(toc)}


def get_chunk(chunk_id: int) -> dict:
    """Get full chunk content by ID."""
    _init_db()
    conn = _get_db()
    c = conn.cursor()
    c.execute(
        "SELECT c.*, d.filename as document_name "
        "FROM chunks c JOIN documents d ON c.document_id = d.id "
        "WHERE c.id = ?",
        (chunk_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return {"found": False, "error": f"Chunk {chunk_id} not found."}
    return {"found": True, "chunk": dict(row)}


def search_chunks(query: str, document_id: int | None = None) -> dict:
    """Full-text search across chunks."""
    _init_db()
    conn = _get_db()
    c = conn.cursor()

    keywords = [k.strip() for k in query.split() if k.strip()]
    if not keywords:
        conn.close()
        return {"results": [], "count": 0}

    fts_query = " AND ".join(keywords)

    sql = """
        SELECT c.id, c.document_id, c.section_number, c.title, c.page_start,
               c.file_path, d.filename as document_name,
               snippet(chunks_fts, 1, '==', '==', '...', 150) as snippet
        FROM chunks_fts f
        JOIN chunks c ON f.rowid = c.id
        JOIN documents d ON c.document_id = d.id
        WHERE chunks_fts MATCH ?
    """
    params = [fts_query]

    if document_id is not None:
        sql += " AND c.document_id = ?"
        params.append(document_id)

    sql += " ORDER BY d.upload_time DESC, c.section_number ASC LIMIT 100"

    try:
        c.execute(sql, params)
        results = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        # Fallback to LIKE search
        like_param = f"%{query}%"
        fallback = """
            SELECT c.id, c.document_id, c.section_number, c.title, c.page_start,
                   c.file_path, d.filename as document_name,
                   substr(c.content, 1, 300) as snippet
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE (c.title LIKE ? OR c.content LIKE ?)
        """
        f_params = [like_param, like_param]
        if document_id is not None:
            fallback += " AND c.document_id = ?"
            f_params.append(document_id)
        fallback += " ORDER BY d.upload_time DESC, c.section_number ASC LIMIT 100"
        c.execute(fallback, f_params)
        results = [dict(row) for row in c.fetchall()]

    conn.close()

    for r in results:
        if r.get("snippet"):
            r["snippet"] = r["snippet"].replace("\n", " ").strip()
            if not r["snippet"].endswith("..."):
                r["snippet"] += "..."
        else:
            r["snippet"] = ""

    return {"results": results, "count": len(results)}


def delete_document(document_id: int) -> dict:
    """Delete a document, its chunks, and physical files."""
    _init_db()
    conn = _get_db()
    c = conn.cursor()

    c.execute("SELECT filename FROM documents WHERE id = ?", (document_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"success": False, "error": f"Document ID {document_id} not found."}

    filename = row["filename"]

    # Remove physical chunk dir
    doc_chunk_dir = _CHUNKS_DIR / str(document_id)
    if doc_chunk_dir.exists():
        shutil.rmtree(doc_chunk_dir)

    # Delete from DB (cascade deletes chunks)
    c.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()
    conn.close()

    return {
        "success": True,
        "filename": filename,
        "document_id": document_id,
        "message": f"Document '{filename}' (ID {document_id}) deleted.",
    }


# ── Tool handler ─────────────────────────────────────────────────

def handler(action: str, file_path: str = "", document_id: int = 0,
            chunk_id: int = 0, query: str = "", **kwargs) -> Any:
    """Dispatch to the correct operation."""

    if action == "parse":
        return parse_document(file_path)
    elif action == "list":
        return list_documents()
    elif action == "toc":
        return get_toc(document_id)
    elif action == "get_chunk":
        return get_chunk(chunk_id)
    elif action == "search":
        return search_chunks(query, document_id if document_id else None)
    elif action == "delete":
        return delete_document(document_id)
    else:
        return {"success": False, "error": f"Unknown action: {action}. "
                "Valid: parse, list, toc, get_chunk, search, delete"}


TOOL_DEF = {
    "name": "document_structuring",
    "description": "Parse PDF/DOCX files into structured Markdown chunks stored in SQLite with FTS5 full-text search. "
                   "Actions: parse (file_path), list, toc (document_id), get_chunk (chunk_id), search (query, document_id optional), delete (document_id). "
                   "Requires: PyMuPDF, pymupdf4llm, python-docx (auto-checked).",
    "handler": handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["parse", "list", "toc", "get_chunk", "search", "delete"],
                "description": "Operation to perform."
            },
            "file_path": {
                "type": "string",
                "description": "Path to PDF or DOCX file (required for 'parse')."
            },
            "document_id": {
                "type": "integer",
                "description": "Document database ID (required for 'toc', 'delete'; optional for 'search').",
                "default": 0
            },
            "chunk_id": {
                "type": "integer",
                "description": "Chunk database ID (required for 'get_chunk').",
                "default": 0
            },
            "query": {
                "type": "string",
                "description": "Search query string (required for 'search')."
            }
        },
        "required": ["action"]
    },
    "risk_level": "medium",
}
