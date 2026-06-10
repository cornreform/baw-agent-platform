"""baw chat — interactive chat REPL. Multi-layered welcome, BAW Purple+Gold identity.
Streaming replies via DeepSeek API. Supports slash commands (/help /model /soul etc).
"""
from __future__ import annotations
import json, os, sys, uuid
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
from rich.rule import Rule

from cli import console, BAW_THEME
# Secondary console for user input (avoids Rich markup collision)
plain_console = Console(theme=BAW_THEME, highlight=False)

BAW_HOME = Path.home() / ".baw"
BAW_ROOT = Path(__file__).resolve().parent.parent.parent

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

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
    if isinstance(pname, list):
        pname = pname[0] if pname else "deepseek"
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
        if sz > 1_000_000:
            return f"{sz/1_000_000:.1f}MB"
        return f"{sz/1000:.0f}KB"
    return "—"

def _recent_sessions_table(n: int = 5) -> Table | None:
    sd = BAW_HOME / "sessions"
    if not sd.exists():
        return None
    files = sorted(sd.glob("*.jsonl"), key=os.path.getmtime, reverse=True)[:n]
    if not files:
        return None
    t = Table(box=None, padding=(0, 1), expand=True, show_header=True)
    t.add_column("Session", style="baw.purple", width=14)
    t.add_column("Msgs", style="white", justify="right", width=5)
    t.add_column("Last", style="baw.muted")
    for f in files:
        lines = f.read_text().splitlines()
        mtime = _relative_time(os.path.getmtime(f))
        t.add_row(f.stem[:14], str(len(lines)), mtime)
    return t

def _relative_time(ts: float) -> str:
    import time
    diff = time.time() - ts
    if diff < 60: return f"{int(diff)}s"
    if diff < 3600: return f"{int(diff/60)}m"
    if diff < 86400: return f"{int(diff/3600)}h"
    return f"{int(diff/86400)}d"


# ═══════════════════════════════════════════════════════════════════════
# Welcome screen — multi-layered
# ═══════════════════════════════════════════════════════════════════════

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
    provider = cfg.get("model", {}).get("provider", "—")
    adv = cfg.get("adversarial", {})
    tone_name = cfg.get("tone", {}).get("default", "casual")
    tone_profiles = cfg.get("tone", {}).get("profiles", {})
    tone_desc = tone_profiles.get(tone_name, {}).get("description", "—")
    fc_mode = cfg.get("fact_check", {}).get("mode", "normal")
    caps = cfg.get("capabilities", {})

    # 1. Banner
    for line in BAW_BANNER.splitlines():
        if line.strip():
            plain_console.print(Text(line, style="baw.brand", justify="center"))
    plain_console.print(Text("Black And White · Agent Platform", style="baw.subtitle", justify="center"))
    plain_console.print(Rule(style="baw.accent"))

    # 2. Status bar — models / sessions / memory
    stats = Table(show_header=False, box=None, padding=(0, 2), expand=True, show_edge=False)
    for _ in range(6):
        stats.add_column(width=3, justify="center")
        stats.add_column(width=4)
    stats.add_row(
        "🤖", f"[baw.gold]{_model_count(cfg)}[/]", "[baw.muted]models[/]",
        "📂", f"[baw.gold]{_session_count()}[/]", "[baw.muted]sessions[/]",
        "🧠", f"[baw.gold]{_memory_size()}[/]", "[baw.muted]memory[/]",
    )
    plain_console.print(stats)

    # 3. Active model + tone + fact-check + adversarial
    info = Table(show_header=False, box=box.SIMPLE, border_style="baw.accent",
                 padding=(0, 2), expand=True)
    info.add_column(style="baw.key", width=14)
    info.add_column(style="baw.val")
    info.add_row("model", f"[baw.gold]{model}[/]")
    info.add_row("provider", provider or "[baw.muted]auto[/]")
    info.add_row("tone", f"[baw.purple]{tone_name}[/] [baw.muted]({tone_desc})[/]")

    # Fact-check mode with color
    fc_colors = {"strict": "red", "normal": "yellow", "relaxed": "green"}
    fc_color = fc_colors.get(fc_mode, "white")
    info.add_row("fact-check", f"[{fc_color}]{fc_mode}[/]")

    # Angel / Devil (always show)
    adv_enabled = adv.get("enabled", False)
    devil_model = adv.get("devil_model", model)
    angel_model = model  # Angel uses default model
    info.add_row("😇 Angel", f"[green]{angel_model}[/]")
    info.add_row("👿 Devil", f"[red]{devil_model}[/]")
    if not adv_enabled:
        info.add_row("adversarial", "[baw.muted]disabled[/]")

    plain_console.print(info)
    plain_console.print()

    # 4. Capability routing
    cap_table = Table(show_header=False, box=None, padding=(0, 1), expand=True, show_edge=False)
    cap_table.add_column(style="baw.purple", width=16)
    cap_table.add_column(style="baw.gold")
    cap_table.add_column(style="baw.purple", width=16)
    cap_table.add_column(style="baw.gold")
    cap_items = []
    for cap in ("chat", "vision", "tts", "stt", "image_generation", "browser"):
        cap_cfg = caps.get(cap, {})
        m = cap_cfg.get("model", "") if isinstance(cap_cfg, dict) else ""
        method = cap_cfg.get("method", "") if isinstance(cap_cfg, dict) else ""
        label = m or method or "[baw.muted]—[/]"
        if label:
            cap_items.append((cap, label))
    # Split into 2 columns
    for i in range(0, len(cap_items), 2):
        row = []
        for j in range(2):
            if i + j < len(cap_items):
                row.append(cap_items[i + j][0])
                row.append(cap_items[i + j][1])
            else:
                row += ["", ""]
        cap_table.add_row(*row)
    plain_console.print(Panel(cap_table, title="⚡ Capability Routing",
                              border_style="baw.accent", title_align="left"))

    # 5. Slash commands
    cmds = Table(show_header=False, box=None, padding=(0, 1), expand=True, show_edge=False)
    cmds.add_column(style="baw.cmd", width=14, no_wrap=True)
    cmds.add_column(style="baw.muted")
    cmds.add_column(style="baw.cmd", width=14, no_wrap=True)
    cmds.add_column(style="baw.muted")
    cmds.add_column(style="baw.cmd", width=14, no_wrap=True)
    cmds.add_column(style="baw.muted")
    cmds.add_row("/help", "show help", "/model", "switch model", "/soul", "view SOUL")
    cmds.add_row("/config", "view config", "/session", "session info", "/clear", "reset chat")
    cmds.add_row("/exit", "quit", "", "", "", "")
    plain_console.print(Panel(cmds, title="⌨  Commands", border_style="baw.accent", title_align="left"))

    # 6. Recent sessions
    rt = _recent_sessions_table(5)
    if rt:
        plain_console.print()
        plain_console.print(Panel(rt, title="📂 Recent Sessions",
                                  border_style="baw.accent", title_align="left"))

    plain_console.print(Rule(style="baw.accent"))


# ═══════════════════════════════════════════════════════════════════════
# Streaming
# ═══════════════════════════════════════════════════════════════════════

def _stream(client, model_id, messages):
    spinner = Spinner("dots2", text=f"[baw.muted]{model_id} thinking…[/]", style="baw.muted")
    full = ""

    with Live(spinner, console=plain_console, refresh_per_second=10, transient=True) as live:
        try:
            for chunk in client.chat.completions.create(
                model=model_id, messages=messages, stream=True,
                stream_options={"include_usage": True},
            ):
                if chunk.choices and chunk.choices[0].delta.content:
                    if live.is_started:
                        live.stop()
                        plain_console.print(Text("🖤  BAW", style="baw.gold"))
                    c = chunk.choices[0].delta.content
                    full += c
                    plain_console.print(c, end="", style="white")
            if live.is_started:
                live.stop()
                plain_console.print(Text("🖤  BAW", style="baw.gold"))
            plain_console.print()
        except Exception as e:
            live.stop()
            plain_console.print(f"\n[baw.error]✗ API error:[/] {e}")
            return None
    return full


# ═══════════════════════════════════════════════════════════════════════
# Slash commands
# ═══════════════════════════════════════════════════════════════════════

def _slash(cmd: str, cfg, msgs) -> str | bool | None:
    parts = cmd.split()
    v = parts[0].lower()

    if v in ("/exit", "/quit", "/q"):
        return "EXIT"
    if v in ("/clear", "/reset"):
        return "CLEAR"

    if v == "/help":
        t = Table(box=box.SIMPLE, border_style="baw.accent", show_header=False,
                  pad_edge=False, expand=True)
        t.add_column("cmd", style="baw.cmd", width=14, no_wrap=True)
        t.add_column("desc", style="baw.muted")
        t.add_row("/help", "this help")
        t.add_row("/model [name]", "switch model")
        t.add_row("/soul", "view SOUL.md")
        t.add_row("/config", "view config")
        t.add_row("/session", "session info")
        t.add_row("/clear", "reset chat")
        t.add_row("/exit", "quit")
        plain_console.print(t)
        return True

    if v == "/model":
        if len(parts) > 1:
            cfg["model"]["default"] = parts[1]
            plain_console.print(f"[baw.success]✓ model → {parts[1]}[/]")
        else:
            plain_console.print(f"[baw.purple]current:[/] [baw.gold]{cfg.get('model',{}).get('default','?')}[/]")
            plain_console.print("[baw.muted]usage: /model <name>[/]")
        return True

    if v in ("/soul", "/identity"):
        sp = BAW_HOME / "SOUL.md"
        content = sp.read_text() if sp.exists() else "[baw.muted]SOUL.md not found[/]"
        plain_console.print(Panel(content, title="🧠 SOUL.md", border_style="baw.accent",
                                  title_align="left"))
        return True

    if v == "/session":
        sid = msgs[0].get("session_id", "N/A") if msgs else "N/A"
        plain_console.print(f"[baw.purple]session:[/] [baw.muted]{sid}[/]  "
                            f"[baw.purple]msgs:[/] [baw.muted]{len(msgs)}[/]")
        return True

    if v == "/config":
        import yaml
        safe = dict(cfg)
        for pk, pv in safe.get("providers", {}).items():
            if "api_key_env" in pv:
                pv["api_key_env"] = pv["api_key_env"] + "=***"
        plain_console.print(Panel(yaml.dump(safe, allow_unicode=True, default_flow_style=False),
                                  title="📄 config.yaml", border_style="baw.accent"))
        return True

    return None


# ═══════════════════════════════════════════════════════════════════════
# Main chat loop
# ═══════════════════════════════════════════════════════════════════════

def cmd_chat():
    cfg = _cfg()
    mid = cfg.get("model", {}).get("default", "deepseek-v4-flash")
    pname = cfg.get("model", {}).get("provider") or list(cfg.get("providers", {}))[:1]
    if isinstance(pname, list):
        pname = pname[0] if pname else "deepseek"
    pinfo = cfg.get("providers", {}).get(pname, {})
    base = pinfo.get("base_url", "https://api.deepseek.com/v1")
    key = _key(cfg)

    if not key:
        plain_console.print("[baw.error]✗ No API key. Set it in ~/.baw/.env[/]")
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

    # Check TTY status
    is_tty = sys.stdin.isatty()
    if not is_tty:
        plain_console.print("[baw.warning]⚠ stdin is not a TTY — chat may not work.[/]")
        plain_console.print("[baw.dim]Run 'docker exec -it baw-telegram python3 -m cli.main' instead.[/]")

    while True:
        try:
            if is_tty:
                inp = input("\033[35m⚡ \033[0m")
            else:
                sys.stdout.write("⚡ ")
                sys.stdout.flush()
                inp = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            plain_console.print("\n[baw.muted]👋 bye[/]")
            _save(sid, msgs)
            break

        inp = inp.strip()
        if not inp:
            continue

        try:
            if inp.startswith("/"):
                r = _slash(inp, cfg, msgs)
                if r == "EXIT":
                    _save(sid, msgs)
                    plain_console.print("[baw.muted]👋 bye[/]")
                    break
                if r == "CLEAR":
                    msgs = [sysprompt]
                    plain_console.print("[baw.muted]🧹 Cleared[/]")
                    continue
                if r:
                    continue

            msgs.append({"role": "user", "content": inp})
            resp = _stream(client, mid, msgs)
            if resp:
                msgs.append({"role": "assistant", "content": resp})
            else:
                plain_console.print("[baw.warning]⚠ No response received. Try again or /exit.[/]")
        except Exception as e:
            plain_console.print(f"[baw.error]✗ Error: {e}[/]")
            plain_console.print("[baw.dim]Type /help for commands, /exit to quit.[/]")


def _save(sid, msgs):
    d = BAW_HOME / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    with open(p, "a") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
