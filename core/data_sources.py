"""BAW — Default Data Source Registry.

Single source of truth for "what's the best default for X data type".

The 2026-06-12 sub-agent defaulted to **Google Places** for restaurant
data and got stuck on a fake API key. The systemic fix isn't "remember
to use OSM" — it's "BAW has a registry of preferred defaults, ordered
by **free + no-key + open data** first, and sub-agents consult it
before reaching for paid services."

Selection criteria (in order):
  1. **Free** — no paid tier, no metered billing.
  2. **No API key** — no signup, no env var, no risk of "key invalid".
  3. **Open data** — community-maintained, has fallback if it goes down.
  4. **Stdlib-friendly** — works with `urllib` (no `requests` install).
  5. **Reasonable coverage** for the most common use cases.

This module is **read at agent boot** and a summary is injected into
the system prompt, so sub-agents see the defaults before they reach
for a paid service.

When a new data type is added:
  1. Add an entry below.
  2. Document the "why this default" in 1-2 lines.
  3. If the default requires a non-stdlib library, document the pip
     install in a comment.
  4. Update ``core/loop.py`` ``_build_defaults_block()`` if a new
     category needs to be visible to sub-agents.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DataSource:
    """One default data source entry."""
    category: str                 # e.g. "restaurants"
    name: str                     # short name, e.g. "OSM Overpass"
    endpoint: str                 # base URL
    auth_required: bool           # False = free, no key
    method: str                   # "GET" | "POST"
    cost: str                     # "free" | "freemium" | "paid"
    coverage: str                 # one-line "good for X, weak for Y"
    fallback: Optional[str] = None  # name of a secondary source
    requires_lib: str = "stdlib"  # "stdlib" | "requests" | "bs4" | etc.
    example_call: str = ""        # 1-line call example
    notes: str = ""


# ── Registry ────────────────────────────────────────────────

REGISTRY: Dict[str, DataSource] = {
    "restaurants": DataSource(
        category="restaurants",
        name="OSM Overpass",
        endpoint="https://overpass-api.de/api/interpreter",
        auth_required=False,
        method="POST",
        cost="free",
        coverage="HK chains and shopping-mall food courts good; tiny dai pai dong weak",
        fallback="Nominatim (for search by name) + Google Places (paid, opt-in)",
        requires_lib="stdlib",
        example_call='http_post(overpass_url, data="[out:json];node[amenity=restaurant]...")',
        notes="See tools/restaurant.py — used by `baw restaurant search`.",
    ),
    "geocoding": DataSource(
        category="geocoding",
        name="OSM Nominatim",
        endpoint="https://nominatim.openstreetmap.org/search",
        auth_required=False,
        method="GET",
        cost="free",
        coverage="Worldwide; HK excellent; respect 1 req/s rate limit",
        fallback="Photon (komoot), or paid Google Geocoding (opt-in)",
        requires_lib="stdlib",
        example_call='http_get("https://nominatim.openstreetmap.org/search?q=銅鑼灣&format=json")',
        notes="Add `User-Agent: BAW/1.0` header (Nominatim ToS).",
    ),
    "weather": DataSource(
        category="weather",
        name="Open-Meteo",
        endpoint="https://api.open-meteo.com/v1/forecast",
        auth_required=False,
        method="GET",
        cost="free",
        coverage="Worldwide; HK 1-km resolution; no rate limit for non-commercial",
        fallback="Hong Kong Observatory (HKO) public feed (text/JSON)",
        requires_lib="stdlib",
        example_call='http_get("https://api.open-meteo.com/v1/forecast?latitude=22.28&longitude=114.17&current=temperature_2m")',
        notes="No key. CC-BY 4.0. 11,000 reqs/day free.",
    ),
    "transit_hk": DataSource(
        category="transit_hk",
        name="MTR Live API",
        endpoint="https://opendata.mtr.com.hk/",  # limited; not always public
        auth_required=True,
        method="GET",
        cost="freemium",
        coverage="MTR lines only; KMB / Citybus / GMB require separate feeds",
        fallback="Citybus / KMB open data (CSV/JSON, no key, low rate)",
        requires_lib="stdlib",
        example_call="(no stable public API; use scraped schedules)",
        notes="MTR doesn't publish a stable free public API for live arrivals as of 2026. "
              "KMB and Citybus publish GTFS feeds (no key).",
    ),
    "search_web": DataSource(
        category="search_web",
        name="DuckDuckGo (via core.search)",
        endpoint="https://duckduckgo.com/html/",
        auth_required=False,
        method="GET",
        cost="free",
        coverage="Good general web; no rate limit for moderate use",
        fallback="Brave Search (free, key optional), Serper (paid)",
        requires_lib="requests + bs4",
        example_call='web_search("HK weather tomorrow", limit=5)',
        notes="Default in `baw web_search`. See tools/web_search.py.",
    ),
    "exchange_rates": DataSource(
        category="exchange_rates",
        name="exchangerate.host",
        endpoint="https://api.exchangerate.host/latest",
        auth_required=False,
        method="GET",
        cost="free",
        coverage="160+ currencies; daily refresh",
        fallback="Open Exchange Rates (free tier: 1000 req/mo, key required)",
        requires_lib="stdlib",
        example_call='http_get("https://api.exchangerate.host/latest?base=USD&symbols=HKD")',
        notes="No key. Use as the default for currency conversion.",
    ),
    "wikipedia": DataSource(
        category="wikipedia",
        name="Wikipedia REST API",
        endpoint="https://en.wikipedia.org/api/rest_v1/",
        auth_required=False,
        method="GET",
        cost="free",
        coverage="All Wikipedia languages; CC-BY-SA",
        fallback="Wikidata SPARQL (paid: complex query syntax)",
        requires_lib="stdlib",
        example_call='http_get("https://en.wikipedia.org/api/rest_v1/page/summary/Hong_Kong")',
        notes="No key. Required `User-Agent: BAW/1.0` header.",
    ),
    "address_geocode_zh": DataSource(
        category="address_geocode_zh",
        name="HK GeoData Search (landsd)",
        endpoint="https://geodata.gov.hk/gs/api/v1.0.0/locations/search",
        auth_required=False,
        method="GET",
        cost="free",
        coverage="HK addresses, buildings, POI (Chinese + English)",
        fallback="Nominatim (less HK-specific)",
        requires_lib="stdlib",
        example_call='http_get("https://geodata.gov.hk/gs/api/v1.0.0/locations/search?q=銅鑼灣崇光百貨")',
        notes="No key. HK Government open data.",
    ),
}


# ── Public helpers ──────────────────────────────────────────

def get(category: str) -> Optional[DataSource]:
    """Look up the preferred default for a data category."""
    return REGISTRY.get(category)


def all_categories() -> List[str]:
    """List all registered categories (sorted)."""
    return sorted(REGISTRY.keys())


def free_sources() -> List[DataSource]:
    """All sources that are free + no auth + stdlib — the systemic default."""
    return [s for s in REGISTRY.values()
            if s.cost == "free" and not s.auth_required and s.requires_lib == "stdlib"]


def summary_block() -> str:
    """Human-readable summary, injected into system prompt via core/loop.py.

    Lists only the free + no-key + stdlib sources so sub-agents have a
    short, actionable list of "what to reach for first".
    """
    lines = ["## BAW Default Data Sources (free + no-key + stdlib first)"]
    for cat in all_categories():
        s = REGISTRY[cat]
        tag = "✓" if (s.cost == "free" and not s.auth_required) else "$"
        lines.append(
            f"- {tag} **{cat}** → `{s.name}` ({s.endpoint}) — {s.coverage}"
        )
    lines.append("")
    lines.append("Rule: prefer a free entry above before reaching for a paid service.")
    lines.append("If the user asks for a data type not in this list, ADD a new entry here")
    lines.append("before writing the tool. See `core/data_sources.py` for the schema.")
    return "\n".join(lines)


# ── Validation ──────────────────────────────────────────────

def validate() -> List[str]:
    """Run by `baw self-test`. Returns list of warnings (empty = OK)."""
    warnings: List[str] = []
    if not REGISTRY:
        warnings.append("REGISTRY is empty — sub-agents have no defaults to consult")
    free = free_sources()
    if len(free) < 3:
        warnings.append(
            f"Only {len(free)} free+no-key+stdlib sources registered. "
            f"Sub-agents will default to paid services more often."
        )
    for cat, s in REGISTRY.items():
        if not s.endpoint.startswith(("http://", "https://")):
            warnings.append(f"{cat}: endpoint {s.endpoint!r} is not a URL")
        if not s.coverage:
            warnings.append(f"{cat}: missing coverage description")
    return warnings
