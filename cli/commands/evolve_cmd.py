"""baw evolve — Self-evolution engine CLI.

Runs the BAW self-evolution pipeline:
  analyze     — Scan behavior logs for patterns
  optimize    — Apply auto-optimizations (dry-run by default)
  stats       — Show evolution summary
"""
from __future__ import annotations
import sys
from pathlib import Path

# Ensure BAW root is on path before any imports
_APP_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_APP_ROOT))

# Import console safely (may not be available when run standalone)
try:
    from cli import console
except Exception:
    console = None


def main(argv: list[str] | None = None):
    args = argv or sys.argv[1:]
    subcommand = args[0] if args else "stats"

    from core.evolve import analyze, auto_optimize, get_evolve_stats, flush_behavior

    def _print(msg: str):
        if console:
            console.print(msg)
        else:
            print(msg)

    if subcommand == "analyze":
        hours = 168
        if len(args) > 1:
            try:
                hours = int(args[1])
            except ValueError:
                pass
        flush_behavior()
        result = analyze(hours_back=hours)
        _print(f"[baw.gold]Evolution Analysis ({result['period_hours']}h)[/baw.gold]")
        _print(f"  Events: {result['total_entries']}")
        _print(f"  Tool calls: {result['tool_calls']}")
        _print(f"  Success rate: {result['success_rate']}%")
        _print(f"  Corrections: {result['corrections']}")
        _print(f"  Patterns: {len(result['recommendations'])}")
        for r in result['recommendations']:
            _print(f"  • [{r['type']}] {r.get('tool', '')}: {r['suggestion']}")

    elif subcommand == "optimize":
        dry_run = "--apply" not in args
        flush_behavior()
        result = auto_optimize(dry_run=dry_run)
        mode = "DRY-RUN" if dry_run else "APPLY"
        _print(f"[baw.gold]Auto-Optimize ({mode})[/baw.gold]")
        _print(f"  Patterns found: {result['patterns_found']}")
        _print(f"  Queued: {result.get('queued_count', 0)}")
        _print(f"  Soul patched: {result['soul_patched']}")
        _print(f"  Config patched: {result['config_patched']}")
        if result.get('snapshot'):
            snap = result['snapshot']
            _print(f"  Git snapshot: {snap['commit'][:8] if snap.get('commit') else 'FAILED'} {'✓' if snap.get('ok') else '✗'}")
        if result.get('rolled_back'):
            _print(f"  [baw.red]ROLLED BACK[/baw.red] due to verify errors")
        for p in result['patches']:
            _print(f"  • {p}")
        if dry_run:
            _print("[baw.dim]  Run `baw evolve pending` to see queued items[/baw.dim]")

    elif subcommand == "pending":
        from core.evolve import get_pending_approvals
        pending = get_pending_approvals()
        if not pending:
            _print("📜 No pending approvals")
        else:
            _print(f"[baw.gold]{len(pending)} Pending Approvals:[/baw.gold]")
            for i, p in enumerate(pending):
                _print(f"  [{i}] [{p.get('type', '')}] {p.get('suggestion', '')[:80]}")
            _print("[baw.dim]  Run `baw evolve approve <index>` to apply[/baw.dim]")

    elif subcommand == "approve":
        from core.evolve import approve_pending
        idx = 0
        if len(args) > 1:
            try:
                idx = int(args[1])
            except ValueError:
                _print("[baw.error]Usage:[/baw.error] baw evolve approve <index>")
                sys.exit(1)
        res = approve_pending(idx, approved=True)
        if res["ok"]:
            _print(f"[baw.gold]✓ Approved and {res['action']}[/baw.gold]")
        else:
            _print(f"[baw.error]✗ Failed:[/baw.error] {res.get('error', '')}")

    elif subcommand == "reject":
        from core.evolve import approve_pending
        idx = 0
        if len(args) > 1:
            try:
                idx = int(args[1])
            except ValueError:
                _print("[baw.error]Usage:[/baw.error] baw evolve reject <index>")
                sys.exit(1)
        res = approve_pending(idx, approved=False)
        if res["ok"]:
            _print(f"[baw.gold]✓ Rejected[/baw.gold]")
        else:
            _print(f"[baw.error]✗ Failed:[/baw.error] {res.get('error', '')}")

    elif subcommand == "stats":
        from core.evolve import get_learned_lessons_summary
        _print(get_evolve_stats())
        _print(get_learned_lessons_summary())

    elif subcommand == "lessons":
        from core.evolve import get_learned_lessons_summary
        _print(get_learned_lessons_summary())

    else:
        _print("[baw.error]Unknown subcommand:[/baw.error] {subcommand}")
        _print("Usage: baw evolve [analyze|optimize|stats|pending|approve|reject|lessons]")
        sys.exit(1)
