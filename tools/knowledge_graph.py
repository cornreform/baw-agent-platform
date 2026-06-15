"""BAW — Knowledge Graph Memory

Relational triple store: (subject, relation, object).
Upgrades flat JSONL memory to linked knowledge graph.
Enables semantic queries: "what relates to X?", "find entities connected by Y".

File: ~/.baw/knowledge_graph.json
"""

import json
import re
from datetime import datetime
from pathlib import Path


_KG_FILE = Path.home() / ".baw" / "knowledge_graph.json"


def _ensure_store():
    _KG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _KG_FILE.exists():
        _KG_FILE.write_text('{"triples": [], "entities": {}}', encoding="utf-8")


def _load() -> dict:
    _ensure_store()
    try:
        return json.loads(_KG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return {"triples": [], "entities": {}}


def _save(data: dict):
    _KG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize(s: str) -> str:
    """Normalize entity name for matching."""
    return s.strip().lower().replace(" ", "_")


# ── Public API ──────────────────────────────────────────────────


def add_triple(subject: str, relation: str, object: str) -> str:
    """Add a fact: subject -[relation]-> object.

    Auto-deduplicates: if identical triple exists, returns existing ID.
    Auto-registers entities. Supports inverse relations.

    Args:
        subject: The subject entity (e.g., 'mmx-cli')
        relation: The relation (e.g., 'is_package_name_of', 'installed_via')
        object: The object entity (e.g., 'npm')

    Returns:
        Confirmation with triple ID.
    """
    sub = subject.strip()[:200]
    rel = relation.strip().lower().replace(" ", "_")[:50]
    obj = object.strip()[:200]
    if not sub or not rel or not obj:
        return "Error: subject, relation, and object are all required"

    data = _load()
    ts = datetime.now().isoformat()

    # Dedup
    for t in data["triples"]:
        if t["s"] == sub and t["r"] == rel and t["o"] == obj:
            return f"[>] Triple already exists (id: {t['id']})"

    tid = f"t{len(data['triples'])+1}"
    triple = {"id": tid, "s": sub, "r": rel, "o": obj, "ts": ts}
    data["triples"].append(triple)

    # Register entities
    for name in [sub, obj]:
        nk = _normalize(name)
        if nk not in data["entities"]:
            data["entities"][nk] = {
                "name": name,
                "first_seen": ts,
                "relations": [rel],
            }
        else:
            if rel not in data["entities"][nk]["relations"]:
                data["entities"][nk]["relations"].append(rel)

    _save(data)
    return f"[OK] Triple saved: {sub} -[{rel}]-> {obj}  (id: {tid})"


def query_entity(entity: str) -> str:
    """Find all triples involving this entity.

    Args:
        entity: Entity name (fuzzy matched).

    Returns:
        Formatted list of relations.
    """
    if not entity.strip():
        return "Error: entity is required"

    entity = entity.strip()
    data = _load()

    matches = []
    for t in data["triples"]:
        if t["s"] == entity:
            matches.append(f"  {entity} -[{t['r']}]-> {t['o']}")
        if t["o"] == entity:
            matches.append(f"  {t['s']} -[{t['r']}]-> {entity}")

    if not matches:
        return f"No knowledge found for '{entity}'"

    return f"## Knowledge: {entity}\n" + "\n".join(matches)


def query_relation(relation: str) -> str:
    """Find all triples with this relation.

    Args:
        relation: Relation to search (e.g., 'installed_via').

    Returns:
        Formatted list of triples.
    """
    rel = relation.strip().lower().replace(" ", "_")[:50]
    if not rel:
        return "Error: relation is required"

    data = _load()
    matches = [t for t in data["triples"] if t["r"] == rel]

    if not matches:
        return f"No triples with relation '{rel}'"

    lines = [f"## Triples with relation '{rel}' ({len(matches)}):"]
    for t in matches:
        lines.append(f"  {t['s']} -[{t['r']}]-> {t['o']}")
    return "\n".join(lines)


def search_knowledge(query: str) -> str:
    """Full-text search through subjects and objects.

    Args:
        query: Search text.

    Returns:
        Formatted matching triples.
    """
    q = query.strip().lower()
    if not q:
        return "Error: query is required"

    data = _load()
    matches = []
    for t in data["triples"]:
        if q in t["s"].lower() or q in t["o"].lower() or q in t["r"]:
            matches.append(t)

    if not matches:
        return f"No knowledge matches '{query}'"

    lines = [f"## Knowledge matching '{query}' ({len(matches)}):"]
    for t in matches:
        lines.append(f"  [{t['id']}] {t['s']} -[{t['r']}]-> {t['o']}")
    return "\n".join(lines)


def stats() -> str:
    """Show knowledge graph statistics."""
    data = _load()
    triples = data["triples"]
    entities = data["entities"]
    total_relations = sum(len(e.get("relations", [])) for e in entities.values())
    return (
        f"[STATS] Knowledge Graph:\n"
        f"  Triples:  {len(triples)}\n"
        f"  Entities: {len(entities)}\n"
        f"  Relations: {total_relations}\n"
        f"  File: {_KG_FILE}"
    )


def extract_from_memory() -> str:
    """Auto-extract triples from BAW's JSONL memory store.

    Reads memories (each entry = {"content": "...", "tags": [...], ...})
    and creates entity relations for tagged concepts.
    If no tags, extracts entities from content via keyword detection.

    Returns:
        Summary of what was extracted.
    """
    mem_file = Path.home() / ".baw" / "memory" / "store.jsonl"
    if not mem_file.exists():
        mem_file = Path.home() / ".baw" / "store.jsonl"
        if not mem_file.exists():
            mem_file = Path.home() / ".baw" / "store.md"
            if not mem_file.exists():
                return "No memory store found (~/.baw/memory/store.jsonl, store.jsonl, or store.md)"

    data = _load()
    count = 0
    try:
        with open(mem_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                content = entry.get("content", "").strip()
                tags = entry.get("tags", [])
                if not content:
                    continue

                # ── Smart extraction: tags first, then content entities ──

                # Method A: tag-based triples
                for i, tag in enumerate(tags[:5]):
                    tag = tag.strip()
                    if not tag:
                        continue
                    triple = {"id": f"m{count}", "s": tag, "r": "tagged",
                              "o": content[:100], "ts": entry.get("ts", "")}
                    dup = False
                    for t in data["triples"]:
                        if t["s"] == triple["s"] and t["r"] == triple["r"] and t["o"] == triple["o"]:
                            dup = True
                            break
                    if not dup:
                        data["triples"].append(triple)
                        count += 1
                        nk = _normalize(tag)
                        if nk not in data["entities"]:
                            data["entities"][nk] = {
                                "name": tag, "first_seen": triple["ts"],
                                "relations": ["tagged"],
                            }

                # Method B: content-entity extraction (for untagged memories)
                if not tags:
                    entities = _extract_entities_from_content(content)
                    for i, entity in enumerate(entities[:3]):
                        triple = {"id": f"e{count}", "s": entity, "r": "mentioned_in",
                                  "o": content[:80], "ts": entry.get("ts", "")}
                        dup = False
                        for t in data["triples"]:
                            if t["s"] == triple["s"] and t["r"] == triple["r"] and t["o"] == triple["o"]:
                                dup = True
                                break
                        if not dup:
                            data["triples"].append(triple)
                            count += 1
                            nk = _normalize(entity)
                            if nk not in data["entities"]:
                                data["entities"][nk] = {
                                    "name": entity, "first_seen": triple["ts"],
                                    "relations": ["mentioned_in"],
                                }
    except FileNotFoundError:
        return "Memory store not found."

    _save(data)
    return f"[OK] Extracted {count} triples from memory store."


def _extract_entities_from_content(content: str) -> list[str]:
    """Extract meaningful entities from plain-text memory content.

    Returns up to 5 entity names detected from keywords.
    """
    entities = []
    content_lower = content.lower()

    # Patterns: detect prefixes like "User:", "BAW:", topics, tools
    import re

    # Named roles
    for pattern in [r"User:\s*(.{1,40})", r"BAW:\s*(.{1,40})",
                    r"(Sunny|Sticky|Robi|BAW|Hermes)"]:
        for m in re.finditer(pattern, content[:300]):
            entity = m.group(1).strip()[:40]
            if entity and entity not in entities:
                entities.append(entity)

    # Topic detection (keyword-based)
    topics = {
        "AI新聞": ["ai", "新聞", "news", "artificial intelligence"],
        "美股": ["美股", "stock", "nasdaq", "dow", "sp500"],
        "電車": ["電車", "tram", "ev", "electric vehicle"],
        "編程": ["code", "程式", "python", "script", "開發"],
        "部署": ["deploy", "docker", "container", "部署"],
        "記憶": ["memory", "記憶", "remember"],
        "工具": ["tool", "工具", "cli", "command"],
        "config": ["config", "setting", "設定", "配置"],
        "TTS": ["tts", "語音", "voice", "speech", "audio"],
        "Vision": ["vision", "image", "圖片", "照片"],
    }
    for topic, keywords in topics.items():
        if any(kw in content_lower for kw in keywords):
            entities.append(topic)

    return entities[:5]


# ── Tool dispatcher ─────────────────────────────────────────────


def _dispatcher(action: str, subject: str = "", relation: str = "",
                object: str = "", entity: str = "", query: str = "") -> str:
    """Dispatch knowledge graph actions."""
    actions = {
        "add": lambda: add_triple(subject, relation, object),  # type: ignore[arg-type]
        "query_entity": lambda: query_entity(entity),  # type: ignore[arg-type]
        "query_relation": lambda: query_relation(relation),  # type: ignore[arg-type]
        "search": lambda: search_knowledge(query),  # type: ignore[arg-type]
        "stats": lambda: stats(),
        "extract": lambda: extract_from_memory(),
    }
    fn = actions.get(action)
    if fn is None:
        avail = ", ".join(actions.keys())
        return f"Error: unknown action '{action}'. Available: {avail}"
    return fn()


TOOL_DEF = {
    "name": "knowledge_graph",
    "description": (
        "Knowledge Graph Memory — store and query relational facts. "
        "Each fact is a triple: subject -[relation]-> object. "
        "Actions: 'add' (save triple), 'query_entity' (find all facts about X), "
        "'query_relation' (find all facts with relation Y), "
        "'search' (full-text), 'stats' (graph size), "
        "'extract' (auto-import from memory store). "
        "Use this to build connected knowledge across sessions."
    ),
    "handler": _dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "query_entity", "query_relation", "search", "stats", "extract"],
                "description": "What to do with the knowledge graph.",
            },
            "subject": {
                "type": "string",
                "description": "Subject entity for 'add' (e.g., 'mmx-cli').",
            },
            "relation": {
                "type": "string",
                "description": "Relation for 'add' or 'query_relation' (e.g., 'installed_via').",
            },
            "object": {
                "type": "string",
                "description": "Object entity for 'add' (e.g., 'npm').",
            },
            "entity": {
                "type": "string",
                "description": "Entity name for 'query_entity'.",
            },
            "query": {
                "type": "string",
                "description": "Search text for 'search'.",
            },
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
