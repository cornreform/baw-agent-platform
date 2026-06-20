"""BAW Self-Harness Evaluation — weekly autonomous improvement loop.

Runs every Sunday 05:00.  Scans codebase, runs diagnostics, identifies
harness-level improvement opportunities, and proposes changes.

The goal: BAW improves its own prompts, tools, skills, and architecture
without waiting for a human to steer it.
"""

import json
import os
import sys
from pathlib import Path

BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))

sys.path.insert(0, str(BAW_HOME))


def run_step(name: str, fn) -> dict:
    """Run a step and return result dict."""
    try:
        result = fn()
        return {"step": name, "ok": True, "result": result}
    except Exception as e:
        return {"step": name, "ok": False, "error": str(e)}


def scan_codebase() -> str:
    """Run codebase_doc scan."""
    from tools.codebase_doc import scan_ast, gen_report, INDEX_FILE
    scan_ast()
    report = gen_report()
    idx_exists = INDEX_FILE.exists()
    return f"Modules scanned: {report.get('total_modules', 0)}, INDEX.md: {'yes' if idx_exists else 'no'}"


def run_diagnose() -> str:
    """Run self_diagnose."""
    from tools.self_diagnose import all_checks
    results = all_checks()
    ok_count = sum(1 for r in results.values() if r.get("ok"))
    total = len(results)
    return f"Health: {ok_count}/{total} checks passed"


def check_harness_layers() -> list[dict]:
    """Identify harness-layer improvement opportunities.

    Harness layers:
    - Memory: kg_curator, memory_quality, session_synthesis, weighted retrieval
    - Focus: auto-steer, tool limits, dead-end detection
    - Format: output validation, HTML sanitization, token display
    - Cron: self-maintenance, error detection, delivery
    - Codebase: INDEX.md, self-documenting code, AI-maintainable design
    - Natural language: hardcoded keywords, rigid routing
    """
    findings = []

    # Check for any remaining hardcoded patterns
    for py_file in (BAW_HOME / "core").rglob("*.py"):
        content = py_file.read_text(encoding="utf-8", errors="replace")

        # Flag: keyword lists used for routing/classification
        if "keyword" in content.lower() and "task_rules" in content:
            findings.append({
                "area": "natural-language",
                "file": str(py_file.relative_to(BAW_HOME)),
                "issue": "Remaining keyword-based routing",
                "severity": "medium",
            })

        # Flag: excessive hardcoded patterns/lists
        if "re.search" in content or "re.compile" in content:
            findings.append({
                "area": "natural-language",
                "file": str(py_file.relative_to(BAW_HOME)),
                "issue": "Regex pattern matching — consider LLM-driven alternative",
                "severity": "info",
            })

    # Check SOUL.md size (inflated SOUL degrades LLM performance)
    soul_path = BAW_DATA / "SOUL.md"
    if soul_path.exists():
        soul_size = len(soul_path.read_text(encoding="utf-8"))
        if soul_size > 20000:
            findings.append({
                "area": "format",
                "file": "SOUL.md",
                "issue": f"SOUL.md is {soul_size} chars — consider pruning deprecated rules",
                "severity": "medium",
            })

    # Check memory store size
    mem_store = BAW_DATA / "memory" / "store.jsonl"
    if mem_store.exists():
        line_count = len(mem_store.read_text(encoding="utf-8").splitlines())
        if line_count > 2000:
            findings.append({
                "area": "memory",
                "file": "store.jsonl",
                "issue": f"Memory store has {line_count} entries — consider decay/consolidation",
                "severity": "low",
            })

    # Check kg curation staleness
    kg_path = BAW_DATA / "knowledge_graph.json"
    if kg_path.exists():
        from datetime import datetime, timezone
        mtime = datetime.fromtimestamp(kg_path.stat().st_mtime, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - mtime).days
        if age_days > 14:
            findings.append({
                "area": "memory",
                "file": "knowledge_graph.json",
                "issue": f"KG not curated in {age_days} days",
                "severity": "low",
            })

    # Check cron health
    from core.scheduler import Scheduler
    scheduler = Scheduler(BAW_DATA)
    cron_report = scheduler.cron_status_report()
    if "failed" in cron_report.lower() or "error" in cron_report.lower():
        findings.append({
            "area": "cron",
            "file": "schedule.yaml",
            "issue": "Cron job failures detected in task history",
            "severity": "high",
            "details": cron_report[:300],
        })

    return findings


def main():
    steps = []

    # Step 1: Codebase scan
    steps.append(run_step("codebase_scan", scan_codebase))

    # Step 2: Self-diagnose
    steps.append(run_step("self_diagnose", run_diagnose))

    # Step 3: Harness check
    steps.append(run_step("harness_check", check_harness_layers))

    # Build report
    report = []
    report.append("=" * 48)
    report.append("BAW Self-Harness Evaluation")
    report.append("=" * 48)
    report.append("")

    all_ok = True
    for s in steps:
        status = "OK" if s["ok"] else "FAIL"
        if not s["ok"]:
            all_ok = False
        report.append(f"[{status}] {s['step']}")
        if s["ok"] and isinstance(s.get("result"), str):
            report.append(f"  {s['result']}")
        elif not s["ok"]:
            report.append(f"  Error: {s.get('error', 'unknown')}")

    # Findings
    harness_findings = None
    for s in steps:
        if s["step"] == "harness_check" and s["ok"]:
            harness_findings = s["result"]
            break

    if harness_findings:
        report.append("")
        report.append("-" * 48)
        report.append("Improvement Opportunities")
        report.append("-" * 48)
        if harness_findings:
            for f in harness_findings:
                severity_icon = {"high": "CRIT", "medium": "WARN", "low": "INFO", "info": "INFO"}
                icon = severity_icon.get(f.get("severity", "info"), "INFO")
                report.append(f"  [{icon}] {f['area']}/{f.get('file','?')}")
                report.append(f"        {f['issue']}")
        else:
            report.append("  No improvement opportunities found — system healthy.")

    report.append("")
    report.append(f"Summary: {len(steps)} steps, {'ALL OK' if all_ok else 'needs attention'}")
    report.append("")

    # Save report
    reports_dir = BAW_DATA / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "weekly_harness_report.txt"
    report_text = "\n".join(report)
    report_path.write_text(report_text, encoding="utf-8")

    print(report_text)


if __name__ == "__main__":
    main()
