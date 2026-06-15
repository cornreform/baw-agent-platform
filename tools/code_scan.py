"""BAW built-in: code_scan — systematic security scanner for directories.

Scans a directory for security risks WITHOUT relying on LLM context window.
Returns structured JSON report — LLM only needs to interpret, not scan.

Usage: code_scan(path="/tmp/minimax-skills", scan_type="quick")
       code_scan(path="/tmp/downloads", scan_type="full", max_files=500)
"""

import os
import re
import json
import shutil
from pathlib import Path

# ── Scan patterns ──────────────────────────────────────────

# File-level patterns (scan file content)
FILE_PATTERNS = {
    "eval_exec": {
        "label": "eval() / exec() usage — arbitrary code execution risk",
        "severity": "critical",
        "patterns": [
            r"\beval\s*\([^)]*\)",
            r"\bexec\s*\([^)]*\)",
        ],
    },
    "shell_injection": {
        "label": "Unsafe shell execution — command injection risk",
        "severity": "critical",
        "patterns": [
            r"subprocess\.(run|call|Popen)\s*\(.*shell\s*=\s*True",
            r"os\.system\s*\([^)]+\)",
            r"\.exec\s*\(.*cmd",
        ],
    },
    "break_system": {
        "label": "System package bypass — corrupts pip install isolation",
        "severity": "high",
        "patterns": [
            r"--break-system-packages",
        ],
    },
    "pickle_unpickle": {
        "label": "pickle.load() — arbitrary code execution if source untrusted",
        "severity": "high",
        "patterns": [
            r"\bpickle\.(load|loads)\s*\(",
        ],
    },
    "credential_leak": {
        "label": "Potential hardcoded credentials or API keys",
        "severity": "high",
        "patterns": [
            r"(api_key|api_secret|password|token)\s*=\s*[\"'][^\"']{8,}[\"']",
            r"(API_KEY|SECRET|TOKEN)=\s*[\"'][^\"']{8,}[\"']",
            r"sk-[a-zA-Z0-9]{20,}",
        ],
    },
    "sudo_usage": {
        "label": "sudo / root requirement — privilege escalation",
        "severity": "medium",
        "patterns": [
            r"\bsudo\s",
            r"require.*root",
        ],
    },
    "auto_install_at_import": {
        "label": "Module-level auto-install — triggers pip on import",
        "severity": "medium",
        "patterns": [
            r"(ensure_deps|install_deps|check_deps)\s*\(\s*\)",
        ],
    },
    "download_no_hash": {
        "label": "External download without hash verification",
        "severity": "low",
        "patterns": [
            r"(urllib\.request\.urlretrieve|requests\.get\s*\(.*download|curl\s+-[sS]*O)",
        ],
    },
    "unsafe_deserialize": {
        "label": "Unsafe deserialization (yaml load without SafeLoader)",
        "severity": "low",
        "patterns": [
            r"yaml\.load\s*\([^)]*\)",
        ],
    },
}

# File extensions to scan
SCAN_EXTENSIONS = {
    ".py", ".js", ".ts", ".sh", ".bash", ".rb", ".pl", ".php",
    ".go", ".rs", ".java", ".c", ".cpp", ".h", ".yaml", ".yml",
    ".json", ".toml", ".cfg", ".conf", ".ini",
}

# Max file size for content scan (skip larger files)
MAX_FILE_SIZE = 500 * 1024  # 500KB

# ── Core scanner ────────────────────────────────────────────

def _scan_file(path: Path) -> dict:
    """Scan a single file for security patterns."""
    findings = []
    try:
        size = path.stat().st_size
    except OSError:
        return {"path": str(path), "findings": [], "skipped": True, "reason": "stat failed"}

    if size > MAX_FILE_SIZE:
        return {
            "path": str(path),
            "findings": [],
            "skipped": True,
            "reason": f"file too large ({size:,} bytes > {MAX_FILE_SIZE:,} limit)",
        }

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"path": str(path), "findings": [], "skipped": True, "reason": "read error"}

    for line_no, line in enumerate(content.split("\n"), 1):
        for pattern_name, pattern_def in FILE_PATTERNS.items():
            for pattern in pattern_def["patterns"]:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        "line": line_no,
                        "type": pattern_name,
                        "severity": pattern_def["severity"],
                        "label": pattern_def["label"],
                        "snippet": line.strip()[:120],
                    })
                    break  # one match per pattern type per line

    return {
        "path": str(path),
        "size": size,
        "lines": len(content.split("\n")),
        "findings": findings,
        "skipped": False,
    }


def _scan_directory(
    path: str,
    scan_type: str = "quick",
    max_files: int = 100,
) -> str:
    """Scan a directory for security risks.

    Args:
        path: Directory to scan.
        scan_type: "quick" (extensions only), "full" (all files), "deps_only" (requirements/pyproject only).
        max_files: Maximum files to scan.

    Returns:
        JSON report as string.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return json.dumps({"error": f"path not found: {path}"}, ensure_ascii=False)
    if not p.is_dir():
        return json.dumps({"error": f"not a directory: {path}"}, ensure_ascii=False)

    # Collect files
    all_files = []
    if scan_type == "deps_only":
        dep_patterns = ["requirements*.txt", "pyproject.toml", "setup.py",
                        "setup.cfg", "Pipfile", "package.json", "Cargo.toml",
                        "go.mod", "Gemfile", "composer.json"]
        for dp in dep_patterns:
            all_files.extend(p.rglob(dp))
    else:
        for f in p.rglob("*"):
            if f.is_file():
                ext = f.suffix.lower()
                if scan_type == "full" or ext in SCAN_EXTENSIONS or ext == "":
                    all_files.append(f)

    all_files = sorted(all_files)[:max_files]

    # Scan
    results = []
    total_findings = 0
    critical_count = 0
    high_count = 0
    skipped_count = 0

    for f in all_files:
        result = _scan_file(f)
        results.append(result)
        if result.get("skipped"):
            skipped_count += 1
        findings = result.get("findings", [])
        total_findings += len(findings)
        for finding in findings:
            if finding["severity"] == "critical":
                critical_count += 1
            elif finding["severity"] == "high":
                high_count += 1

    report = {
        "path": str(p),
        "scan_type": scan_type,
        "total_files": len(results),
        "skipped_files": skipped_count,
        "total_findings": total_findings,
        "critical_findings": critical_count,
        "high_findings": high_count,
        "summary": _build_summary(critical_count, high_count, total_findings),
        "files": results,
    }
    return json.dumps(report, indent=2, ensure_ascii=False)


def _build_summary(critical: int, high: int, total: int) -> str:
    """Build a human-readable summary."""
    if total == 0:
        return "✅ Clean — no security issues found."
    parts = []
    if critical > 0:
        parts.append(f"🔴 {critical} critical finding(s)")
    if high > 0:
        parts.append(f"🟡 {high} high-severity finding(s)")
    other = total - critical - high
    if other > 0:
        parts.append(f"{other} lower-severity finding(s)")
    return "⚠️ " + ", ".join(parts) + f" ({total} total)"


# ── TOOL_DEF ────────────────────────────────────────────────

def _handler(path: str, scan_type: str = "quick", max_files: int = 100) -> str:
    return _scan_directory(path=path, scan_type=scan_type, max_files=max_files)


TOOL_DEF = {
    "name": "code_scan",
    "description": (
        "🔍 **SECURITY SCANNER** — MUST USE before executing any downloaded code. "
        "Scan a directory for security risks: eval/exec, shell injection, "
        "credential leaks, unsafe deserialization, system bypass flags, "
        "and auto-install triggers. "
        "Returns structured JSON report with per-file findings. "
        "Use this BEFORE executing scripts from: cloned repos, downloads, "
        "user-uploaded code, pip-installed packages."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to scan (absolute path).",
            },
            "scan_type": {
                "type": "string",
                "enum": ["quick", "full", "deps_only"],
                "description": "quick=code files only, full=all files, deps_only=dependency files only.",
                "default": "quick",
            },
            "max_files": {
                "type": "integer",
                "description": "Maximum files to scan (default: 100, max: 500).",
                "default": 100,
            },
        },
        "required": ["path"],
    },
    "risk_level": "low",
}
