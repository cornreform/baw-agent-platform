"""baw self-test — exercise the SELF_BUILD_RECIPE end-to-end.

Fetches a known-good public URL, parses it, stores a dataset, and
reports whether every layer is healthy. Designed so BAW can run it
silently after a self-build task to confirm its own work.

Usage:
  baw self-test                       # default sample URL
  baw self-test --url <url>           # custom test URL
  baw self-test --paths-only          # just check path resolution
  baw self-test --no-fetch            # skip network, only check internals

Exit codes:
  0  — all checks passed
  1  — one or more checks failed (details printed)
  2  — fatal: path resolution itself broken (BAW can't find its own files)
"""
from __future__ import annotations
import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

from core.paths import self_check, data_dir, repo_root, runtime_home, ensure_data_file
from cli import console
from rich.panel import Panel
from rich.table import Table

# A tiny, stable, public URL that we use to prove HTTP fetching works.
# Wikipedia's REST API summary endpoint returns JSON — small, fast, no JS.
DEFAULT_SAMPLE_URL = (
    "https://en.wikipedia.org/api/rest_v1/page/summary/Hong_Kong"
)
DEFAULT_EXPECT_KEY = "title"  # JSON field that must appear in the response


def _http_get(url: str, timeout: int = 15) -> tuple[bool, str, int]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BAW/1.0 (+self-test)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return True, body, r.status
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}", e.code
    except urllib.error.URLError as e:
        return False, f"URL error: {e.reason}", 0
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0


def _paths_check() -> tuple[bool, list[str]]:
    report = self_check()
    lines = []
    ok = report["all_present"]
    for label, c in report["checks"].items():
        mark = "✓" if c["exists"] else "✗"
        lines.append(f"  {mark} {label}: {c['path']}")
    return ok, lines


def _fetch_check(url: str) -> tuple[bool, list[str]]:
    lines = [f"  GET {url}"]
    ok, body, status = _http_get(url)
    if not ok:
        lines.append(f"  ✗ fetch failed: {body}")
        return False, lines
    lines.append(f"  ✓ HTTP {status}, {len(body)} bytes")
    # Try to parse as JSON
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        lines.append(f"  ✗ response is not JSON: {e}")
        return False, lines
    # Check expected field
    if DEFAULT_EXPECT_KEY not in data:
        lines.append(f"  ✗ missing expected field '{DEFAULT_EXPECT_KEY}'")
        return False, lines
    val = str(data[DEFAULT_EXPECT_KEY])[:60]
    lines.append(f"  ✓ parsed JSON, {DEFAULT_EXPECT_KEY} = {val!r}")
    return True, lines


def _store_check(url: str, body: str) -> tuple[bool, list[str]]:
    """Persist a tiny dataset to prove data_dir() works end-to-end."""
    lines = []
    try:
        data = json.loads(body)
    except Exception:
        return False, lines + ["  ✗ body not JSON, store check skipped"]

    sample = {
        "source_url": url,
        "scraped_at": "2026-06-12",
        "schema": {"fields": list(data.keys())[:5]},
        "items": [data],  # 1 record is enough to prove write works
    }
    out_path = ensure_data_file("self_test_sample.json")
    out_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2))
    size = out_path.stat().st_size
    lines.append(f"  ✓ wrote {out_path} ({size} bytes)")

    # Read it back
    rt = json.loads(out_path.read_text())
    assert rt["items"][0][DEFAULT_EXPECT_KEY] == data[DEFAULT_EXPECT_KEY]
    lines.append(f"  ✓ read-back OK")
    return True, lines


def _register_check() -> tuple[bool, list[str]]:
    """Prove BAW's tool registry is wired up correctly."""
    lines = []
    try:
        import tools  # noqa: F401
        from tools import register_all
        from core.tools import list_tools
        register_all()
        ts = list_tools()
        names = sorted(t.name for t in ts)
        lines.append(f"  ✓ {len(names)} tools registered: {', '.join(names)}")
        return True, lines
    except Exception as e:
        lines.append(f"  ✗ register_all() failed: {type(e).__name__}: {e}")
        return False, lines


def main(argv=None):
    p = argparse.ArgumentParser(prog="baw self-test",
                                description="End-to-end smoke test of BAW's self-build pipeline.")
    p.add_argument("--url", default=DEFAULT_SAMPLE_URL)
    p.add_argument("--paths-only", action="store_true",
                   help="Only check that BAW can locate its own files.")
    p.add_argument("--no-fetch", action="store_true",
                   help="Skip the network check.")
    args = p.parse_args(argv)

    sections: list[tuple[str, bool, list[str]]] = []

    # 1. Paths
    ok, lines = _paths_check()
    sections.append(("Path resolution (core.paths)", ok, lines))
    if not ok:
        # Bail — nothing else will work
        console.print(Panel("\n".join(["✗ PATH RESOLUTION BROKEN — BAW can't find its own files.",
                                       *lines]),
                            title="baw self-test", border_style="red"))
        sys.exit(2)

    if args.paths_only:
        console.print(Panel("\n".join(lines), title="baw self-test — paths", border_style="green"))
        sys.exit(0)

    # 2. Tool registration
    ok, lines = _register_check()
    sections.append(("Tool registry (register_all)", ok, lines))

    # 3. HTTP fetch
    if not args.no_fetch:
        ok, lines = _fetch_check(args.url)
        sections.append((f"HTTP fetch ({args.url[:60]}...)", ok, lines))
        # 4. Store (only if fetch OK)
        if ok:
            ok, body, _ = _http_get(args.url)
            if ok:
                ok, lines = _store_check(args.url, body)
                sections.append(("Dataset write (data_dir)", ok, lines))
        else:
            sections.append(("Dataset write (data_dir)", False,
                             ["  ⤷ skipped because fetch failed"]))

    # ── Render ─────────────────────────────────────────────────
    all_ok = all(ok for _, ok, _ in sections)
    color = "green" if all_ok else "red"
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Check", style="white")
    table.add_column("Status", width=6)
    for title, ok, _ in sections:
        mark = "✓ PASS" if ok else "✗ FAIL"
        style = "green" if ok else "red"
        table.add_row(title, f"[{style}]{mark}[/{style}]")
    console.print(table)
    for title, ok, lines in sections:
        console.print(f"\n[bold]{title}[/bold]")
        console.print("\n".join(lines))

    msg = "ALL CHECKS PASSED — BAW can self-build" if all_ok else "ONE OR MORE CHECKS FAILED"
    console.print(Panel(msg, title="baw self-test", border_style=color))
    sys.exit(0 if all_ok else 1)
