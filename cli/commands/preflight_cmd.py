"""CLI: ``baw preflight [--url URL]`` — capability pre-flight check.

Runs the four checks defined in ``core.preflight`` and prints a JSON
report. Exits 0 on PASS/WARN, 1 on BLOCK.

Examples:
  baw preflight                            # generic capability check
  baw preflight --url https://example.com  # targeted check for a URL
"""
from __future__ import annotations
import argparse
import json
import sys
from typing import List, Optional

# Allow running as `python cli/commands/preflight_cmd.py` directly
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.preflight import run_preflight  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="baw preflight",
        description=(
            "Step 0 of SELF_BUILD_RECIPE. Verifies BAW has the tools, network, "
            "disk, and path resolution it needs to start a 'scrape / build me "
            "a tool' task. Warns about known SPA hosts. Refuses to start "
            "(exits 1) when any check is BLOCKED."
        ),
    )
    p.add_argument(
        "--url",
        default=None,
        help="Target URL the user wants to fetch. If given, we test "
             "network reachability and warn if the host is a known SPA platform.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print only the JSON report (no human-readable verdict block).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    report = run_preflight(url=args.url)

    payload = report.to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if not args.json:
        print()
        print(f"VERDICT: {report.verdict}")
        for step in report.next_steps:
            print(step)

    return 0 if report.verdict != "BLOCK" else 1


if __name__ == "__main__":
    sys.exit(main())
