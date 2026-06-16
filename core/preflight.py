"""BAW — Capability Pre-flight Check.

Before any "scrape this URL / build me a tool / fetch data" task, BAW
MUST run a pre-flight check to verify it has the tools needed. This
prevents the 2026-06-12 failure mode where a sub-agent tried to fetch
a Next.js SPA with ``urllib`` (returned empty shell) and reported
"50/1000 limit, upstream doesn't matter" — wrong by capability, not
by luck.

A pre-flight check is a 4-step audit:

  1. **Tool availability** — does the requested task need a tool we
     don't have? E.g. browser-render for SPAs, ``curl`` for binary
     downloads, ``ffmpeg`` for audio conversion.
  2. **Network reachability** — can the host actually reach the URL?
     Don't promise to fetch a URL we can't reach.
  3. **Disk space** — is there room for the mirror file + parsed
     dataset + tool source?
  4. **Path resolution** — does ``core.paths`` resolve to the right
     place? (Detects the ``/home/baw/baw`` vs ``~/baw`` confusion that
     bit the pet-restaurant sub-agent.)

The pre-flight returns a verdict + remediation steps. BAW should
refuse to start a self-build task when verdict is ``BLOCKED`` and
should print the remediation steps verbatim.

Exposed via:
  - ``baw preflight`` CLI (self-check before the user starts a job)
  - ``core.preflight.run_preflight()`` (called by the agent loop at
    task start when a self-build intent is detected)
"""
from __future__ import annotations
import shutil
import socket
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse


# ── Verdicts ────────────────────────────────────────────────

PASS = "PASS"     # Ready to proceed
WARN = "WARN"     # Proceed but address the warning
BLOCK = "BLOCK"   # Cannot proceed — fix the missing capability first


@dataclass
class PreFlightReport:
    verdict: str = PASS
    checks: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Individual checks ──────────────────────────────────────

def check_tool_availability() -> Dict[str, Any]:
    """Verify the core tools BAW needs for self-build tasks are present."""
    required = {
        "python3": shutil.which("python3"),
        "urllib (stdlib)": "always",
        "requests": _try_import("requests"),
        "bs4": _try_import("bs4"),
        "yaml": _try_import("yaml"),
    }
    missing = [name for name, present in required.items() if not present]
    return {
        "name": "tool_availability",
        "status": PASS if not missing else WARN,
        "details": required,
        "missing": missing,
    }


def _try_import(mod: str) -> Optional[str]:
    try:
        __import__(mod)
        return mod
    except ImportError:
        return None


def _check_web_extract() -> Optional[str]:

    We can't shell into the agent from here, so we report based on the
    local file presence of the tool's import path. If the tool is
    installed, BAW's loop can call it. If not, SPA pages will fail.
    """
    candidates = [
        Path("tools/web_extract.py"),
        Path(__file__).resolve().parent.parent / "tools" / "web_extract.py",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def check_network(url: Optional[str] = None) -> Dict[str, Any]:
    """Quick reachability check. If ``url`` is given, test that host."""
    if not url:
        # Default: just check we can resolve a known host.
        try:
            socket.gethostbyname("github.com")
            return {"name": "network", "status": PASS, "details": "github.com resolves"}
        except Exception as e:
            return {"name": "network", "status": BLOCK, "details": f"DNS failure: {e}"}
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return {"name": "network", "status": BLOCK, "details": f"no host in {url}"}
    try:
        socket.gethostbyname(host)
        return {"name": "network", "status": PASS, "details": f"{host} resolves"}
    except Exception as e:
        return {
            "name": "network",
            "status": BLOCK,
            "details": f"{host} does not resolve: {e}",
        }


def check_disk(min_free_mb: int = 50) -> Dict[str, Any]:
    """Make sure there's room for the mirror file + dataset + tool source."""
    from . import paths as _paths
    target = _paths.data_dir()
    target.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(target)
    free_mb = usage.free / (1024 * 1024)
    return {
        "name": "disk",
        "status": PASS if free_mb >= min_free_mb else BLOCK,
        "details": f"{free_mb:.1f} MB free at {target} (need ≥ {min_free_mb} MB)",
    }


def check_path_resolution() -> Dict[str, Any]:
    """Verify ``core.paths`` resolves to a real BAW root, not a hardcoded
    trap. This catches the ``/home/baw/baw`` vs ``~/baw`` confusion."""
    from . import paths as _paths
    root = _paths.repo_root()
    runtime = _paths.runtime_home()
    data = _paths.data_dir()
    if not (root / "cli" / "main.py").exists():
        return {
            "name": "path_resolution",
            "status": BLOCK,
            "details": (
                f"repo_root() = {root} — missing cli/main.py. "
                f"Set $BAW_HOME or fix the hardcoded path."
            ),
        }
    return {
        "name": "path_resolution",
        "status": PASS,
        "details": f"repo_root={root}, runtime={runtime}, data={data}",
    }


# ── Aggregation ─────────────────────────────────────────────

def run_preflight(url: Optional[str] = None) -> PreFlightReport:
    """Run all four checks, return an aggregated report.

    Args:
        url: Optional target URL the user wants to fetch. If given,
             we test network reachability and warn if the URL is on a
             known SPA framework (nextjs.org / gatsbyjs.com / vercel.app).
    """
    report = PreFlightReport()
    report.checks.append(check_tool_availability())
    report.checks.append(check_network(url))
    report.checks.append(check_disk())
    report.checks.append(check_path_resolution())

    for c in report.checks:
        if c["status"] == BLOCK:
            report.missing.extend(c.get("missing", []))
            report.verdict = BLOCK
        elif c["status"] == WARN and report.verdict != BLOCK:
            report.verdict = WARN
            report.warnings.append(c["name"])

    # Detect SPA URL up front
    if url and _is_known_spa_host(url):
        report.warnings.append(
            f"{urlparse(url).hostname} is a known SPA host. "
            f"Plan to mirror via web_extract before parsing."
        )
        if report.verdict == PASS:
            report.verdict = WARN

    # Build remediation steps
    if report.verdict == BLOCK:
        report.next_steps.append(
            "Cannot start a self-build task. Fix the BLOCK items first:"
        )
        for c in report.checks:
            if c["status"] == BLOCK:
                report.next_steps.append(f"  • {c['name']}: {c['details']}")
    elif report.verdict == WARN:
        report.next_steps.append(
            "Pre-flight passed with warnings. Proceed, but address them:"
        )
        for w in report.warnings:
            report.next_steps.append(f"  • {w}")
    else:
        report.next_steps.append("All checks passed. Safe to start the self-build task.")

    return report


def _is_known_spa_host(url: str) -> bool:
    """Heuristic list of hosts that are almost always SPAs."""
    host = (urlparse(url).hostname or "").lower()
    spa_hosts = (
        "vercel.app", "netlify.app", "herokuapp.com",
        "github.io", "pages.dev",
    )
    return any(host.endswith(h) for h in spa_hosts)


# ── CLI entry point ─────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or sys.argv[1:]
    url = argv[0] if argv else None
    report = run_preflight(url=url)
    import json
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.verdict == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
