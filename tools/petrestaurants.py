"""BAW built-in: petrestaurants — query Hong Kong's first 1,000 pet-friendly
restaurants (FEHD 2026-06-12 lottery result, sourced from petwellhk.com).

Built via SELF_BUILD_RECIPE 2026-06-12 — proof that BAW can self-build.

Data pipeline:
  Source URL: petwellhk.com/hk-fehd-pet-friendly-restaurants-1000-list
       (Next.js SPA, hydrates client-side — Python `urllib` can't parse it)
       │
       ▼
  data/petrestaurants_source.md  ← rendered markdown, mirrored via web_extract
       │
       ▼  (this tool parses on every call, no live network needed)
  data/petrestaurants.json       ← cached dataset, rebuilt via `baw pet refresh`

To refresh when PetWell publishes more of the 1000:
  1. Re-run web_extract on the source URL (browser-rendered)
  2. Overwrite data/petrestaurants_source.md
  3. Run `baw pet refresh` to rebuild the JSON cache
  4. Queries will return the new data immediately

Provides:
  - search_by_district(district)  — filter by district name
  - search_by_region(region)      — filter by region (港島區/九龍區/新界區)
  - nearest(lat, lon, k=10)       — sort by distance from a point
  - search(query, ...)            — name fuzzy search
  - stats()                       — counts per region / district
  - refresh()                     — re-parse source.md → petrestaurants.json
"""
from __future__ import annotations
import json
import math
import re
import time
from pathlib import Path
from typing import Optional

from core.paths import data_dir

DATA_FILE = data_dir() / "petrestaurants.json"
SOURCE_MD = data_dir() / "petrestaurants_source.md"
SOURCE_URL = "https://petwellhk.com/hk-fehd-pet-friendly-restaurants-1000-list"

# District → (region, approx centroid lat, lon) for distance calculation
DISTRICT_CENTROIDS = {
    "灣仔":   ("港島區", 22.2770, 114.1730),
    "中西區": ("港島區", 22.2830, 114.1580),
    "南區":   ("港島區", 22.2470, 114.1900),
    "東區":   ("港島區", 22.2840, 114.2200),
    "油尖區": ("九龍區", 22.3000, 114.1720),
    "深水埗": ("九龍區", 22.3300, 114.1650),
    "九龍城": ("九龍區", 22.3200, 114.1880),
    "旺角":   ("九龍區", 22.3180, 114.1700),
    "黃大仙": ("九龍區", 22.3450, 114.1950),
    "觀塘":   ("九龍區", 22.3100, 114.2250),
    "元朗":   ("新界區", 22.4450, 114.0250),
    "荃灣":   ("新界區", 22.3710, 114.1100),
    "屯門":   ("新界區", 22.3900, 113.9700),
    "大埔":   ("新界區", 22.4500, 114.1650),
    "西貢":   ("新界區", 22.3800, 114.2700),
    "沙田":   ("新界區", 22.3800, 114.1900),
    "離島":   ("新界區", 22.2600, 113.9400),
}


# ── Parse the local mirrored markdown ────────────────────────

# Markdown table row: | <name> | <district> | <address> |
_ROW_RE = re.compile(
    r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
)

# Section header that tells us which region the next table belongs to
_REGION_HEADER_RE = re.compile(r"^#+\s*(港島區|九龍區|新界區)\s*$", re.MULTILINE)


def _parse_source_md(md_text: str) -> list[dict]:
    """Extract restaurants from the mirrored markdown file.

    The source structure is:
      ### <region>          ← region header
      | name | district | address |
      | name | district | address |
      | ... |
      ### <next region>
    We track the current region from the most recent header.
    """
    rows = []
    seen = set()
    current_region = None
    lines = md_text.splitlines()
    in_table = False
    for line in lines:
        # Region header?
        m = re.match(r"^#{1,6}\s*(港島區|九龍區|新界區)\s*$", line.strip())
        if m:
            current_region = m.group(1)
            in_table = False
            continue
        # Data row?
        m = _ROW_RE.match(line)
        if not m:
            in_table = False
            continue
        name, district, address = (g.strip() for g in m.groups())
        # Skip table header (Chinese "餐廳名稱" or English "Restaurant name")
        if name in ("餐廳名稱", "Restaurant name", "name", "Name", "---"):
            in_table = True
            continue
        if not name or "---" in name or "---" in district or set(district) <= {"-", " "}:
            continue
        # District must be in our known list
        if district not in DISTRICT_CENTROIDS:
            continue
        # Use the region header; if absent, fall back to district→region map
        if current_region:
            region = current_region
        else:
            region = DISTRICT_CENTROIDS[district][0]
        key = (name, address)
        if key in seen:
            continue
        seen.add(key)
        _, lat, lon = DISTRICT_CENTROIDS[district]
        rows.append({
            "name": name,
            "district": district,
            "region": region,
            "address": address,
            "lat": lat,
            "lon": lon,
            "source": f"petwellhk.com (mirrored {time.strftime('%Y-%m-%d')})",
        })
    # Stable IDs
    for i, r in enumerate(rows, 1):
        r["id"] = f"PR-{i:04d}"
    return rows


def _build_dataset(rows: list[dict], source_url: str) -> dict:
    return {
        "source_url": source_url,
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_announced": 1000,
        "available_in_dataset": len(rows),
        "note": ("Parsed from data/petrestaurants_source.md (mirrored from petwellhk.com). "
                 "The full 1,000 will appear as PetWell updates the page."),
        "districts": {k: {"region": v[0], "lat": v[1], "lon": v[2]}
                      for k, v in DISTRICT_CENTROIDS.items()},
        "restaurants": rows,
    }


def refresh() -> str:
    """Re-parse data/petrestaurants_source.md → rebuild petrestaurants.json."""
    if not SOURCE_MD.exists():
        return (f"⚠️ Source file missing: {SOURCE_MD}\n"
                f"   Mirror petwellhk.com via web_extract, then write the result to this path.")
    try:
        md_text = SOURCE_MD.read_text(encoding="utf-8")
    except Exception as e:
        return f"⚠️ Failed to read source: {e}"
    rows = _parse_source_md(md_text)
    if not rows:
        return (f"⚠️ Parsed 0 rows from {SOURCE_MD} ({len(md_text)} bytes).\n"
                f"   Markdown format may have changed. Inspect and fix _ROW_RE.")
    ds = _build_dataset(rows, SOURCE_URL)
    DATA_FILE.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")
    return (f"✓ Rebuilt dataset: {len(rows)} restaurants\n"
            f"   source_md: {SOURCE_MD}\n"
            f"   cache:     {DATA_FILE}")


def _ensure_fresh() -> tuple[dict, str]:
    """Load dataset, refreshing from local source.md if cache is stale or missing."""
    note = ""
    ds = None
    if DATA_FILE.exists():
        try:
            ds = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            ds = None
    if not ds:
        if SOURCE_MD.exists():
            # No cache yet — build it from source.md
            rows = _parse_source_md(SOURCE_MD.read_text(encoding="utf-8"))
            if rows:
                ds = _build_dataset(rows, SOURCE_URL)
                DATA_FILE.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")
                note = f" (built from {SOURCE_MD.name}: {len(rows)} rows)"
            else:
                note = f" ({SOURCE_MD.name} parsed 0 rows)"
        else:
            note = f" (no {SOURCE_MD.name} and no cache)"
    return ds or {"restaurants": [], "districts": {}}, note


def _load() -> dict:
    """Back-compat: ensure data is fresh, then return it."""
    ds, _note = _ensure_fresh()
    return ds


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
    ds, note = _ensure_fresh()
    matches = [r for r in ds["restaurants"] if r["district"] == district]
    if not matches:
        all_d = sorted({r["district"] for r in ds["restaurants"]})
        return (f"⚠️ No restaurants found in district '{district}'.\n"
                f"Available districts: {', '.join(all_d)}")
    out = [f"🐾 Found {len(matches)} pet-friendly restaurant(s) in {district}{note}:"]
    for r in matches[:20]:
        out.append(_format(r))
    if len(matches) > 20:
        out.append(f"  …and {len(matches) - 20} more")
    return "\n".join(out)


def search_by_region(region: str) -> str:
    """Filter by region: 港島區 / 九龍區 / 新界區."""
    ds, note = _ensure_fresh()
    matches = [r for r in ds["restaurants"] if r["region"] == region]
    if not matches:
        all_r = sorted({r["region"] for r in ds["restaurants"]})
        return (f"⚠️ No restaurants found in region '{region}'.\n"
                f"Available regions: {', '.join(all_r)}")
    out = [f"🐾 {len(matches)} pet-friendly restaurant(s) in {region}{note}:"]
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
    ds, note = _ensure_fresh()
    with_loc = [r for r in ds["restaurants"] if r.get("lat") is not None]
    scored = []
    for r in with_loc:
        dist = _haversine_km(lat, lon, r["lat"], r["lon"])
        scored.append((dist, r))
    scored.sort(key=lambda x: x[0])
    out = [f"🐾 {min(k, len(scored))} nearest pet-friendly restaurant(s) to "
           f"({lat:.4f}, {lon:.4f}){note}:"]
    for dist, r in scored[:k]:
        out.append(_format(r, {"distance_km": dist}))
    return "\n".join(out)


def search(query: str, region: Optional[str] = None,
           district: Optional[str] = None, k: int = 10) -> str:
    """Fuzzy name search with optional region/district filter."""
    ds, note = _ensure_fresh()
    q = query.lower()
    matches = [r for r in ds["restaurants"]
               if q in r["name"].lower()
               and (not region or r["region"] == region)
               and (not district or r["district"] == district)]
    if not matches:
        return f"⚠️ No matches for query '{query}'."
    out = [f"🐾 {len(matches)} match(es) for '{query}'{note}:"]
    for r in matches[:k]:
        out.append(_format(r))
    return "\n".join(out)


def stats() -> str:
    """Show counts per region and district + cache freshness."""
    ds, note = _ensure_fresh()
    rest = ds["restaurants"]
    by_region: dict[str, int] = {}
    by_dist: dict[str, int] = {}
    for r in rest:
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
        by_dist[r["district"]] = by_dist.get(r["district"], 0) + 1
    out = [
        f"🐾 Pet-friendly restaurants in dataset: {len(rest)}",
        f"   (FEHD announced {ds.get('total_announced', '?')}; "
        f"{len(rest)} available as of {ds.get('scraped_at', '?')})",
        f"   source: {ds.get('source_url', '?')}",
        f"   parse source: data/petrestaurants_source.md{note}",
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
    if action == "refresh":
        return refresh()
    return (f"Error: unknown action '{action}'. "
            f"Use: search_by_district, search_by_region, nearest, search, stats, refresh")


TOOL_DEF = {
    "name": "petrestaurants",
    "description": (
        "Query the Hong Kong FEHD 2026-06-12 pet-friendly restaurant lottery result "
        "(sourced from petwellhk.com). Reads from local mirror data/petrestaurants_source.md "
        "(because petwellhk.com is a Next.js SPA that stdlib urllib cannot parse). "
        "Actions: search_by_district, search_by_region, nearest (lat/lon, great-circle "
        "distance), search (name fuzzy), stats, refresh."
    ),
    "handler": _dispatch,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search_by_district", "search_by_region", "nearest",
                         "search", "stats", "refresh"],
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
