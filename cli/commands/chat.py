"""baw chat — interactive chat REPL with BAW identity visual design.

Purple (#8b5cf6) + Gold (#e2b714) theme on dark background.
Rich live-streaming responses. Slash commands for session control.
"""
from __future__ import annotations
import json
import os
import readline
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.table import Table
from rich import box
from rich.theme import Theme

PURPLE = "#8b5cf6"
GOLD = "#e2b714"
SOFT_PURPLE = "#c4a0ff"
DIM = "#6e7681"

BAW_THEME = Theme({
    "p": SOFT_PURPLE,
    "g": GOLD,
    "d": DIM,
    "e": "#f85149",
    "ok": "#3fb950",
    "w": "#e6edf3",
    "ai": "#b0b8c0",
})

console = Console(theme=BAW_THEME, highlight=False)
BAW_HOME = Path.home() / ".baw"
BAW_ROOT = Path(__file__).resolve().parent.parent.parent

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _soul() -> str:
    p = BAW_HOME / "SOUL.md"
    return p.read_text() if p.exists() else "BAW — autonomous agent."

def _cfg():
    import yaml
    c = {}
    for p in [BAW_ROOT / "config.yaml", BAW_HOME / "config.yaml"]:
        if p.exists():
            c = _merge(c, yaml.safe_load(p.read_text()) or {})
    return c

def _merge(a, b):
    r = dict(a)
    for k, v in b.items():
        if k in r and isinstance(r[k], dict) and isinstance(v, dict):
            r[k] = _merge(r[k], v)
        else:
            r[k] = v
    return r

def _key(cfg):
    pname = cfg.get("model", {}).get("provider") or list(cfg.get("providers", {}))[:1]
    if isinstance(pname, list): pname = pname[0] if pname else "deepseek"
    pinfo = cfg.get("providers", {}).get(pname, {})
    env = pinfo.get("api_key_env", "")
    if env:
        val = os.environ.get(env)
        if not val:
            ep = BAW_HOME / ".env"
            if ep.exists():
                for ln in ep.read_text().splitlines():
                    if ln.startswith(f"{env}=") and "=" in ln:
                        val = ln.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        return val
    return os.environ.get("OPENAI_API_KEY", "")

def _model_count(cfg) -> str:
    n = 0
    for pv in cfg.get("providers", {}).values():
        n += len(pv.get("models", []))
    return str(n)

def _session_count() -> str:
    sd = BAW_HOME / "sessions"
    if sd.exists():
        return str(len(list(sd.glob("*.jsonl"))))
    return "0"

def _memory_size() -> str:
    md = BAW_HOME / "memory"
    if md.exists():
        sz = sum(f.stat().st_size for f in md.rglob("*") if f.is_file())
        if sz > 1_000_000: return f"{sz/1_000_000:.1f}MB"
        return f"{sz/1000:.0f}KB"
    return "—"


# ═══════════════════════════════════════════════════════════════════════════════
# Welcome screen
# ═══════════════════════════════════════════════════════════════════════════════

BAW_BANNER = r"""
    ██████╗  █████╗ ██╗    ██╗
    ██╔══██╗██╔══██╗██║    ██║
    ██████╔╝███████║██║ █╗ ██║
    ██╔══██╗██╔══██╗██║███╗██║
    ██████╔╝██║  ██║╚███╔███╔╝
    ╚═════╝ ╚═╝  ╚═╝ ╚══╝╚══╝
"""

def _welcome(cfg):
    model = cfg.get("model", {}).get("default", "?")
    provider = cfg.get("model", {}).get("provider", "?")
    w = max(shutil.get_terminal_size().columns, 60)

    # Banner
    for line in BAW_BANNER.splitlines():
        if line.strip():
            console.print(Text(line, style=f"bold {PURPLE}", justify="center"))

    console.print(Text("Black And White · Agent Platform", style=f"italic {GOLD}", justify="center"))
    console.print()

    # Stats bar
    bar = Table(show_header=False, box=None, padding=(0, 2), expand=True, show_edge=False)
    bar.add_column(style=GOLD, width=3, justify="center")
    bar.add_column(style="w", width=3)
    bar.add_column(style=DIM)
    bar.add_column(style=GOLD, width=3, justify="center")
    bar.add_column(style="w", width=3)
    bar.add_column(style=DIM)
    bar.add_column(style=GOLD, width=3, justify="center")
    bar.add_column(style="w", width=3)
    bar.add_column(style=DIM)

    bar.add_row("🤖", _model_count(cfg), "models",
                "📂", _session_count(), "sessions",
                "🧠", _memory_size(), "memory")
    console.print(bar)
    console.print()

    # Model info
    info = Table(show_header=False, box=box.SIMPLE, border_style=SOFT_PURPLE,
                 padding=(0, 2), expand=True)
    info.add_column(style=f"bold {SOFT_PURPLE}", width=12)
    info.add_column(style="w")
    info.add_row("model", f"[g]{model}[/g]")
    info.add_row("provider", provider or "[d]auto[/d]")
    info.add_row("commands", "/help /model /soul /config /session /clear /exit")
    console.print(info)
    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# Streaming
# ═══════════════════════════════════════════════════════════════════════════════

def _stream(client, model_id, messages):
    spinner = Spinner("dots2", text=f"[d]{model_id} thinking…[/d]", style="d")
    full = ""

    with Live(spinner, console=console, refresh_per_second=10, transient=True) as live:
        try:
            for chunk in client.chat.completions.create(
                model=model_id, messages=messages, stream=True,
                stream_options={"include_usage": True},
            ):
                if chunk.choices and chunk.choices[0].delta.content:
                    if live.is_started:
                        live.stop()
                        console.print(Text("🖤  BAW", style=f"bold {GOLD}"))
                    c = chunk.choices[0].delta.content
                    full += c
                    console.print(c, end="", style="w")
            if live.is_started:
                live.stop()
                console.print(Text("🖤  BAW", style=f"bold {GOLD}"))
            console.print()
        except Exception as e:
            live.stop()
            console.print(f"\n[e]✗ API error:[/e] {e}")
            return None
    return full


# ═══════════════════════════════════════════════════════════════════════════════
# Slash commands
# ═══════════════════════════════════════════════════════════════════════════════

def _slash(cmd: str, cfg, msgs) -> str | bool | None:
    parts = cmd.split()
    v = parts[0].lower()

    if v in ("/exit", "/quit", "/q"): return "EXIT"
    if v in ("/clear", "/reset"): return "CLEAR"

    if v == "/help":
        t = Table(box=box.SIMPLE, border_style=SOFT_PURPLE, show_header=False, pad_edge=False, expand=True)
        t.add_column("cmd", style=f"bold {SOFT_PURPLE}", width=12, no_wrap=True)
        t.add_column("desc", style="ai")
        t.add_row("/help",     "this help")
        t.add_row("/model [n]", "switch model")
        t.add_row("/soul",     "view SOUL.md")
        t.add_row("/config",   "view config")
        t.add_row("/session",  "session info")
        t.add_row("/clear",    "reset chat")
        t.add_row("/exit",     "quit")
        console.print(t)
        return True

    if v == "/model":
        if len(parts) > 1:
            cfg["model"]["default"] = parts[1]
            console.print(f"[ok]✓ model → {parts[1]}[/ok]")
        else:
            console.print(f"[p]current:[/p] [g]{cfg.get('model',{}).get('default','?')}[/g]")
            console.print("[d]usage: /model <name>[/d]")
        return True

    if v in ("/soul", "/identity"):
        sp = BAW_HOME / "SOUL.md"
        console.print(Panel(sp.read_text() if sp.exists() else "[d]SOUL.md not found[/d]",
                            title="🧠 SOUL.md", border_style=PURPLE))
        return True

    if v == "/session":
        sid = msgs[0].get("session_id", "N/A") if msgs else "N/A"
        console.print(f"[p]session:[/p] [d]{sid}[/d]  [p]msgs:[/p] [d]{len(msgs)}[/d]")
        return True

    if v == "/config":
        import yaml
        safe = dict(cfg)
        for pk, pv in safe.get("providers", {}).items():
            if "api_key_env" in pv:
                pv["api_key_env"] = pv["api_key_env"] + "=***"
        console.print(Panel(yaml.dump(safe, allow_unicode=True, default_flow_style=False),
                            title="📄 config.yaml", border_style=PURPLE))
        return True

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_chat():
    cfg = _cfg()
    mid = cfg.get("model", {}).get("default", "deepseek-v4-flash")
    pname = cfg.get("model", {}).get("provider", "deepseek")
    pinfo = cfg.get("providers", {}).get(pname, {})
    base = pinfo.get("base_url", "https://api.deepseek.com/v1")
    key = _key(cfg)

    if not key:
        console.print("[e]✗ No API key. Set it in ~/.baw/.env[/e]")
        sys.exit(1)

    client = OpenAI(api_key=key, base_url=base)
    soul = _soul()
    sysprompt = {
        "role": "system",
        "content": f"""You are BAW — an autonomous AI agent inside a Docker container on Dragon Q6A ARM64.

{soul}

Style:
- Traditional Chinese (繁體中文) when user speaks Chinese
- Concise, witty, action-oriented. Lead with results.
- CLI chat — no markdown tables, keep formatting simple
- Identity: BAW. Never say "Hermes" or "Sticky".
- Environment: "Docker container on Dragon Q6A ARM64 SBC." """,
    }
    msgs = [sysprompt]
    sid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    _welcome(cfg)

    while True:
        try:
            inp = input("\033[38;2;196;160;255m⚡ \033[0m")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[d]👋 bye[/d]")
            break

        inp = inp.strip()
        if not inp: continue

        if inp.startswith("/"):
            r = _slash(inp, cfg, msgs)
            if r == "EXIT":
                _save(sid, msgs)
                break
            if r == "CLEAR":
                msgs = [sysprompt]
                console.print("[d]🧹 Cleared[/d]")
                continue
            if r: continue

        msgs.append({"role": "user", "content": inp})
        resp = _stream(client, mid, msgs)
        if resp:
            msgs.append({"role": "assistant", "content": resp})
            console.print()


def _save(sid, msgs):
    d = BAW_HOME / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    with open(p, "a") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
