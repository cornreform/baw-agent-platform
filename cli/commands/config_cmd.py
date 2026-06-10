"""baw config — view and edit BAW configuration."""
import os
import subprocess
from pathlib import Path
from rich.panel import Panel
from rich.syntax import Syntax
from cli import console

BAW_HOME = Path.home() / ".baw"
CONFIG_PATH = BAW_HOME / "config.yaml"
ENV_PATH = BAW_HOME / ".env"


def cmd_config(subcommand: str | None, args: list[str]):
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
        console.print("[baw.muted]Try: config show | edit | get <key> | set <key> <val>[/baw.muted]")


def _config_show():
    if not CONFIG_PATH.exists():
        console.print("[baw.error]Config not found.[/baw.error] Run baw --setup first.")
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
                masked = val[:8] + "..." + val[-4:] if len(val) > 12 else "***"
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
    subprocess.call([editor, str(path)])


def _config_get(args: list[str]):
    if not args:
        console.print("[baw.error]Usage:[/baw.error] baw config get <key>")
        return
    import yaml
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    key = args[0]
    parts = key.split(".")
    val = cfg
    for p in parts:
        if isinstance(val, dict) and p in val:
            val = val[p]
        else:
            console.print(f"[baw.error]Key not found:[/baw.error] {key}")
            return
    console.print(f"[baw.key]{key}:[/baw.key] [baw.value]{val}[/baw.value]")


def _config_set(args: list[str]):
    if len(args) < 2:
        console.print("[baw.error]Usage:[/baw.error] baw config set <key> <value>")
        return
    import yaml
    key, val_str = args[0], " ".join(args[1:])
    cfg = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}

    # Try to parse value as YAML (for numbers, bools, etc.)
    try:
        val = yaml.safe_load(val_str)
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
