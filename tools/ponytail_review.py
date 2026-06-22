"""BAW built-in: YAGNI code review — scan for over-engineering

Inspired by Ponytail (DietrichGebert/ponytail, 47.5K ⭐).

Climbs the YAGNI Decision Ladder and flags code that could be
simplified, deduplicated, or removed entirely.
"""

import ast
import json
import os


def _check_file(path: str) -> list[dict]:
    """Check a single Python file for YAGNI violations."""
    findings = []
    try:
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, FileNotFoundError):
        return findings

    # Check 1: Unnecessary imports (stdlib alternatives exist)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")

    # Flag imports that are NOT in stdlib
    import sys as _sys
    stdlib_modules = set()
    if hasattr(_sys, 'stdlib_module_names'):
        stdlib_modules = _sys.stdlib_module_names
    # Fallback common stdlib prefixes
    stdlib_prefixes = (
        "os.", "sys.", "json.", "re.", "math.", "datetime.", "collections.",
        "typing.", "functools.", "itertools.", "hashlib.", "base64.", "uuid.",
        "csv.", "io.", "textwrap.", "inspect.", "logging.", "argparse.",
        "subprocess.", "ast.", "pathlib.", "copy.", "enum.", "random.",
        "statistics.", "string.", "struct.", "tempfile.", "time.", "traceback.",
        "types.", "warnings.", "weakref.", "pprint.", "pickle.", "sqlite3.",
        "xml.", "html.", "http.", "urllib.", "email.", "json",
    )

    third_party = []
    for imp in imports:
        # Get the top-level module name
        top_level = imp.split(".")[0]
        # Check if it's a known stdlib module
        if top_level in stdlib_modules:
            continue
        # Check prefix-based fallback
        if any(imp.startswith(p) for p in stdlib_prefixes):
            continue
        # Skip if it's clearly a local module (no dot, lowercase)
        if "." not in imp and imp.islower() and imp not in stdlib_modules:
            third_party.append(imp)

    for imp in third_party:
        findings.append({
            "type": "unnecessary_dep",
            "file": path,
            "detail": f"Third-party import '{imp}' — consider if stdlib can do this",
            "line": 0,
        })

    # Check 2: Overly long functions (>50 lines)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            line_count = node.end_lineno - node.lineno if node.end_lineno else 0
            if line_count > 50:
                findings.append({
                    "type": "long_function",
                    "file": path,
                    "detail": f"Function '{node.name}' is {line_count} lines — could it be split?",
                    "line": node.lineno,
                })

    # Check 3: Classes where a simple function would do
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [n for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if len(methods) <= 1 and methods:
                # Class with only __init__ + one method → could be a function
                if any(m.name == "__init__" for m in methods) and len(methods) <= 2:
                    findings.append({
                        "type": "over_engineered",
                        "file": path,
                        "detail": f"Class '{node.name}' has only __init__ + {len(methods)-1} methods — could be a function",
                        "line": node.lineno,
                    })

    return findings


def ponytail_review(path: str = ".") -> str:
    """Review code for over-engineering and YAGNI violations.

    Scans Python files in the given path (file or directory) and reports
    findings for:
    - Unnecessary third-party deps (stdlib alternative exists)
    - Overly long functions (>50 lines)
    - Classes that could be simple functions
    - Existing [YAGNI] comments (deferred decisions)

    Args:
        path: File or directory to scan (default: current dir)

    Returns:
        Structured report of YAGNI findings.
    """
    findings = []

    if os.path.isfile(path):
        files = [path]
    else:
        files = []
        for root, dirs, fnames in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "venv", "node_modules"))]
            for f in fnames:
                if f.endswith(".py"):
                    files.append(os.path.join(root, f))

    for fpath in files:
        findings.extend(_check_file(fpath))

        # Also scan for existing [YAGNI] comments (deferred decisions)
        try:
            with open(fpath) as f:
                for i, line in enumerate(f, 1):
                    if "[YAGNI]" in line:
                        findings.append({
                            "type": "deferred_yagni",
                            "file": fpath,
                            "detail": line.strip(),
                            "line": i,
                        })
        except (OSError, UnicodeDecodeError):
            pass

    if not findings:
        return json.dumps({"status": "clean", "message": "No YAGNI violations found."}, ensure_ascii=False, indent=2)

    # Summarize
    by_type = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)

    result = {
        "total_findings": len(findings),
        "summary": {k: len(v) for k, v in by_type.items()},
        "details": findings,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


TOOL_DEF = {
    "name": "ponytail_review",
    "description": (
        "Review code for over-engineering and YAGNI violations. "
        "Inspired by Ponytail (DietrichGebert/ponytail). "
        "Scans Python files for: unnecessary third-party deps, "
        "overly long functions (>50 lines), classes that could be "
        "functions, and existing [YAGNI] deferred decisions. "
        "Fully self-contained — uses only Python stdlib (ast module)."
    ),
    "handler": ponytail_review,
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to scan (default: current dir)",
            },
        },
    },
    "risk_level": "low",
}
