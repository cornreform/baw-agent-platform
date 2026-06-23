"""BAW built-in: codebase documentation — self-documenting, AI-maintainable.

Scans BAW's own codebase (core/ and tools/) and produces structured
reports, dependency maps, interface contracts, and an INDEX.md that acts
as a "readme for AI."  The scan result is cached as JSON so repeated
calls are fast.

Usage (from within BAW):
  codebase_doc(scan=True)       — just scan and cache
  codebase_doc(report=True)     — print structured report
  codebase_doc(write_index=True) — write INDEX.md to BAW_HOME
  codebase_doc(verify=True)     — verify all imports resolve

Output uses plain-text [DOC] [MODULE] [CONTRACT] [DEP] tags — no emoji,
no markdown bold, no unicode arrows.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("baw.codebase_doc")

# ── Paths ─────────────────────────────────────────────────────────────────

_THIS_FILE = Path(__file__).resolve()
_BAW_HOME = _THIS_FILE.parent.parent  # tools/ -> baw/

_CACHE_FILE = _BAW_HOME / ".codebase_cache.json"
_INDEX_FILE = _BAW_HOME / "INDEX.md"

# Directories to scan (relative to BAW_HOME)
_SCAN_DIRS = ["core", "tools"]

# ── Data structures ────────────────────────────────────────────────────────


def _is_baw_import(name: str) -> bool:
    """Return True if `name` looks like an import of another BAW module."""
    parts = name.split(".")
    if len(parts) >= 1 and parts[0] in ("core", "tools"):
        return True
    # Also catch relative imports from within core/ or tools/
    return False


def _relative_to_baw(path: Path) -> str:
    """Convert absolute path to BAW-relative like core/loop.py."""
    try:
        return str(path.relative_to(_BAW_HOME))
    except ValueError:
        return path.name


def _read_module_source(filepath: Path, info: dict) -> tuple[list[str] | None, ast.Module | None]:
    """Read and parse a .py file, filling info with size/docstring on error."""
    try:
        source = filepath.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Cannot read %s: %s", filepath, e)
        return None, None

    lines = source.splitlines()
    info["size_lines"] = len(lines)

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        logger.warning("Syntax error in %s: %s", filepath, e)
        info["docstring"] = f"[PARSE ERROR] {e}"
        return None, None

    return lines, tree


def _extract_module_docstring(tree: ast.Module) -> str:
    """Extract module-level docstring from parsed AST."""
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        return tree.body[0].value.value.strip()[:500]
    return ""


def _walk_module_ast(tree: ast.Module, filepath: Path, source: str, lines: list[str]) -> dict:
    """Walk AST to collect imports, functions, and classes into info dict."""
    info: dict[str, Any] = {
        "all_imports": [], "baw_imports": [], "functions": [], "classes": [], "exports": [],
    }

    for node in ast.walk(tree):
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                info["all_imports"].append(alias.name)
                if _is_baw_import(alias.name):
                    info["baw_imports"].append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                info["all_imports"].append(node.module)
                if _is_baw_import(node.module):
                    for alias in node.names:
                        qualified = f"{node.module}.{alias.name}"
                        info["baw_imports"].append(qualified)
                elif not node.level:
                    info["all_imports"].append(node.module)

        # Top-level functions
        elif isinstance(node, ast.FunctionDef) and isinstance(node, ast.stmt):
            if not any(isinstance(parent, (ast.ClassDef, ast.FunctionDef))
                       for parent in _get_parent_chain(tree, node)):
                func_info = _extract_function_info(node)
                info["functions"].append(func_info)
                info["exports"].append(node.name)

        # Top-level classes
        elif isinstance(node, ast.ClassDef) and isinstance(node, ast.stmt):
            if not any(isinstance(parent, (ast.ClassDef, ast.FunctionDef))
                       for parent in _get_parent_chain(tree, node)):
                cls_info = _extract_class_info(node, filepath, source, lines)
                info["classes"].append(cls_info)
                info["exports"].append(node.name)

    info["baw_imports"] = sorted(set(info["baw_imports"]))
    info["all_imports"] = sorted(set(info["all_imports"]))
    return info


def _extract_module_info(filepath: Path) -> dict[str, Any]:
    """Parse a single .py file and return structured module info."""
    rel_path = _relative_to_baw(filepath)
    module_name = rel_path.replace("/", ".").replace(".py", "")
    if module_name.endswith(".__init__"):
        module_name = module_name[:-9]  # drop .__init__
    elif module_name == "__init__":
        module_name = rel_path.replace(".py", "")

    info: dict[str, Any] = {
        "path": rel_path,
        "module_name": module_name,
        "size_lines": 0,
        "docstring": "",
        "functions": [],
        "classes": [],
        "baw_imports": [],
        "all_imports": [],
        "exports": [],
    }

    lines, tree = _read_module_source(filepath, info)
    if lines is None or tree is None:
        return info

    info["docstring"] = _extract_module_docstring(tree)
    walked = _walk_module_ast(tree, filepath, source := "\n".join(lines), lines)
    info.update(walked)

    return info


def _get_parent_chain(tree: ast.Module, target: ast.AST) -> list[ast.AST]:
    """Walk the tree and return parent chain for target node."""
    chain: list[ast.AST] = []

    def _walk(node: ast.AST, parents: list[ast.AST]) -> bool:
        if node is target:
            chain.extend(parents)
            return True
        for child in ast.iter_child_nodes(node):
            if _walk(child, parents + [node]):
                return True
        return False

    _walk(tree, [])
    return chain


def _extract_function_info(node: ast.FunctionDef) -> dict[str, Any]:
    """Extract info from a FunctionDef node."""
    func_doc = ast.get_docstring(node) or ""
    args_info = []
    for arg in node.args.args:
        arg_name = arg.arg
        arg_type = ""
        if arg.annotation:
            try:
                arg_type = ast.unparse(arg.annotation)
            except Exception:
                arg_type = "?"
        args_info.append({"name": arg_name, "type": arg_type})

    return_type = ""
    if node.returns:
        try:
            return_type = ast.unparse(node.returns)
        except Exception:
            return_type = "?"

    return {
        "name": node.name,
        "docstring": func_doc[:300],  # cap
        "args": args_info,
        "return_type": return_type,
        "lineno": node.lineno,
        "decorators": [ast.unparse(d) for d in node.decorator_list],
    }


def _extract_class_info(
    node: ast.ClassDef, filepath: Path, source: str, lines: list[str]
) -> dict[str, Any]:
    """Extract info from a ClassDef node."""
    cls_doc = ast.get_docstring(node) or ""
    methods = []
    for item in node.body:
        if isinstance(item, ast.FunctionDef):
            m = _extract_function_info(item)
            methods.append(m)

    return {
        "name": node.name,
        "docstring": cls_doc[:300],
        "methods": methods,
        "lineno": node.lineno,
    }


# ── Scan ────────────────────────────────────────────────────────────────────


def _discover_files() -> list[Path]:
    """Return all .py files in core/ and tools/."""
    files: list[Path] = []
    for d in _SCAN_DIRS:
        scan_dir = _BAW_HOME / d
        if not scan_dir.is_dir():
            logger.warning("Scan directory not found: %s", scan_dir)
            continue
        for root, _dirs, fnames in os.walk(str(scan_dir)):
            root_path = Path(root)
            # Skip __pycache__
            if "__pycache__" in root_path.parts:
                continue
            for fn in sorted(fnames):
                if fn.endswith(".py"):
                    files.append(root_path / fn)
    return files


def _build_dependency_graph(modules: dict[str, dict]) -> tuple[dict[str, list[str]], list[list[str]]]:
    """Build dependency map and detect circular deps from module info."""
    deps: dict[str, set[str]] = {}
    for rel, mod in modules.items():
        deps[rel] = set()
        for imp in mod["baw_imports"]:
            parts = imp.split(".")
            if len(parts) >= 2 and parts[0] in _SCAN_DIRS:
                candidate = "/".join(parts) + ".py"
                if candidate in modules:
                    deps[rel].add(candidate)
                    continue
                candidate_init = "/".join(parts) + "/__init__.py"
                if candidate_init in modules:
                    deps[rel].add(candidate_init)
                    continue
                candidate_mod = "/".join(parts[:2]) + ".py"
                if candidate_mod in modules:
                    deps[rel].add(candidate_mod)
                    continue
                for depth in range(len(parts) - 1, 1, -1):
                    trial = "/".join(parts[:depth]) + ".py"
                    if trial in modules:
                        deps[rel].add(trial)
                        break
                    trial_init = "/".join(parts[:depth]) + "/__init__.py"
                    if trial_init in modules:
                        deps[rel].add(trial_init)
                        break

    sorted_deps: dict[str, list[str]] = {
        k: sorted(deps[k]) for k in sorted(deps)
    }
    circular = _find_circular_dependencies(deps)
    return sorted_deps, circular


def _cache_scan_result(result: dict) -> None:
    """Write scan result to JSON cache file."""
    try:
        _CACHE_FILE.write_text(
            json.dumps(result, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Cached scan to %s", _CACHE_FILE)
    except Exception as e:
        logger.warning("Failed to write cache: %s", e)


def scan() -> dict[str, Any]:
    """Scan BAW's own codebase and return structured data.

    Returns dict with keys:
      modules: dict[rel_path -> module_info]
      module_map: dependency graph info
      scanned_at: timestamp
    """
    logger.info("Scanning BAW codebase at %s", _BAW_HOME)

    files = _discover_files()
    modules: dict[str, dict] = {}
    total_lines = 0

    for fp in files:
        info = _extract_module_info(fp)
        rel = info["path"]
        modules[rel] = info
        total_lines += info["size_lines"]

    sorted_deps, circular = _build_dependency_graph(modules)

    result: dict[str, Any] = {
        "modules": modules,
        "dependency_map": sorted_deps,
        "circular_dependencies": circular,
        "stats": {
            "modules_scanned": len(modules),
            "total_lines": total_lines,
            "dependencies_tracked": sum(len(v) for v in sorted_deps.values()),
            "circular_dep_count": len(circular),
            "scanned_at": str(__import__("datetime").datetime.now()),
        },
    }

    _cache_scan_result(result)

    return result


# ── Circular dependency detection (DFS) ────────────────────────────────────


def _find_circular_dependencies(
    deps: dict[str, set[str]]
) -> list[list[str]]:
    """Detect circular dependencies using DFS with path tracking."""
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        if node in path_set:
            # Found a cycle
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            cycles.append(cycle)
            return
        if node in visited:
            return

        visited.add(node)
        path.append(node)
        path_set.add(node)

        for neighbour in deps.get(node, set()):
            dfs(neighbour)

        path.pop()
        path_set.remove(node)

    for node in sorted(deps):
        if node not in visited:
            dfs(node)

    # Deduplicate cycles (same set of nodes, rotated)
    seen: set[str] = set()
    unique_cycles: list[list[str]] = []
    for cycle in cycles:
        sig = "|".join(sorted(cycle))
        if sig not in seen:
            seen.add(sig)
            unique_cycles.append(cycle)

    return unique_cycles


# ── Report ──────────────────────────────────────────────────────────────────


def _format_docstring(doc: str, max_len: int = 100) -> str:
    """Truncate and clean a docstring for one-line display."""
    if not doc:
        return "(no docstring)"
    # Take first line or first sentence
    first_line = doc.splitlines()[0].strip()
    if len(first_line) > max_len:
        first_line = first_line[: max_len - 3] + "..."
    return first_line


def _report_module_map(modules: dict, dep_map: dict) -> list[str]:
    """Generate Module Map section of the report."""
    lines: list[str] = []
    for scan_dir in _SCAN_DIRS:
        dir_modules = {
            k: v for k, v in modules.items() if k.startswith(scan_dir + "/")
        }
        if not dir_modules:
            continue
        for rel_path in sorted(dir_modules):
            mod = modules[rel_path]
            doc = _format_docstring(mod.get("docstring", ""))
            lines.append(f"{rel_path} ({mod['size_lines']} lines)")
            lines.append(f"  [DOC] {doc}")

            baw_imps = mod.get("baw_imports", [])
            if baw_imps:
                lines.append(f"  [DEP] Imports: {', '.join(sorted(set(baw_imps)))}")

            exports = mod.get("exports", [])
            if exports:
                lines.append(f"  [MODULE] Exports: {', '.join(exports)}")

            dep_count = len(dep_map.get(rel_path, []))
            lines.append(f"  [DEP] Dependencies: {dep_count} module(s)")
            lines.append("")
    return lines


def _report_interface_contracts(modules: dict) -> list[str]:
    """Generate Interface Contracts section of the report."""
    lines: list[str] = []
    for rel_path in sorted(modules):
        mod = modules[rel_path]
        funcs = mod.get("functions", [])
        classes = mod.get("classes", [])

        if not funcs and not classes:
            continue

        lines.append(f"[CONTRACT] {rel_path}")

        for fn in funcs:
            args_str = ", ".join(
                f"{a['name']}: {a['type']}" if a["type"] else a["name"]
                for a in fn["args"]
            )
            return_str = f" -> {fn['return_type']}" if fn["return_type"] else ""
            lines.append(f"  {fn['name']}({args_str}){return_str}")
            fn_doc = fn.get("docstring", "")
            if fn_doc:
                short = fn_doc[:120].replace("\n", " ")
                lines.append(f"    [CONTRACT] {short}")

        for cls in classes:
            lines.append(f"  class {cls['name']}")
            cls_doc = cls.get("docstring", "")
            if cls_doc:
                short = cls_doc[:120].replace("\n", " ")
                lines.append(f"    [CONTRACT] {short}")
            for m in cls.get("methods", []):
                args_str = ", ".join(
                    f"{a['name']}: {a['type']}" if a["type"] else a["name"]
                    for a in m["args"]
                )
                return_str = f" -> {m['return_type']}" if m["return_type"] else ""
                lines.append(f"    {m['name']}({args_str}){return_str}")

        lines.append("")
    return lines


def _report_dependency_groups(modules: dict, dep_map: dict) -> list[str]:
    """Generate Dependency Groups section of the report."""
    lines: list[str] = []
    for scan_dir in _SCAN_DIRS:
        dir_modules = {
            k for k in modules if k.startswith(scan_dir + "/")
        }
        if not dir_modules:
            continue

        depends_on: set[str] = set()
        for rel in sorted(dir_modules):
            for dep in dep_map.get(rel, []):
                dep_dir = dep.split("/")[0]
                if dep_dir != scan_dir:
                    depends_on.add(dep_dir)

        if depends_on:
            lines.append(
                f"{scan_dir}/ (tool layer): depends on {', '.join(sorted(depends_on))}"
            )
        else:
            lines.append(f"{scan_dir}/ (base layer): no intra-module dependencies")

    lines.append("")
    return lines


def _report_circular_deps(circular: list) -> list[str]:
    """Generate Circular Dependency Warnings section."""
    lines: list[str] = []
    if circular:
        lines.append("=== Circular Dependency Warnings ===")
        for cycle in circular:
            path_str = " -> ".join(cycle)
            lines.append(f"  [WARN] Circular: {path_str}")
        lines.append("")
    else:
        lines.append("  No circular dependencies found")
        lines.append("")
    return lines


def _report_per_directory_summary(modules: dict, dep_map: dict) -> list[str]:
    """Generate Per-Directory Summary section."""
    lines: list[str] = []
    lines.append("=== Per-Directory Summary ===")
    for scan_dir in _SCAN_DIRS:
        dir_modules = {
            k: v for k, v in modules.items() if k.startswith(scan_dir + "/")
        }
        if not dir_modules:
            continue
        dir_lines = sum(m["size_lines"] for m in dir_modules.values())
        dir_deps = sum(
            len(dep_map.get(rel, [])) for rel in dir_modules
        )
        lines.append(
            f"  {scan_dir}/: {len(dir_modules)} files, "
            f"{dir_lines} lines, {dir_deps} inter-module deps"
        )
    return lines


def report(scan_data: Optional[dict] = None) -> str:
    """Generate a structured plain-text report of the codebase.

    Modules scanned, dependency map, interface contracts, circular deps.
    """
    if scan_data is None:
        scan_data = _load_or_scan()

    modules: dict = scan_data["modules"]
    dep_map: dict = scan_data["dependency_map"]
    circular: list = scan_data["circular_dependencies"]
    stats: dict = scan_data["stats"]

    lines: list[str] = []
    lines.append("[DOC] BAW Codebase Analysis")
    lines.append(f"  Modules scanned: {stats['modules_scanned']}")
    lines.append(f"  Total lines: {stats['total_lines']}")
    lines.append(f"  Dependencies tracked: {stats['dependencies_tracked']}")
    lines.append(f"  Circular deps: {stats['circular_dep_count']}")
    lines.append("")

    # Module Map
    lines.append("=== Module Map ===")
    lines.extend(_report_module_map(modules, dep_map))

    # Interface Contracts
    lines.append("=== Interface Contracts ===")
    lines.extend(_report_interface_contracts(modules))

    # Dependency Groups
    lines.append("=== Dependency Groups ===")
    lines.extend(_report_dependency_groups(modules, dep_map))

    # Circular Dependencies
    lines.extend(_report_circular_deps(circular))

    # Per-Directory Summary
    lines.extend(_report_per_directory_summary(modules, dep_map))

    return "\n".join(lines)


# ── Write INDEX.md ─────────────────────────────────────────────────────────


def _write_index_header(stats: dict) -> list[str]:
    """Generate INDEX.md header section."""
    md_lines = [
        "# BAW Codebase Index",
        "",
        "Auto-generated by `tools/codebase_doc.py`. "
        "AI agents should read this file first before modifying BAW's own code.",
        "",
        f"- Modules scanned: {stats['modules_scanned']}",
        f"- Total lines: {stats['total_lines']}",
        f"- Dependencies tracked: {stats['dependencies_tracked']}",
        f"- Circular dependencies: {stats['circular_dep_count']}",
        "",
    ]
    return md_lines


def _write_index_module_inventory(modules: dict) -> list[str]:
    """Generate Module Inventory table for INDEX.md."""
    md_lines = [
        "## Module Inventory",
        "",
        "| Module | Lines | Docstring | Exports |",
        "|--------|-------|-----------|---------|",
    ]

    for rel_path in sorted(modules):
        mod = modules[rel_path]
        doc = _format_docstring(mod.get("docstring", ""), max_len=80)
        exports = ", ".join(mod.get("exports", []))[:80]
        md_lines.append(
            f"| `{rel_path}` | {mod['size_lines']} | {doc} | `{exports}` |"
        )

    md_lines.append("")
    return md_lines


def _write_index_dependency_map(dep_map: dict) -> list[str]:
    """Generate Dependency Map section for INDEX.md."""
    md_lines = [
        "## Dependency Map",
        "",
        "```",
    ]
    for rel_path in sorted(dep_map):
        deps = dep_map[rel_path]
        if deps:
            md_lines.append(f"  {rel_path}  -->  {', '.join(deps)}")
        else:
            md_lines.append(f"  {rel_path}  (no BAW deps)")
    md_lines.append("```")
    md_lines.append("")
    return md_lines


def _write_index_dependency_groups(modules: dict, dep_map: dict) -> list[str]:
    """Generate Dependency Groups section for INDEX.md."""
    md_lines = [
        "## Dependency Groups",
        "",
    ]
    for scan_dir in _SCAN_DIRS:
        dir_modules = sorted(
            k for k in modules if k.startswith(scan_dir + "/")
        )
        if not dir_modules:
            continue
        md_lines.append(f"### {scan_dir}/")
        md_lines.append("")
        for rel in dir_modules:
            deps = dep_map.get(rel, [])
            dep_str = ", ".join(deps) if deps else "(none)"
            md_lines.append(f"- `{rel}` depends on: {dep_str}")
        md_lines.append("")
    return md_lines


def _write_index_circular(circular: list) -> list[str]:
    """Generate Circular Dependencies section for INDEX.md."""
    md_lines = ["## Circular Dependencies", ""]
    if circular:
        md_lines.append("WARNING: The following circular dependencies exist:")
        md_lines.append("")
        for cycle in circular:
            md_lines.append(f"- {' -> '.join(cycle)}")
        md_lines.append("")
    else:
        md_lines.append("None found.")
        md_lines.append("")
    return md_lines


def _write_index_interface_contracts(modules: dict) -> list[str]:
    """Generate Interface Contracts section for INDEX.md."""
    md_lines = [
        "## Interface Contracts (Functions with Arguments)",
        "",
    ]
    for rel_path in sorted(modules):
        mod = modules[rel_path]
        funcs = mod.get("functions", [])
        classes = mod.get("classes", [])

        has_contract = any(
            fn.get("args") for fn in funcs
        ) or any(
            m.get("args") for cls in classes for m in cls.get("methods", [])
        )

        if not has_contract:
            continue

        md_lines.append(f"### `{rel_path}`")
        md_lines.append("")

        for fn in funcs:
            if not fn.get("args"):
                continue
            args_str = ", ".join(
                f"{a['name']}: {a['type']}" if a["type"] else a["name"]
                for a in fn["args"]
            )
            ret = f" -> {fn['return_type']}" if fn.get("return_type") else ""
            md_lines.append(f"- `{fn['name']}({args_str}){ret}`")
            if fn.get("docstring"):
                md_lines.append(f"  - {fn['docstring'][:200]}")

        for cls in classes:
            for m in cls.get("methods", []):
                if not m.get("args"):
                    continue
                args_str = ", ".join(
                    f"{a['name']}: {a['type']}" if a["type"] else a["name"]
                    for a in m["args"]
                )
                ret = f" -> {m['return_type']}" if m.get("return_type") else ""
                md_lines.append(f"- `{cls['name']}.{m['name']}({args_str}){ret}`")
                if m.get("docstring"):
                    md_lines.append(f"  - {m['docstring'][:200]}")

        md_lines.append("")
    return md_lines


def write_index(scan_data: Optional[dict] = None) -> str:
    """Write INDEX.md to BAW_HOME with complete module inventory and map.

    This acts as a "readme for AI" — when BAW needs to modify its own
    code, it first reads INDEX.md to understand the architecture.
    """
    if scan_data is None:
        scan_data = _load_or_scan()

    modules: dict = scan_data["modules"]
    dep_map: dict = scan_data["dependency_map"]
    circular: list = scan_data["circular_dependencies"]
    stats: dict = scan_data["stats"]

    md_lines: list[str] = []
    md_lines.extend(_write_index_header(stats))
    md_lines.extend(_write_index_module_inventory(modules))
    md_lines.extend(_write_index_dependency_map(dep_map))
    md_lines.extend(_write_index_dependency_groups(modules, dep_map))
    md_lines.extend(_write_index_circular(circular))
    md_lines.extend(_write_index_interface_contracts(modules))

    # Footer
    md_lines.append("---")
    md_lines.append(f"_Generated at {stats['scanned_at']}_")
    md_lines.append("")

    content = "\n".join(md_lines)

    try:
        _INDEX_FILE.write_text(content, encoding="utf-8")
        return f"[CODEBASE] Wrote INDEX.md ({len(content)} chars) to {_INDEX_FILE}"
    except Exception as e:
        return f"[CODEBASE] ERROR writing INDEX.md: {e}"


# ── Verify Imports ─────────────────────────────────────────────────────────


def _verify_single_module(mod_name: str) -> str | None:
    """Try to import a module, return error string or None on success."""
    import importlib
    try:
        importlib.import_module(mod_name)
        return None
    except Exception as e:
        return f"{mod_name}: {e}"


def _discover_modules() -> list[str]:
    """Return module names for all .py files in core/ and tools/."""
    modules: list[str] = []
    for scan_dir in _SCAN_DIRS:
        scan_path = _BAW_HOME / scan_dir
        if not scan_path.is_dir():
            continue
        for root, _dirs, fnames in os.walk(str(scan_path)):
            for fn in sorted(fnames):
                if not fn.endswith(".py"):
                    continue
                rel = Path(root).relative_to(_BAW_HOME)
                mod_parts = list(rel.parts)
                if fn == "__init__.py":
                    mod_name = ".".join(mod_parts)
                else:
                    mod_parts.append(fn[:-3])
                    mod_name = ".".join(mod_parts)
                modules.append(mod_name)
    return modules


def verify_imports() -> str:
    """Verify all imports in core/ and tools/ resolve correctly.

    Returns a plain-text report of broken imports.
    """
    lines: list[str] = []
    lines.append("[CODEBASE] Import verification")
    lines.append("")

    broken: list[str] = []
    ok_count = 0
    broken_count = 0

    for mod_name in _discover_modules():
        err = _verify_single_module(mod_name)
        if err:
            broken.append(err)
            broken_count += 1
        else:
            ok_count += 1

    lines.append(f"  Modules OK: {ok_count}")
    lines.append(f"  Modules broken: {broken_count}")

    if broken:
        lines.append("")
        lines.append("=== Broken Imports ===")
        for b in broken:
            lines.append(f"  [FAIL] {b}")
    else:
        lines.append("  All imports verified successfully")

    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_or_scan() -> dict:
    """Load cached scan data if available and fresh, otherwise scan."""
    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            logger.info("Loaded cached scan from %s", _CACHE_FILE)
            return data
        except Exception as e:
            logger.info("Cache invalid, re-scanning: %s", e)

    return scan()


def invalidate_cache() -> str:
    """Delete the cached scan data."""
    if _CACHE_FILE.exists():
        _CACHE_FILE.unlink()
        return "[CODEBASE] Cache invalidated"
    return "[CODEBASE] No cache to invalidate"


# ── Handler ────────────────────────────────────────────────────────────────


def codebase_doc(
    do_scan: bool = False,
    do_report: bool = True,
    do_write_index: bool = False,
    do_verify: bool = False,
    do_invalidate_cache: bool = False,
) -> str:
    """BAW codebase documentation tool.

    Scans BAW's own codebase and produces structured documentation,
    dependency maps, and import verification.

    Args:
        do_scan: Force a fresh scan (default: load from cache if available)
        do_report: Print structured report (default: True)
        do_write_index: Write INDEX.md to BAW_HOME
        do_verify: Verify all imports resolve correctly
        do_invalidate_cache: Delete the JSON cache

    Returns:
        Plain-text report string.
    """
    parts: list[str] = []

    if do_invalidate_cache:
        parts.append(invalidate_cache())

    if do_scan or do_report or do_write_index:
        if do_scan:
            data = scan()
        else:
            data = _load_or_scan()

        if do_report:
            parts.append(report(scan_data=data))

        if do_write_index:
            parts.append(write_index(scan_data=data))

    if do_verify:
        parts.append(verify_imports())

    if not parts:
        # Default: run report from cache
        parts.append(report())

    return "\n".join(parts)


# ── TOOL_DEF ──────────────────────────────────────────────────────────────


TOOL_DEF = {
    "name": "codebase_doc",
    "description": (
        "[DOC] BAW codebase introspection — scan, report, write INDEX.md, "
        "verify imports. Makes the codebase self-documenting and "
        "AI-maintainable. Output uses [DOC] [MODULE] [CONTRACT] [DEP] tags."
    ),
    "handler": codebase_doc,
    "parameters": {
        "type": "object",
        "properties": {
            "do_scan": {
                "type": "boolean",
                "description": "Force a fresh scan (default: use cache)",
                "default": False,
            },
            "do_report": {
                "type": "boolean",
                "description": "Print structured report with module map and dependency graph",
                "default": True,
            },
            "do_write_index": {
                "type": "boolean",
                "description": "Write INDEX.md to BAW_HOME with full inventory",
                "default": False,
            },
            "do_verify": {
                "type": "boolean",
                "description": "Verify all imports resolve correctly",
                "default": False,
            },
            "do_invalidate_cache": {
                "type": "boolean",
                "description": "Delete the JSON scan cache",
                "default": False,
            },
        },
    },
    "risk_level": "low",
}


# ── CLI entry point ────────────────────────────────────────────────────────


if __name__ == "__main__":
    # Simple CLI for manual testing
    import argparse

    parser = argparse.ArgumentParser(description="BAW Codebase Documentation Tool")
    parser.add_argument("--scan", action="store_true", help="Force fresh scan")
    parser.add_argument("--report", action="store_true", default=True, help="Print report")
    parser.add_argument("--write-index", action="store_true", help="Write INDEX.md")
    parser.add_argument("--verify", action="store_true", help="Verify imports")
    parser.add_argument("--invalidate-cache", action="store_true", help="Delete cache")
    args = parser.parse_args()

    result = codebase_doc(
        do_scan=args.scan,
        do_report=args.report,
        do_write_index=args.write_index,
        do_verify=args.verify,
        do_invalidate_cache=args.invalidate_cache,
    )
    print(result)
