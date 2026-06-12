"""CLI: ``baw restaurant [...]`` — find restaurants via OpenStreetMap.

Examples:
  baw restaurant search                           # Causeway Bay 1km box
  baw restaurant search --lat 22.28 --lon 114.17 --max-km 1.5
  baw restaurant search --cuisine japanese
  baw restaurant search --query "喜記"
  baw restaurant search --amenity cafe
  baw restaurant search --lat 22.28 --lon 114.17 --pet-friendly
  baw restaurant search --json                    # raw JSON output
"""
from __future__ import annotations
import argparse
import json
import sys
from typing import List, Optional

import os
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.restaurant import search  # noqa: E402


def _format_row(r: dict, *, show_distance: bool = True) -> str:
    name = r.get("name") or "?"
    cuisine = r.get("cuisine") or "-"
    amenity = r.get("amenity") or "-"
    addr = r.get("addr") or ""
    parts = [f"  • {name}"]
    sub = [f"cuisine={cuisine}", f"amenity={amenity}"]
    if r.get("pet_friendly"):
        sub.append("🐾 pet_friendly")
    parts.append(f"      {', '.join(sub)}")
    if addr:
        parts.append(f"      {addr}")
    parts.append(f"      ({r['lat']:.5f}, {r['lon']:.5f})")
    return "\n".join(parts)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="baw restaurant",
        description="Find restaurants via OpenStreetMap Overpass (free, no key).")
    p.add_argument("action", choices=["search", "stats"], default="search", nargs="?")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"),
                   help="Bounding box (south west north east).")
    p.add_argument("--lat", type=float, help="Latitude for radius / nearest search.")
    p.add_argument("--lon", type=float, help="Longitude for radius / nearest search.")
    p.add_argument("--k", type=int, default=10, help="How many nearest to return.")
    p.add_argument("--max-km", type=float, help="Radius in km from lat/lon.")
    p.add_argument("--cuisine", help="OSM cuisine tag, e.g. japanese, pizza, ramen.")
    p.add_argument("--amenity", default="restaurant",
                   help="OSM amenity: restaurant|cafe|fast_food|food_court|pub|ice_cream|bakery.")
    p.add_argument("--query", help="Name fuzzy (Chinese + English).")
    p.add_argument("--pet-friendly", action="store_true",
                   help="Intersect with FEHD 50-restaurant pet-friendly dataset.")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true", help="Raw JSON output.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)

    bbox = tuple(args.bbox) if args.bbox else None
    result = search(
        bbox=bbox,
        lat=args.lat,
        lon=args.lon,
        k=args.k,
        max_km=args.max_km,
        cuisine=args.cuisine,
        amenity=args.amenity,
        query=args.query,
        pet_friendly=args.pet_friendly,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("source") != "error" else 1

    if result.get("source") == "error":
        print(f"❌ Overpass error: {result.get('error')}")
        return 1

    results = result["results"]
    print(f"🍴 Found {len(results)} restaurant(s) via {result['source']} "
          f"(amenity={args.amenity}"
          + (f", cuisine={args.cuisine}" if args.cuisine else "")
          + (f", query='{args.query}'" if args.query else "")
          + (f", max_km={args.max_km}" if args.max_km else "")
          + "):")
    for r in results[:args.k]:
        print(_format_row(r))
    for w in result.get("warnings", []):
        print(f"  ⚠️  {w}")
    if len(results) > args.k:
        print(f"  …and {len(results) - args.k} more (use --k or --json to see all)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
