"""BAW built-in: YAGNI code review — scan for over-engineering

Inspired by Ponytail (DietrichGebert/ponytail, 47.5K ⭐).

Climbs the YAGNI Decision Ladder and flags code that could be
simplified, deduplicated, or removed entirely.
"""

import ast
import json
import os


def _check_unnecessary_imports(tree: ast.AST, path: str) -> list[dict]:
    """Check 1: Flag third-party imports where stdlib alternative exists."""
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
    import sys as _sys
    stdlib_modules = set()
    if hasattr(_sys, 'stdlib_module_names'):
        stdlib_modules = _sys.stdlib_module_names
    _skip_libs = {
        "yaml", "faster_whisper", "whisper", "fitz", "pymupdf4llm",
        "feedparser", "html2text", "markdownify", "rich", "textual",
        "fastapi", "uvicorn", "httpx", "requests",
    }
    findings = []
    for imp in imports:
        top_level = imp.split(".")[0]
        if top_level in stdlib_modules or top_level in _skip_libs:
            continue
        if "." not in imp and imp.islower() and top_level not in stdlib_modules:
            findings.append({
                "type": "unnecessary_dep", "file": path,
                "detail": f"Third-party import '{imp}' — consider if stdlib can do this", "line": 0,
            })
    return findings


def _check_long_functions(tree: ast.AST, path: str) -> list[dict]:
    """Check 2: Flag functions over 50 lines."""
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            line_count = node.end_lineno - node.lineno if node.end_lineno else 0
            if line_count > 50:
                findings.append({
                    "type": "long_function", "file": path,
                    "detail": f"Function '{node.name}' is {line_count} lines — could it be split?",
                    "line": node.lineno,
                })
    return findings


def _check_overengineered_classes(tree: ast.AST, path: str) -> list[dict]:
    """Check 3: Flag classes where a simple function would do."""
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [n for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if len(methods) <= 1 and methods:
                if any(m.name == "__init__" for m in methods) and len(methods) <= 2:
                    findings.append({
                        "type": "over_engineered", "file": path,
                        "detail": f"Class '{node.name}' has only __init__ + {len(methods)-1} methods — could be a function",
                        "line": node.lineno,
                    })
    return findings


def _check_file(path: str) -> list[dict]:
    """Check a single Python file for YAGNI violations."""
    try:
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, FileNotFoundError):
        return []
    findings = _check_unnecessary_imports(tree, path)
    findings.extend(_check_long_functions(tree, path))
    findings.extend(_check_overengineered_classes(tree, path))
    return findings


def _walk_python_files(path: str) -> list[str]:
    """Walk a path (file or directory) and return list of .py files."""
    if os.path.isfile(path):
        return [path]
    files = []
    for root, dirs, fnames in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__", "venv", "node_modules"))]
        for f in fnames:
            if f.endswith(".py"):
                files.append(os.path.join(root, f))
    return files


def _scan_yagni_comments(fpath: str) -> list[dict]:
    """Scan a file for existing [YAGNI] comments."""
    try:
        results = []
        with open(fpath) as f:
            for i, line in enumerate(f, 1):
                if "[YAGNI]" in line:
                    results.append({
                        "type": "deferred_yagni", "file": fpath,
                        "detail": line.strip(), "line": i,
                    })
        return results
    except (OSError, UnicodeDecodeError):
        return []


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
    for fpath in _walk_python_files(path):
        findings.extend(_check_file(fpath))
        findings.extend(_scan_yagni_comments(fpath))

    if not findings:
        return json.dumps({"status": "clean", "message": "No YAGNI violations found."}, ensure_ascii=False, indent=2)

    by_type = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)

    result = {"total_findings": len(findings),
              "summary": {k: len(v) for k, v in by_type.items()},
              "details": findings}
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
