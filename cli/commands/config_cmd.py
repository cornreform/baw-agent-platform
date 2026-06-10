"""baw config — view and edit BAW configuration."""
import os
import sys
import subprocess
from pathlib import Path
from rich.panel import Panel
from rich.syntax import Syntax
from cli import console

BAW_HOME = Path.home() / ".baw"
CONFIG_PATH = BAW_HOME / "config.yaml"
ENV_PATH = BAW_HOME / ".env"


def cmd_config(subcommand: str | None = None, args: list[str] | None = None):
    if subcommand is None or subcommand == "show":
        _config_show()
    elif subcommand == "edit":
        _config_edit(args)
    elif subcommand == "get":
        _config_get(args)
    elif subcommand == "set":
        _config_set(args)
    else:
        console.print(f"[baw.error]Unknown config subcommand:[/baw.error] {subcommand}")
        console.print("[baw.muted]Try: config show | edit | get <key> | set <key> <val> [--raw][/baw.muted]")


def _config_show():
    if not CONFIG_PATH.exists():
        console.print("[baw.error]Config not found. Run baw setup first.[/baw.error]")
        return

    content = CONFIG_PATH.read_text()
    syntax = Syntax(content, "yaml", theme="monokai", line_numbers=True,
                    word_wrap=True, background_color="default")
    console.print(Panel(syntax, title=f"📄 {CONFIG_PATH}", border_style="baw.border"))

    # Also show .env masked
    if ENV_PATH.exists():
        console.print()
        env_lines = []
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                val = val.strip().strip('"').strip("'")
                if len(val) > 12:
                    masked = f"{val[:8]}...{val[-4:]}"
                elif len(val) > 4:
                    masked = f"{val[:2]}...{val[-2:]}"
                else:
                    masked = "***"
                env_lines.append(f"{key}={masked}")
            else:
                env_lines.append(line)
        syntax_env = Syntax("\n".join(env_lines), "bash", theme="monokai",
                            background_color="default")
        console.print(Panel(syntax_env, title="🔐 .env (masked)", border_style="baw.border"))


def _config_edit(args: list[str]):
    editor = os.environ.get("EDITOR", "nano")
    path = CONFIG_PATH
    if args and args[0] == "env":
        path = ENV_PATH
    try:
        result = subprocess.call([editor, str(path)])
        if result != 0:
            console.print(f"[baw.warning]⚠ Editor exited with code {result}[/baw.warning]")
    except FileNotFoundError:
        console.print(f"[baw.error]✗ Editor '{editor}' not found.[/baw.error]")
        console.print("[baw.dim]Set $EDITOR or install nano/vim.[/baw.dim]")


def _config_get(args: list[str]):
    if not args:
        console.print("[baw.error]Usage:[/baw.error] baw config get <key>")
        return
    import yaml
    if not CONFIG_PATH.exists():
        console.print("[baw.error]Config not found.[/baw.error]")
        return
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    key = args[0]
    parts = key.split(".")
    val = cfg
    for p in parts:
        if isinstance(val, dict) and p in val:
            val = val[p]
        else:
            console.print(f"[baw.error]Key not found:[/baw.error] {key}")
            sys.exit(1)
    console.print(f"[baw.key]{key}:[/baw.key] [baw.value]{val}[/baw.value]")


def _config_set(args: list[str]):
    if len(args) < 2:
        console.print("[baw.error]Usage:[/baw.error] baw config set <key> <value> [--raw]")
        return
    import yaml

    # Parse --raw flag
    raw = False
    clean_args = []
    for a in args:
        if a == "--raw":
            raw = True
        else:
            clean_args.append(a)

    if len(clean_args) < 2:
        console.print("[baw.error]Usage:[/baw.error] baw config set <key> <value> [--raw]")
        return

    key, val_str = clean_args[0], " ".join(clean_args[1:])
    if not CONFIG_PATH.exists():
        console.print("[baw.error]Config not found. Run baw setup first.[/baw.error]")
        return

    cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}

    # Parse value: --raw keeps string, otherwise try YAML first then fallback
    if raw:
        val = val_str
    else:
        try:
            val = yaml.safe_load(val_str)
            # yaml.safe_load("123") → 123, "true" → True, "null" → None
            # But for simple strings like "casual" it stays string
            # If val_str looks like a YAML value (bool/number/null), keep parsed;
            # otherwise fallback to string
        except Exception:
            val = val_str

    parts = key.split(".")
    target = cfg
    for p in parts[:-1]:
        if p not in target:
            target[p] = {}
        target = target[p]
    target[parts[-1]] = val

    CONFIG_PATH.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False))
    console.print(f"[baw.success]✓[/baw.success] Set [baw.key]{key}[/baw.key] = [baw.value]{val}[/baw.value]")
