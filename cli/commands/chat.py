"""baw chat — interactive chat REPL.

BAW-identity-first design.  NOT a Hermes clone.
Visual style: dark slate background, purple/gold accents, BAW brand.
"""

from __future__ import annotations
import json
import os
import readline
import shutil
import sys
import textwrap
import time
import uuid
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.layout import Layout
from rich import box

from rich.theme import Theme

# ── BAW brand theme ───────────────────────────────────────────────
BAW_THEME = Theme({
    "baw.accent":    "#c4a0ff",   # soft purple
    "baw.bold":      "#e2b04a",   # warm gold
    "baw.dim":       "#6e7681",   # muted grey
    "baw.error":     "#f85149",   # red
    "baw.success":   "#3fb950",   # green
    "baw.prompt":    "#c4a0ff",   # purple prompt
    "baw.user":      "#e6edf3",   # light text
    "baw.ai":        "#b0b8c0",   # muted text for AI
})

console = Console(theme=BAW_THEME, highlight=False)

BAW_HOME = Path.home() / ".baw"
BAW_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_soul() -> str:
    """Load SOUL.md for system prompt."""
    soul_path = BAW_HOME / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")
    return "You are BAW, an autonomous AI agent."


def _load_config():
    """Load merged config."""
    import yaml
    config = {}
    for p in [BAW_ROOT / "config.yaml", BAW_HOME / "config.yaml"]:
        if p.exists():
            cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            config = _deep_merge(config, cfg)
    return config


def _deep_merge(base, override):
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _get_api_key(config):
    """Resolve API key from config."""
    provider_name = config.get("model", {}).get("provider") or list(config.get("providers", {}).keys())[:1]
    if isinstance(provider_name, list):
        provider_name = provider_name[0] if provider_name else "deepseek"
    if provider_name:
        pinfo = config.get("providers", {}).get(provider_name, {})
        env_var = pinfo.get("api_key_env", "")
        if env_var:
            # Try env, then ~/.baw/.env
            val = os.environ.get(env_var)
            if not val:
                env_path = BAW_HOME / ".env"
                if env_path.exists():
                    for line in env_path.read_text().splitlines():
                        if line.startswith(f"{env_var}=") and "=" in line:
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
            return val
    return os.environ.get("OPENAI_API_KEY", "")


def _print_welcome(config):
    """Print BAW brand splash screen."""
    model = config.get("model", {}).get("default", "?")
    w = shutil.get_terminal_size().columns

    console.print()
    # Banner
    banner = Text()
    banner.append("╭", style="baw.dim")
    banner.append("─" * (w - 2), style="baw.dim")
    banner.append("╮", style="baw.dim")
    console.print(banner)

    inner = Text()
    inner.append("  🖤  ", style="bold white")
    inner.append("BAW", style="bold #c4a0ff")
    inner.append(" Agent Platform", style="bold white")
    pad = w - 25
    inner.append(" " * pad, style="")
    console.print(inner)

    banner2 = Text()
    banner2.append("╰", style="baw.dim")
    banner2.append("─" * (w - 2), style="baw.dim")
    banner2.append("╯", style="baw.dim")
    console.print(banner2)

    console.print()
    console.print(f"  [baw.dim]model:[/baw.dim] [baw.bold]{model}[/baw.bold]    [baw.dim]sessions:[/baw.dim] [baw.accent]~/.baw/sessions/[/baw.accent]    [baw.dim]help:[/baw.dim] [baw.accent]/help[/baw.accent]")
    console.print()


def _stream_response(client, model_id, messages):
    """Stream LLM response with Live spinner."""
    spinner = Spinner("dots2", text="[baw.accent]thinking…[/baw.accent]", style="baw.dim")
    full_text = ""

    with Live(spinner, console=console, refresh_per_second=10, transient=True) as live:
        try:
            stream = client.chat.completions.create(
                model=model_id,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
            )
            first_chunk = True
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    if first_chunk:
                        live.stop()
                        first_chunk = False
                    content = chunk.choices[0].delta.content
                    full_text += content
                    console.print(content, end="", style="baw.user")
            if not first_chunk:
                console.print()
        except Exception as e:
            live.stop()
            console.print(f"\n[baw.error]✗ API error:[/baw.error] {e}")
            return None

    return full_text


def _save_session(session_id, messages):
    """Save session to JSONL."""
    sessions_dir = BAW_HOME / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{session_id}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")


def cmd_chat():
    """Interactive chat mode — BAW identity."""
    try:
        from openai import OpenAI
    except ImportError:
        console.print("[baw.error]✗ openai package not installed[/baw.error]")
        sys.exit(1)

    config = _load_config()
    model_id = config.get("model", {}).get("default", "deepseek-v4-flash")
    provider_name = config.get("model", {}).get("provider", "deepseek")
    pinfo = config.get("providers", {}).get(provider_name, {})
    base_url = pinfo.get("base_url", "https://api.deepseek.com/v1")
    api_key = _get_api_key(config)

    if not api_key:
        console.print("[baw.error]✗ No API key found. Set it in ~/.baw/.env[/baw.error]")
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url=base_url)

    soul = _load_soul()
    system_msg = {
        "role": "system",
        "content": f"""You are BAW — an autonomous AI agent running inside a Docker container on a Linux SBC.

{soul}

Style rules:
- Use Traditional Chinese (繁體中文) when the user speaks Chinese
- Be concise, witty, and action-oriented
- Lead with results, not process descriptions
- This is a CLI chat interface — no markdown tables, keep formatting simple
- Your identity is BAW, NOT Hermes. Never say "Hermes" or "Sticky".
- If asked about your environment: "I run in a Docker container on a Dragon Q6A ARM64 SBC."
""",
    }
    messages = [system_msg]

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    _print_welcome(config)

    while True:
        try:
            user_input = input("\033[38;2;196;160;255m⚡ \033[0m")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[baw.dim]👋 bye[/baw.dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # ── slash commands ────────────────────────────────────────
        if user_input.startswith("/"):
            handled = _handle_slash(user_input, config, messages)
            if handled == "EXIT":
                _save_session(session_id, messages)
                break
            elif handled == "CLEAR":
                messages = [system_msg]
                console.print("[baw.dim]🧹 Cleared[/baw.dim]")
                continue
            elif handled:
                continue
            # Unknown slash command — send as normal message

        messages.append({"role": "user", "content": user_input})
        response = _stream_response(client, model_id, messages)

        if response:
            messages.append({"role": "assistant", "content": response})
            console.print()  # blank line after response


def _handle_slash(cmd: str, config, messages) -> str | bool | None:
    """Handle slash commands. Returns 'EXIT', 'CLEAR', True (handled), or None."""
    parts = cmd.split()
    verb = parts[0].lower()

    if verb in ("/exit", "/quit", "/q"):
        return "EXIT"

    if verb in ("/clear", "/reset"):
        return "CLEAR"

    if verb == "/help":
        _show_chat_help()
        return True

    if verb == "/model":
        if len(parts) > 1:
            model = parts[1]
            config["model"]["default"] = model
            console.print(f"[baw.success]✓ model → {model}[/baw.success]")
        else:
            current = config.get("model", {}).get("default", "?")
            console.print(f"[baw.accent]current:[/baw.accent] {current}")
            console.print("[baw.dim]usage: /model <name>[/baw.dim]")
        return True

    if verb in ("/soul", "/identity"):
        soul_path = BAW_HOME / "SOUL.md"
        if soul_path.exists():
            console.print(Panel(soul_path.read_text(encoding="utf-8"),
                                title="SOUL.md", border_style="#6c3fc9"))
        else:
            console.print("[baw.error]SOUL.md not found[/baw.error]")
        return True

    if verb == "/session":
        console.print(f"[baw.accent]session:[/baw.accent] [baw.dim]{messages[0].get('session_id', 'N/A')}[/baw.dim]")
        console.print(f"[baw.accent]messages:[/baw.accent] [baw.dim]{len(messages)}[/baw.dim]")
        return True

    if verb == "/config":
        import yaml
        # Show config but mask secrets
        safe = dict(config)
        for pk, pv in safe.get("providers", {}).items():
            if "api_key" in pv:
                pv["api_key"] = "***"
        console.print(Panel(yaml.dump(safe, allow_unicode=True, default_flow_style=False),
                            title="config.yaml", border_style="#6c3fc9"))
        return True

    return None


def _show_chat_help():
    """Show chat-mode slash commands."""
    from rich.table import Table
    t = Table(box=box.SIMPLE, border_style="#6c3fc9", show_header=False,
              pad_edge=False, expand=True)
    t.add_column("cmd", style="bold #c4a0ff", width=14, no_wrap=True)
    t.add_column("desc", style="#b0b8c0")
    t.add_row("/help",     "this help")
    t.add_row("/model [n]", "switch model")
    t.add_row("/soul",     "view SOUL.md")
    t.add_row("/config",   "view config")
    t.add_row("/session",  "session info")
    t.add_row("/clear",    "reset chat")
    t.add_row("/exit",     "quit")
    console.print(t)
