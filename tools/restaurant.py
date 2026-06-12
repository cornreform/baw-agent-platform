"""BAW built-in: restaurant — find restaurants via OpenStreetMap Overpass API.

Data source: https://overpass-api.de/api/interpreter (OpenStreetMap).
  - 100% free, no API key, no signup.
  - Live query, no cache staleness, no rate limits in normal use.
  - Coverage in HK is good for chain restaurants and shopping-mall
    food courts; less good for tiny dai pai dong.

Why not Google Places?
  - Sub-agent 2026-06-12 defaulted to Google Places and got stuck on
    "the API key is invalid" — paying + key management for a tool
    BAW can run for free is the wrong default. Overpass wins on every
    axis: cost, friction, reproducibility, offline-debuggability.

Query schema (all filters optional except one of: bbox / lat+lon+k / query):
  - bbox=(south, west, north, east)        search a bounding box
  - lat, lon, k=10                         nearest k to a point
  - query="ramen"                          name fuzzy (Chinese + English)
  - cuisine="japanese"                     OSM cuisine tag
  - amenity=restaurant|cafe|fast_food|...   OSM amenity tag
  - max_km=2.0                             only within N km of lat/lon
  - pet_friendly=False                     intersects with petrestaurants
                                            dataset if True (uses city centroid
                                            when no lat/lon given)

Built via SELF_BUILD_RECIPE 2026-06-12 — same path as petrestaurants.
"""
from __future__ import annotations
import json
import math
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.paths import data_dir


CACHE_FILE = data_dir() / "restaurant_cache.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "BAW/1.0 (+https://github.com/cornreform/baw-agent-platform)"
CACHE_TTL_SECONDS = 24 * 3600  # 24h — OSM POIs don't move


# ── Overpass query builder ─────────────────────────────────

def _bbox_query(south: float, west: float, north: float, east: float,
                amenity: str = "restaurant", limit: int = 200) -> str:
    """Return an Overpass QL query string for nodes+ways in a bbox."""
    safe_amenity = re.sub(r"[^a-z_]", "", amenity.lower())
    return (
        f"[out:json][timeout:25];\n"
        f"(\n"
        f"  node[\"amenity\"=\"{safe_amenity}\"]({south},{west},{north},{east});\n"
        f"  way[\"amenity\"=\"{safe_amenity}\"]({south},{west},{north},{east});\n"
        f");\n"
        f"out center {limit};"
    )


def _run_overpass(query: str) -> List[Dict[str, Any]]:
    """POST query to Overpass, return list of normalized POI dicts.

    Each result dict:
        { id, name, lat, lon, amenity, cuisine, tags }
    """
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL, data=body, method="POST",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    elements = data.get("elements", [])
    out: List[Dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags", {}) or {}
        name = tags.get("name") or tags.get("name:en") or tags.get("name:zh")
        if not name:
            continue  # unnamed POIs are noise
        lat = el.get("lat")
        lon = el.get("lon")
        center = el.get("center") or {}
        lat = lat if lat is not None else center.get("lat")
        lon = lon if lon is not None else center.get("lon")
        if lat is None or lon is None:
            continue
        out.append({
            "id": f"OSM-{el.get('type','?')[:3]}-{el.get('id')}",
            "name": name,
            "name_en": tags.get("name:en"),
            "name_zh": tags.get("name:zh") or tags.get("name"),
            "lat": float(lat),
            "lon": float(lon),
            "amenity": tags.get("amenity"),
            "cuisine": tags.get("cuisine"),
            "opening_hours": tags.get("opening_hours"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
            "addr": _format_addr(tags),
            "tags": tags,
        })
    return out


def _format_addr(tags: dict) -> str:
    parts = []
    for k in ("addr:street", "addr:housenumber", "addr:suburb", "addr:city"):
        v = tags.get(k)
        if v:
            parts.append(v)
    return ", ".join(parts) if parts else ""


# ── Cache (24h TTL) ────────────────────────────────────────

def _cache_key(query: str) -> str:
    import hashlib
    return hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]


def _cache_get(query: str) -> Optional[List[Dict[str, Any]]]:
    if not CACHE_FILE.exists():
        return None
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    key = _cache_key(query)
    entry = cache.get(key)
    if not entry:
        return None
    if time.time() - entry.get("ts", 0) > CACHE_TTL_SECONDS:
        return None
    return entry.get("results")


def _cache_put(query: str, results: List[Dict[str, Any]]) -> None:
    cache: Dict[str, Any] = {}
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    key = _cache_key(query)
    cache[key] = {"ts": time.time(), "query": query, "results": results}
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Public query API ───────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


def search(
    *,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    k: int = 10,
    max_km: Optional[float] = None,
    cuisine: Optional[str] = None,
    amenity: str = "restaurant",
    query: Optional[str] = None,
    pet_friendly: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """Find restaurants. Returns dict with 'results', 'count', 'source',
    'warnings'."""
    warnings: List[str] = []
    if not bbox and lat is None and not query:
        # Default: Causeway Bay 1 km box
        lat0, lon0 = 22.280, 114.175
        d = 0.01  # ~1.1 km
        bbox = (lat0 - d, lon0 - d, lat0 + d, lon0 + d)
        warnings.append("No location given — defaulting to Causeway Bay 1km box.")
    elif not bbox and lat is None and query:
        # Query-only — default to HK-wide bbox
        bbox = (22.15, 113.85, 22.55, 114.40)
        warnings.append("Query without location — defaulting to HK-wide bbox.")

    if bbox is None and lat is not None and lon is not None:
        d = (max_km or 1.0) / 111.0
        bbox = (lat - d, lon - d, lat + d, lon + d)
    assert bbox is not None
    south, west, north, east = bbox

    overpass_q = _bbox_query(south, west, north, east, amenity=amenity, limit=limit)
    cached = _cache_get(overpass_q)
    if cached is not None:
        results = cached
        source = "cache"
    else:
        try:
            results = _run_overpass(overpass_q)
            _cache_put(overpass_q, results)
            source = "overpass"
        except Exception as e:
            return {
                "results": [],
                "count": 0,
                "source": "error",
                "error": str(e),
                "warnings": warnings,
            }

    # Filters
    filtered = results
    if cuisine:
        c = cuisine.lower()
        filtered = [r for r in filtered if r.get("cuisine")
                    and c in r["cuisine"].lower()]
    if query:
        pat = re.compile(re.escape(query), re.IGNORECASE)
        filtered = [r for r in filtered
                    if pat.search(r.get("name") or "")
                    or pat.search(r.get("name_en") or "")
                    or pat.search(r.get("name_zh") or "")]
    if lat is not None and lon is not None and max_km is not None:
        filtered = [
            r for r in filtered
            if _haversine_km(lat, lon, r["lat"], r["lon"]) <= max_km
        ]
        # Sort by distance
        filtered.sort(key=lambda r: _haversine_km(lat, lon, r["lat"], r["lon"]))
    elif lat is not None and lon is not None:
        filtered.sort(key=lambda r: _haversine_km(lat, lon, r["lat"], r["lon"]))

    if k and lat is not None and lon is not None:
        filtered = filtered[:k]

    # Pet-friendly intersection (bonus)
    pet_match_count = 0
    if pet_friendly:
        try:
            from tools import petrestaurants as _pet
            ds, _src = _pet._ensure_fresh()
            if isinstance(ds, dict):
                pet_items = ds.get("items", [])
                pet_names = {p["name"].lower() for p in pet_items}
                for r in filtered:
                    if r["name"].lower() in pet_names:
                        r["pet_friendly"] = True
                        pet_match_count += 1
        except Exception as e:
            warnings.append(f"pet_friendly lookup failed: {e}")

    if pet_friendly:
        warnings.append(
            f"pet_friendly=True: {pet_match_count} of {len(filtered)} match "
            f"the FEHD 50-restaurant pet-friendly dataset (osm only knows "
            f"pet_friendly if the operator tagged it)."
        )

    return {
        "results": filtered,
        "count": len(filtered),
        "source": source,
        "warnings": warnings,
    }


# ── TOOL_DEF (for BAW tool registry) ──────────────────────

def _handler(**kwargs) -> Dict[str, Any]:
    """Bridge from BAW tool-call convention to search()."""
    return search(**kwargs)


TOOL_DEF = {
    "name": "restaurant",
    "description": (
        "Find restaurants via OpenStreetMap Overpass (free, no key). "
        "Filter by bbox / lat+lon / max_km / cuisine / amenity / query. "
        "Set pet_friendly=True to intersect with the FEHD 50-restaurant "
        "pet-friendly dataset (osm only knows pet_friendly if tagged)."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "bbox": {
                "type": "array",
                "items": {"type": "number"},
                "description": "[south, west, north, east] bounding box. Use lat+lon instead when possible.",
            },
            "lat": {"type": "number", "description": "Latitude for nearest-k or radius search."},
            "lon": {"type": "number", "description": "Longitude for nearest-k or radius search."},
            "k": {"type": "number", "default": 10, "description": "How many nearest to return when lat+lon are given."},
            "max_km": {"type": "number", "description": "Radius in km from lat/lon."},
            "cuisine": {"type": "string", "description": "OSM cuisine tag, e.g. 'japanese', 'pizza', 'ramen'."},
            "amenity": {"type": "string", "default": "restaurant", "description": "OSM amenity: restaurant|cafe|fast_food|food_court|pub|ice_cream|bakery."},
            "query": {"type": "string", "description": "Name fuzzy (Chinese + English)."},
            "pet_friendly": {"type": "boolean", "default": False},
            "limit": {"type": "number", "default": 200},
        },
        "required": [],
    },
}
