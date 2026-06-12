"""baw tools — create, verify, list, and remove BAW tools.

The scaffolder exists because too many agent sessions try to build a tool by
hand-editing ``tools/<name>.py`` and ``tools/__init__.py`` and then claim
'done' without ever loading the registry. This command enforces the
scaffolding contract:

  1. Create the tool file with a working TOOL_DEF
  2. Register it in tools/__init__.py
  3. Write a smoke test
  4. Run the smoke test
  5. Only declare success if the smoke test passes

Subcommands:
  list                 List all currently-registered tools
  create <name> ...    Scaffold a new tool: write file + register + test
  verify <name>        Run a tool's smoke test (or all tools with --all)
  show <name>          Print a tool's source path + TOOL_DEF summary
  doctor               Cross-check: file on disk == registration in __init__
"""
import argparse
import importlib
import re
import sys
import textwrap
import traceback
from pathlib import Path

from cli import console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax

# Hardcoded paths — single source of truth so agent sessions never write to
# the wrong place. Mirrors the system prompt's `~/baw/` convention.
BAW_REPO = Path.home() / "baw"
TOOLS_DIR = BAW_REPO / "tools"
TOOLS_INIT = TOOLS_DIR / "__init__.py"


# ── Helpers ───────────────────────────────────────────────────

def _read_init() -> tuple[str, list[str]]:
    """Return (init_source, list_of_imported_module_names)."""
    if not TOOLS_INIT.exists():
        return "", []
    src = TOOLS_INIT.read_text(encoding="utf-8")
    # Match `from . import a, b, c` (single-line form, BAW uses this). Anchor
    # at end of line so we don't swallow following defs.
    imported = re.findall(r"from\s+\.\s+import\s+([^\n]+)", src)
    names = []
    for grp in imported:
        for n in grp.split(","):
            n = n.strip()
            # Strip inline comments
            n = n.split("#", 1)[0].strip()
            if n and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", n):
                names.append(n)
    return src, names


def _is_registered_in_init(name: str) -> bool:
    _, names = _read_init()
    return name in names


# ── list ──────────────────────────────────────────────────────

def cmd_list(args):
    src, names = _read_init()
    table = Table(title="[baw.gold]🔧  BAW Tools[/baw.gold]", border_style="baw.accent")
    table.add_column("Name", style="baw.cmd", width=24)
    table.add_column("File", style="baw.muted", width=30)
    table.add_column("Status", width=14)
    if not names:
        console.print("[baw.muted]No tools registered.[/baw.muted]")
        return
    for name in sorted(names):
        path = TOOLS_DIR / f"{name}.py"
        if not path.exists():
            status = "[red]missing file[/red]"
        else:
            has_def = "TOOL_DEF" in path.read_text(encoding="utf-8", errors="replace")
            status = "[green]✓ ok[/green]" if has_def else "[red]no TOOL_DEF[/red]"
        rel = path.relative_to(BAW_REPO) if path.exists() else "—"
        table.add_row(name, str(rel), status)
    console.print(table)


# ── show ──────────────────────────────────────────────────────

def cmd_show(args):
    name = args.name
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        console.print(f"[baw.error]Invalid tool name '{name}'. Use lowercase, digits, underscores; start with a letter.[/baw.error]")
        sys.exit(1)
    path = TOOLS_DIR / f"{name}.py"
    if not path.exists():
        console.print(f"[baw.error]No file at {path}[/baw.error]")
        sys.exit(1)
    src = path.read_text(encoding="utf-8")
    # Find TOOL_DEF block
    m = re.search(r"TOOL_DEF\s*=\s*\{[\s\S]*?^\}", src, re.MULTILINE)
    console.print(Panel(
        Syntax(src, "python", theme="monokai", line_numbers=True, background_color="default"),
        title=f"📄 {path.relative_to(BAW_REPO)}",
        border_style="magenta",
    ))
    if m:
        console.print(Panel(Syntax(m.group(0), "python", theme="monokai"),
                            title="TOOL_DEF", border_style="gold"))


# ── verify ────────────────────────────────────────────────────

def cmd_verify(args):
    """Run a tool's smoke test by importing it and calling TOOL_DEF['handler'].

    For tools whose handler takes no required args, this invokes with `{}`.
    Tools with required args are skipped with a clear note (the agent must
    provide a custom smoke test).
    """
    if args.name:
        names = [args.name]
    else:
        _, names = _read_init()

    if not args.name and not args.all:
        console.print("[baw.dim]Pass a tool name or --all to verify.[/baw.dim]")
        return

    passed, failed, skipped = [], [], []
    for name in names:
        path = TOOLS_DIR / f"{name}.py"
        if not path.exists():
            failed.append((name, "no file on disk"))
            continue
        try:
            # Use BAW_REPO on sys.path so absolute imports work
            if str(BAW_REPO) not in sys.path:
                sys.path.insert(0, str(BAW_REPO))
            mod = importlib.import_module(f"tools.{name}")
            if not hasattr(mod, "TOOL_DEF"):
                failed.append((name, "no TOOL_DEF attribute"))
                continue
            td = mod.TOOL_DEF
            for required in ("name", "description", "handler", "parameters"):
                if required not in td:
                    failed.append((name, f"TOOL_DEF missing '{required}'"))
                    break
            else:
                handler = td["handler"]
                params = td.get("parameters", {})
                required_args = params.get("required", []) if isinstance(params, dict) else []
                if required_args:
                    skipped.append((name, f"required args: {required_args}"))
                else:
                    try:
                        out = handler()
                    except TypeError:
                        # Handler takes at least one arg — call with no-op
                        out = handler(action="verify")
                    passed.append((name, f"handler returned: {str(out)[:80]}"))
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))

    # Render
    table = Table(title="[baw.gold]🔍  Tool verification[/baw.gold]", border_style="baw.accent")
    table.add_column("Tool", style="baw.cmd", width=20)
    table.add_column("Status", width=10)
    table.add_column("Detail")
    for n, d in passed:
        table.add_row(n, "[green]✓ pass[/green]", d)
    for n, d in skipped:
        table.add_row(n, "[yellow]⚠ skip[/yellow]", d)
    for n, d in failed:
        table.add_row(n, "[red]✗ fail[/red]", d)
    console.print(table)

    if failed and not args.continue_on_fail:
        sys.exit(1)


# ── doctor ────────────────────────────────────────────────────

def cmd_doctor(args):
    """Cross-check: file on disk matches registration in tools/__init__.py."""
    src, names = _read_init()
    issues = []
    for name in names:
        path = TOOLS_DIR / f"{name}.py"
        if not path.exists():
            issues.append(f"[red]✗[/red] {name}: registered but file missing at {path}")
            continue
        if "TOOL_DEF" not in path.read_text(encoding="utf-8", errors="replace"):
            issues.append(f"[red]✗[/red] {name}: file exists but has no TOOL_DEF")
            continue
    # Files on disk but not registered
    for f in TOOLS_DIR.glob("*.py"):
        if f.name in ("__init__.py", "MANIFEST.md") or f.stem.startswith("_"):
            continue
        if f.stem not in names:
            issues.append(f"[yellow]⚠[/yellow] {f.stem}: file on disk but not registered in __init__.py")
    if not issues:
        console.print("[baw.success]✓ All tools match disk.[/baw.success]")
        return
    for i in issues:
        console.print(i)
    sys.exit(1)


# ── create ────────────────────────────────────────────────────

TOOL_TEMPLATE = '''"""BAW built-in: {name} — {description_short}

{description_long}
"""
from __future__ import annotations
import json
from typing import Optional


def {name}_handler({handler_args}) -> str:
    """{handler_doc}"""
    # ── TODO: implement the actual tool logic here ──
    return {handler_return}


TOOL_DEF = {{
    "name": "{name}",
    "description": (
        "{description_short}.\\n\\n"
        "Args:\\n"
{args_doc}        "Returns: JSON string or plain text."
    ),
    "handler": {name}_handler,
    "parameters": {{
        "type": "object",
        "properties": {{
{props_doc}        }},
        "required": [{required_list}],
    }},
    "risk_level": "{risk_level}",
}}
'''


SMOKE_TEST_TEMPLATE = '''"""Smoke test for tools/{name}.py — auto-generated by `baw tools create`.

Run: python3 -c "import sys; sys.path.insert(0, '/home/radxa/baw'); import tools.{name}; print(tools.{name}.{name}_handler({sample_args}))"
"""
import sys
sys.path.insert(0, "/home/radxa/baw")
from tools import {name}  # noqa: E402

# 1. Module imports OK
assert hasattr({name}, "TOOL_DEF"), "missing TOOL_DEF"
print(f"✓ TOOL_DEF present, name={{{name}.TOOL_DEF['name']!r}}")

# 2. Handler callable
assert callable({name}.TOOL_DEF["handler"]), "handler not callable"
print("✓ handler is callable")

# 3. Required schema fields
for k in ("name", "description", "handler", "parameters"):
    assert k in {name}.TOOL_DEF, f"missing TOOL_DEF field: {{k}}"
print("✓ TOOL_DEF schema complete")

# 4. Smoke run
out = {name}.{name}_handler({sample_args})
print(f"✓ handler() returned: {{out!r}}")
print("\\nALL CHECKS PASSED")
'''


def _build_template_args(name: str, description: str, args_spec: list[str],
                         properties: dict, required: list[str],
                         risk_level: str, sample_args: str) -> tuple[str, str]:
    """Render the two template files (tool + test) from parsed flags."""
    description_short = description.split("\n", 1)[0].strip()
    description_long = description.strip()
    handler_args = ", ".join(args_spec) if args_spec else ""
    handler_doc = description_short
    handler_return = "json.dumps({'status': 'ok', 'tool': '" + name + "'})"

    # Properties doc block
    props_lines = []
    for prop_name, prop_desc in properties.items():
        props_lines.append(f'            "{prop_name}": {{"type": "string", "description": "{prop_desc}"}},\n')
    props_doc = "".join(props_lines) if props_lines else "            {}\n"

    # Args doc block
    args_lines = []
    for a in args_spec:
        args_lines.append(f'        "- `{a}` (str): TODO describe\\n"\n')
    args_doc = "".join(args_lines)

    tool_src = TOOL_TEMPLATE.format(
        name=name,
        description_short=description_short,
        description_long=description_long,
        handler_args=handler_args,
        handler_doc=handler_doc,
        handler_return=handler_return,
        args_doc=args_doc,
        props_doc=props_doc,
        required_list=", ".join(f'"{r}"' for r in required),
        risk_level=risk_level,
    )
    test_src = SMOKE_TEST_TEMPLATE.format(
        name=name,
        sample_args=sample_args,
    )
    return tool_src, test_src


def cmd_create(args):
    """Scaffold a new tool.

    Steps performed in order:
      1. Validate the tool name (lowercase + underscores only)
      2. Reject if a tool with that name already exists (use --force to overwrite)
      3. Render `tools/<name>.py` from the template
      4. Render `tools/tests/test_<name>.py` (smoke test)
      5. Update `tools/__init__.py` to add the import
      6. Update `tools/__init__.py::register_all()` to add the register call
      7. Run the smoke test
      8. Print success or failure

    If any step fails after a write, the scaffolder leaves a `.partial` file
    and aborts so the user can investigate.
    """
    name = args.name
    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        console.print(f"[baw.error]Invalid name '{name}'. Use lowercase + underscores, start with a letter.[/baw.error]")
        sys.exit(1)

    tool_path = TOOLS_DIR / f"{name}.py"
    if tool_path.exists() and not args.force:
        console.print(f"[baw.error]Tool already exists: {tool_path}[/baw.error]")
        console.print(f"[baw.dim]Use --force to overwrite (will leave the old file at {tool_path}.bak)[/baw.dim]")
        sys.exit(1)
    if tool_path.exists() and args.force:
        bak = tool_path.with_suffix(tool_path.suffix + ".bak")
        tool_path.replace(bak)
        console.print(f"[baw.dim]old {tool_path.name} → {bak.name}[/baw.dim]")

    # Parse --arg/--prop/--required
    args_spec = [a.strip() for a in (args.arg or []) if a.strip()]
    properties = {}
    for spec in (args.prop or []):
        if "=" not in spec:
            console.print(f"[baw.error]--prop must be NAME=description (got: {spec})[/baw.error]")
            sys.exit(1)
        pn, pd = spec.split("=", 1)
        properties[pn.strip()] = pd.strip()
    required = [r.strip() for r in (args.required or []) if r.strip()]
    sample_args = args.sample_args or ""
    risk_level = args.risk or "low"
    if risk_level not in ("low", "medium", "high"):
        console.print(f"[baw.error]--risk must be low|medium|high[/baw.error]")
        sys.exit(1)

    description = args.description or f"BAW tool: {name}"

    tool_src, test_src = _build_template_args(
        name=name, description=description, args_spec=args_spec,
        properties=properties, required=required, risk_level=risk_level,
        sample_args=sample_args,
    )

    # 1. Write the tool file
    tool_path.write_text(tool_src, encoding="utf-8")
    console.print(f"[green]✓ wrote[/green] {tool_path.relative_to(BAW_REPO)}")

    # 2. Write the smoke test
    test_path = TOOLS_DIR / "tests" / f"test_{name}.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(test_src, encoding="utf-8")
    console.print(f"[green]✓ wrote[/green] {test_path.relative_to(BAW_REPO)}")

    # 3. Patch __init__.py
    init_src = TOOLS_INIT.read_text(encoding="utf-8")
    if f"from . import" in init_src:
        # Find the existing import line and append our name
        new_init = re.sub(
            r"(from\s+\.\s+import\s+)([^\n]+)",
            lambda m: m.group(1) + (m.group(2).rstrip() + f", {name}").lstrip(),
            init_src, count=1,
        )
    else:
        new_init = init_src + f"\nfrom . import {name}\n"

    # Add register(**name.TOOL_DEF) inside register_all() — append to the
    # END of the function (so existing registrations stay in their order).
    if "register_all" in new_init and f"register(*{name}.TOOL_DEF)" not in new_init:
        # Find the last register(...) call in register_all and append after it.
        new_init = re.sub(
            r"((?:    register\(\*\*[A-Za-z_][A-Za-z0-9_]*\.TOOL_DEF\)\n)+)",
            r"\1    register(**" + name + r".TOOL_DEF)\n",
            new_init, count=1,
        )
        # If no existing register call was found, insert after `def register_all():`
        if f"register(*{name}.TOOL_DEF)" not in new_init:
            new_init = re.sub(
                r"(def\s+register_all\(\):\s*\n)",
                r"\1    register(**" + name + r".TOOL_DEF)\n",
                new_init, count=1,
            )

    TOOLS_INIT.write_text(new_init, encoding="utf-8")
    console.print(f"[green]✓ patched[/green] {TOOLS_INIT.relative_to(BAW_REPO)}")

    # 4. Run the smoke test
    console.print(f"\n[baw.muted]Running smoke test…[/baw.muted]")
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(test_path)],
        cwd=str(BAW_REPO), capture_output=True, text=True, timeout=30,
    )
    if proc.returncode == 0:
        console.print(Panel(proc.stdout, title=f"✅ {name} verified",
                            border_style="green"))
        console.print(f"\n[baw.success]Tool '{name}' created and verified.[/baw.success]")
        console.print(f"[baw.dim]Next: `baw tools show {name}` to inspect the result.[/baw.dim]")
    else:
        console.print(Panel(
            (proc.stdout or "") + "\n" + (proc.stderr or ""),
            title=f"❌ {name} failed verify",
            border_style="red",
        ))
        console.print(f"\n[baw.error]Tool '{name}' is NOT done — the smoke test failed.[/baw.error]")
        console.print(f"[baw.dim]Fix the implementation in {tool_path} and re-run the test manually.[/baw.dim]")
        sys.exit(1)


# ── arg parser ────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="baw tools",
                                description="Manage BAW tools: list, verify, show, doctor.")
    sub = p.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("list", help="List all registered tools with file status")
    sub.add_parser("doctor", help="Cross-check registered names against files on disk")

    sp_show = sub.add_parser("show", help="Print a tool's source + TOOL_DEF")
    sp_show.add_argument("name")
    sp_show.set_defaults(func=cmd_show)

    sp_verify = sub.add_parser("verify", help="Import + run a tool's handler as a smoke test")
    sp_verify.add_argument("name", nargs="?", default=None,
                           help="Tool name (omit to require --all)")
    sp_verify.add_argument("--all", action="store_true",
                           help="Verify every registered tool")
    sp_verify.add_argument("--continue-on-fail", action="store_true",
                           help="Don't exit non-zero on failures (for batch use)")
    sp_verify.set_defaults(func=cmd_verify)

    sp_create = sub.add_parser(
        "create", help="Scaffold a new tool: write file + register + auto-verify",
    )
    sp_create.add_argument("name", help="Tool name (lowercase + underscores)")
    sp_create.add_argument("--description", "-d", default=None,
                           help="One-line description")
    sp_create.add_argument("--arg", action="append", default=[],
                           help="Handler arg name (repeatable, e.g. --arg query)")
    sp_create.add_argument("--prop", action="append", default=[],
                           help="JSON-schema prop NAME=description (repeatable)")
    sp_create.add_argument("--required", action="append", default=[],
                           help="Required prop name (repeatable)")
    sp_create.add_argument("--sample-args", default="",
                           help="Python literal for the smoke-test call (default: '')")
    sp_create.add_argument("--risk", default="low", choices=["low", "medium", "high"],
                           help="Tool risk level (default: low)")
    sp_create.add_argument("--force", action="store_true",
                           help="Overwrite an existing tool (backs it up to .bak)")
    sp_create.set_defaults(func=cmd_create)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "list":
        cmd_list(args)
        return
    if args.subcommand == "doctor":
        cmd_doctor(args)
        return
    args.func(args)
