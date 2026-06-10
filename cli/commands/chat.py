"""baw chat — interactive chat session (like `hermes` CLI)."""
from __future__ import annotations
import os
import sys
import json
import readline
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.panel import Panel

import yaml
from openai import OpenAI

BAW_HOME = Path.home() / ".baw"
console = Console()


def _load_config():
    merged = {}
    for p in [Path("/app/config.yaml"), BAW_HOME / "config.yaml"]:
        if p.exists():
            merged = {**merged, **(yaml.safe_load(p.read_text()) or {})}
    return merged


def _load_env():
    env = dict(os.environ)
    env_path = BAW_HOME / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() not in env:
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _make_client(config, env):
    default_model = config.get("model", {}).get("default", "deepseek-v4-flash")
    model_hints = {
        "deepseek": "deepseek", "MiniMax": "minimax", "claude": "anthropic",
        "gemini": "google", "gpt": "openai", "grok": "xai",
    }
    provider_name = "deepseek"
    for hint, prov in model_hints.items():
        if hint.lower() in default_model.lower():
            provider_name = prov
            break
    pcfg = config.get("providers", {}).get(provider_name, {})
    base_url = pcfg.get("base_url", "https://api.deepseek.com/v1")
    api_key_env = pcfg.get("api_key_env", "DEEPSEEK_API_KEY")
    api_key = env.get(api_key_env, "")
    return OpenAI(api_key=api_key, base_url=base_url), default_model, provider_name


def _build_system_prompt():
    soul_path = BAW_HOME / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text()
    return "You are BAW, a helpful AI assistant. Respond in Traditional Chinese (繁體中文)."


def _handle_slash(cmd, messages):
    cmd = cmd.lower().strip()
    if cmd in ("/exit", "/quit", "/q"):
        console.print("\n[dim]👋 Bye![/dim]")
        sys.exit(0)
    elif cmd == "/help":
        console.print(Panel("""
[bold]/help[/bold]    — Show this
[bold]/clear[/bold]   — Clear conversation history
[bold]/model[/bold]   — Show current model
[bold]/soul[/bold]    — Show system prompt preview
[bold]/exit[/bold]    — Quit
        """.strip(), title="Commands", border_style="dim"))
    elif cmd == "/clear":
        soul = _build_system_prompt()
        messages.clear()
        messages.append({"role": "system", "content": soul})
        console.print("[green]✓ Conversation cleared.[/green]")
    elif cmd == "/model":
        console.print("[dim]Current model shown in header.[/dim]")
    elif cmd == "/soul":
        soul = _build_system_prompt()
        preview = soul[:300] + ("..." if len(soul) > 300 else "")
        console.print(Panel(preview, title="SOUL.md Preview", border_style="magenta"))
    else:
        console.print(f"[dim]Unknown command: {cmd} — type /help[/dim]")
    console.print()


def _save_session(messages, model):
    sessions_dir = BAW_HOME / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = sessions_dir / f"chat_{ts}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    console.print(f"[dim]💾 Saved: {path}[/dim]")


def cmd_chat(args=None):
    """Interactive chat session — `baw` or `baw chat`."""
    config = _load_config()
    env = _load_env()
    client, model, provider = _make_client(config, env)
    system_prompt = _build_system_prompt()

    # Splash
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]🤖 BAW Chat[/bold cyan]\n"
        f"[dim]Model: [/dim][yellow]{model}[/yellow]  "
        f"[dim]Provider: [/dim][green]{provider}[/green]",
        border_style="cyan",
    ))
    console.print("[dim]Type /help for commands, /exit to quit.[/dim]\n")

    messages = [{"role": "system", "content": system_prompt}]

    while True:
        try:
            user_input = console.input("[bold cyan]You ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]👋 Bye![/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            _handle_slash(user_input, messages)
            continue

        messages.append({"role": "user", "content": user_input})
        console.print()
        console.print("[bold yellow]BAW ›[/bold yellow] ", end="")

        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, stream=True,
            )
            full = ""
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    console.print(text, end="", highlight=False)
                    full += text
            console.print("\n")
            messages.append({"role": "assistant", "content": full})
        except Exception as e:
            console.print(f"\n[red]❌ Error: {e}[/red]\n")

    _save_session(messages, model)
