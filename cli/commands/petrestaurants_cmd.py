"""baw petrestaurants — query HK FEHD pet-friendly restaurant list.

Built 2026-06-12 as proof of SELF_BUILD_RECIPE end-to-end.

Subcommands:
  list                       Show all restaurants in dataset
  stats                      Counts per region / district
  district <name>            Filter by district (e.g. 灣仔, 油尖區)
  region <name>              Filter by region (港島區/九龍區/新界區)
  nearest <lat> <lon> [k]    Sort by distance from a point (default k=10)
  search <query>             Fuzzy name search
"""
import argparse
import sys
from cli import console
from rich.panel import Panel

from tools.petrestaurants import (
    stats, search_by_district, search_by_region, nearest, search,
    _load,
)


def cmd_list(args):
    d = _load()
    rest = d["restaurants"]
    console.print(Panel(
        f"🐾 {len(rest)} pet-friendly restaurants in dataset\n"
        f"   (FEHD announced {d.get('total_announced', '?')}; "
        f"only {len(rest)} published as of {d.get('scraped_at', '?')})",
        title="baw petrestaurants list", border_style="magenta"))


def cmd_stats(args):
    console.print(stats())


def cmd_district(args):
    console.print(search_by_district(args.name))


def cmd_region(args):
    console.print(search_by_region(args.name))


def cmd_nearest(args):
    try:
        lat, lon = float(args.lat), float(args.lon)
    except ValueError:
        console.print(f"[red]lat/lon must be numbers[/red]")
        sys.exit(1)
    console.print(nearest(lat, lon, k=args.k))


def cmd_search(args):
    console.print(search(args.query, region=args.region, district=args.district, k=args.k))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="baw petrestaurants",
                                description="Query the HK FEHD pet-friendly restaurant list.")
    sub = p.add_subparsers(dest="subcommand", required=True)

    sp = sub.add_parser("list", help="Show all restaurants in dataset")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("stats", help="Counts per region / district")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("district", help="Filter by district")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_district)

    sp = sub.add_parser("region", help="Filter by region")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_region)

    sp = sub.add_parser("nearest", help="Sort by distance from a point")
    sp.add_argument("lat")
    sp.add_argument("lon")
    sp.add_argument("--k", type=int, default=10)
    sp.set_defaults(func=cmd_nearest)

    sp = sub.add_parser("search", help="Fuzzy name search")
    sp.add_argument("query")
    sp.add_argument("--region")
    sp.add_argument("--district")
    sp.add_argument("--k", type=int, default=10)
    sp.set_defaults(func=cmd_search)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
