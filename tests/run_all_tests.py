#!/usr/bin/env python3
"""BAW Comprehensive Test Runner.

Runs all test suites with coverage reporting.
Usage: python tests/run_all_tests.py [--quick|--full|--ci]
"""
from __future__ import annotations

import sys
import subprocess
import argparse
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], desc: str) -> int:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=APP_ROOT)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="BAW Test Runner")
    parser.add_argument("--mode", choices=["quick", "full", "ci"], default="full")
    args = parser.parse_args()

    exit_code = 0

    # ── Unit tests (always run) ──
    unit_cmd = [sys.executable, "-m", "pytest", "tests/unit", "-v", "-m", "unit"]
    if args.mode == "ci":
        unit_cmd += ["--cov=core", "--cov=cli", "--cov-report=xml:tests/coverage.xml", "--cov-fail-under=80"]
    exit_code |= run(unit_cmd, "UNIT TESTS")

    if args.mode == "quick":
        return exit_code

    # ── Integration tests ──
    int_cmd = [sys.executable, "-m", "pytest", "tests/integration", "-v", "-m", "integration"]
    exit_code |= run(int_cmd, "INTEGRATION TESTS")

    if args.mode == "quick":
        return exit_code

    # ── E2E tests ──
    e2e_cmd = [sys.executable, "-m", "pytest", "tests/e2e", "-v", "-m", "e2e", "--timeout=120"]
    exit_code |= run(e2e_cmd, "E2E TESTS")

    # ── Coverage report (full/ci only) ──
    if args.mode in ("full", "ci"):
        cov_cmd = [sys.executable, "-m", "pytest", "tests/", "-v", "--cov=core", "--cov=cli",
                   "--cov-report=term-missing", "--cov-report=html:tests/htmlcov"]
        exit_code |= run(cov_cmd, "COVERAGE REPORT")

    print(f"\n{'='*60}")
    if exit_code == 0:
        print("  ✅ ALL TESTS PASSED")
    else:
        print(f"  ❌ SOME TESTS FAILED (exit code: {exit_code})")
    print(f"{'='*60}\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
