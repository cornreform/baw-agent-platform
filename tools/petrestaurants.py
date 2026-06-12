"""BAW built-in: petrestaurants — query Hong Kong's first 1,000 pet-friendly
restaurants (FEHD 2026-06-12 lottery result, sourced from petwellhk.com).

Built via SELF_BUILD_RECIPE 2026-06-12 — proof that BAW can self-build.

Provides:
  - search_by_district(district)  — filter by district name
  - search_by_region(region)      — filter by region (港島區/九龍區/新界區)
  - nearest(lat, lon, k=10)       — sort by distance from a point
  - search(query, ...)            — name fuzzy search
  - stats()                       — counts per region / district
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Optional

from core.paths import data_dir

DATA_FILE = data_dir() / "petrestaurants.json"


def _load() -> dict:
    if not DATA_FILE.exists():
        return {"restaurants": [], "districts": {}}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _format(r: dict, extra: Optional[dict] = None) -> str:
    base = (f"  • {r['name']}  ({r['district']} / {r['region']})\n"
            f"      {r['address']}")
    if extra and "distance_km" in extra:
        base += f"\n      📍 {extra['distance_km']:.2f} km away"
    return base


def search_by_district(district: str) -> str:
    """Filter restaurants by district name (e.g. 灣仔, 油尖區, 離島)."""
    d = _load()
    matches = [r for r in d["restaurants"] if r["district"] == district]
    if not matches:
        all_d = sorted({r["district"] for r in d["restaurants"]})
        return (f"⚠️ No restaurants found in district '{district}'.\n"
                f"Available districts: {', '.join(all_d)}")
    out = [f"🐾 Found {len(matches)} pet-friendly restaurant(s) in {district}:"]
    for r in matches[:20]:
        out.append(_format(r))
    if len(matches) > 20:
        out.append(f"  …and {len(matches) - 20} more")
    return "\n".join(out)


def search_by_region(region: str) -> str:
    """Filter by region: 港島區 / 九龍區 / 新界區."""
    d = _load()
    matches = [r for r in d["restaurants"] if r["region"] == region]
    if not matches:
        all_r = sorted({r["region"] for r in d["restaurants"]})
        return (f"⚠️ No restaurants found in region '{region}'.\n"
                f"Available regions: {', '.join(all_r)}")
    out = [f"🐾 {len(matches)} pet-friendly restaurant(s) in {region}:"]
    by_dist: dict[str, list] = {}
    for r in matches:
        by_dist.setdefault(r["district"], []).append(r)
    for dist in sorted(by_dist):
        out.append(f"  [{dist}]")
        for r in by_dist[dist][:5]:
            out.append(_format(r))
        if len(by_dist[dist]) > 5:
            out.append(f"    …and {len(by_dist[dist]) - 5} more in {dist}")
    return "\n".join(out)


def nearest(lat: float, lon: float, k: int = 10) -> str:
    """Return the k nearest pet-friendly restaurants to a lat/lon point."""
    d = _load()
    with_loc = [r for r in d["restaurants"] if r.get("lat") is not None]
    scored = []
    for r in with_loc:
        dist = _haversine_km(lat, lon, r["lat"], r["lon"])
        scored.append((dist, r))
    scored.sort(key=lambda x: x[0])
    out = [f"🐾 {min(k, len(scored))} nearest pet-friendly restaurant(s) to "
           f"({lat:.4f}, {lon:.4f}):"]
    for dist, r in scored[:k]:
        out.append(_format(r, {"distance_km": dist}))
    return "\n".join(out)


def search(query: str, region: Optional[str] = None,
           district: Optional[str] = None, k: int = 10) -> str:
    """Fuzzy name search with optional region/district filter."""
    d = _load()
    q = query.lower()
    matches = [r for r in d["restaurants"]
               if q in r["name"].lower()
               and (not region or r["region"] == region)
               and (not district or r["district"] == district)]
    if not matches:
        return f"⚠️ No matches for query '{query}'."
    out = [f"🐾 {len(matches)} match(es) for '{query}':"]
    for r in matches[:k]:
        out.append(_format(r))
    return "\n".join(out)


def stats() -> str:
    """Show counts per region and district."""
    d = _load()
    rest = d["restaurants"]
    by_region: dict[str, int] = {}
    by_dist: dict[str, int] = {}
    for r in rest:
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
        by_dist[r["district"]] = by_dist.get(r["district"], 0) + 1
    out = [
        f"🐾 Pet-friendly restaurants in dataset: {len(rest)}",
        f"   (FEHD announced {d.get('total_announced', '?')}; "
        f"only {len(rest)} published as of {d.get('scraped_at', '?')})",
        "",
        "By region:",
    ]
    for region, count in sorted(by_region.items(), key=lambda x: -x[1]):
        out.append(f"  {region}: {count}")
    out.append("")
    out.append("By district:")
    for dist, count in sorted(by_dist.items(), key=lambda x: -x[1]):
        out.append(f"  {dist}: {count}")
    return "\n".join(out)


# ── Tool registry ────────────────────────────────────────────

def _dispatch(action: str, **kwargs) -> str:
    if action == "search_by_district":
        return search_by_district(kwargs["district"])
    if action == "search_by_region":
        return search_by_region(kwargs["region"])
    if action == "nearest":
        return nearest(float(kwargs["lat"]), float(kwargs["lon"]),
                       int(kwargs.get("k", 10)))
    if action == "search":
        return search(kwargs["query"],
                      region=kwargs.get("region"),
                      district=kwargs.get("district"),
                      k=int(kwargs.get("k", 10)))
    if action == "stats":
        return stats()
    return (f"Error: unknown action '{action}'. "
            f"Use: search_by_district, search_by_region, nearest, search, stats")


TOOL_DEF = {
    "name": "petrestaurants",
    "description": (
        "Query the Hong Kong FEHD 2026-06-12 pet-friendly restaurant lottery result "
        "(sourced from petwellhk.com). Actions: search_by_district, search_by_region, "
        "nearest (lat/lon, great-circle distance), search (name fuzzy), stats."
    ),
    "handler": _dispatch,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search_by_district", "search_by_region", "nearest", "search", "stats"],
            },
            "district": {"type": "string"},
            "region": {"type": "string"},
            "lat": {"type": "number"},
            "lon": {"type": "number"},
            "query": {"type": "string"},
            "k": {"type": "integer", "default": 10},
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
