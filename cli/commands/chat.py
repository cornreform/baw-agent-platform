"""baw chat — interactive chat REPL with multi-turn tool calling.
Streaming replies with token/context/tone status bar.
Supports web_search, read_file, write_file tools (loop until text response).
"""
from __future__ import annotations
import json, os, sys, uuid
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from rich.panel import Panel
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text
from rich.table import Table
from rich import box
from rich.rule import Rule

from cli import console as plain_console

BAW_HOME = Path.home() / ".baw"
BAW_ROOT = Path(__file__).resolve().parent.parent.parent

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _soul() -> str:
    p = BAW_HOME / "SOUL.md"
    return p.read_text() if p.exists() else ""

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

def _resolve_provider(cfg, model_id: str):
    """Find which provider has this model, return (pname, pinfo)."""
    for pname, pinfo in cfg.get("providers", {}).items():
        for m in pinfo.get("models", []):
            if m.get("id") == model_id:
                return pname, pinfo
    # fallback: first provider
    pname = list(cfg.get("providers", {}))[:1]
    pname = pname[0] if pname else "deepseek"
    return pname, cfg.get("providers", {}).get(pname, {})

def _key(cfg):
    model_id = cfg.get("model", {}).get("default", "deepseek-v4-flash")
    pname, pinfo = _resolve_provider(cfg, model_id)
    return _key_for_provider(pinfo)

def _key_for_provider(pinfo):
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

def _human_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)

def _get_context_window(cfg, model_id: str) -> int:
    for pinfo in cfg.get("providers", {}).values():
        for m in pinfo.get("models", []):
            if m.get("id") == model_id:
                return m.get("context_window", 131072)
    return 131072

# ═══════════════════════════════════════════════════════════════════════
# Tool executors (CLI chat has web_search + read_file + write_file)
# ═══════════════════════════════════════════════════════════════════════

def _exec_web_search(query: str) -> str:
    """Execute web search via DuckDuckGo Lite POST and return formatted results."""
    try:
        import urllib.request, urllib.parse, re
        # POST to DuckDuckGo Lite (bypasses bot detection)
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        # Extract result links (exclude duckduckgo internal links)
        results = []
        all_links = re.findall(
            r'<a[^>]*href="(https?://[^\"]+)"[^>]*>([^<]+)</a>',
            html
        )
        # Also try getting snippets from <td class="result-snippet">
        snippets = re.findall(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            html, re.DOTALL
        )
        for i, (url, title) in enumerate(all_links):
            if "duckduckgo" in url or "javascript:" in url:
                continue
            snippet = re.sub(r'<[^>]+>', '', snippets[i] if i < len(snippets) else "").strip()[:300]
            results.append({
                "title": title.strip(),
                "snippet": snippet,
                "url": url.strip(),
            })
            if len(results) >= 5:
                break
        return json.dumps({"results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "results": []})

def _exec_read_file(path: str) -> str:
    """Read a file and return its content."""
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return json.dumps({"error": f"File not found: {path}"})
        content = p.read_text()[:5000]
        return json.dumps({"content": content, "path": str(p), "lines": len(content.splitlines())})
    except Exception as e:
        return json.dumps({"error": str(e)})

def _exec_write_file(path: str, content: str) -> str:
    """Write content to a file."""
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return json.dumps({"ok": True, "path": str(p)})
    except Exception as e:
        return json.dumps({"error": str(e)})

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information. Use when you need facts, prices, documentation, or model names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the filesystem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
]

TOOL_EXECUTORS = {
    "web_search": lambda args: _exec_web_search(args.get("query", "")),
    "read_file": lambda args: _exec_read_file(args.get("path", "")),
    "write_file": lambda args: _exec_write_file(args.get("path", ""), args.get("content", "")),
}

# ═══════════════════════════════════════════════════════════════════════
# Welcome screen
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
    provider, _ = _resolve_provider(cfg, model) if model != "?" else ("—", {})
    ctx = _get_context_window(cfg, model)
    adv = cfg.get("adversarial", {})
    tone_name = cfg.get("tone", {}).get("default", "casual")
    tone_profiles = cfg.get("tone", {}).get("profiles", {})
    tone_desc = tone_profiles.get(tone_name, {}).get("description", "—")
    fc_mode = cfg.get("fact_check", {}).get("mode", "normal")
    caps = cfg.get("capabilities", {})

    for line in BAW_BANNER.splitlines():
        if line.strip():
            plain_console.print(Text(line, style="baw.brand", justify="center"))
    plain_console.print(Text("Black And White · Agent Platform", style="baw.subtitle", justify="center"))
    plain_console.print(Rule(style="baw.accent"))

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

    info = Table(show_header=False, box=box.SIMPLE, border_style="baw.accent", padding=(0, 2), expand=True)
    info.add_column(style="baw.key", width=14)
    info.add_column(style="baw.val")
    info.add_row("model", f"[baw.gold]{model}[/] [baw.dim](context: {_human_tokens(ctx)})[/]")
    info.add_row("provider", provider or "[baw.muted]auto[/]")
    info.add_row("tone", f"[baw.purple]{tone_name}[/] [baw.muted]({tone_desc})[/]")
    fc_colors = {"strict": "red", "normal": "yellow", "relaxed": "green"}
    fc_color = fc_colors.get(fc_mode, "white")
    info.add_row("fact-check", f"[{fc_color}]{fc_mode}[/]")
    adv_enabled = adv.get("enabled", False)
    devil_model = adv.get("devil_model", model)
    info.add_row("😇 Angel", f"[green]{model}[/]")
    info.add_row("👿 Devil", f"[red]{devil_model}[/]" if adv_enabled else f"[baw.muted]{devil_model} (disabled)[/]")
    plain_console.print(info)
    plain_console.print()

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
    for i in range(0, len(cap_items), 2):
        row = []
        for j in range(2):
            if i + j < len(cap_items):
                row.append(cap_items[i + j][0])
                row.append(cap_items[i + j][1])
            else:
                row += ["", ""]
        cap_table.add_row(*row)
    plain_console.print(Panel(cap_table, title="⚡ Capability Routing", border_style="baw.accent", title_align="left"))

    cmds = Table(show_header=False, box=None, padding=(0, 1), expand=True, show_edge=False)
    cmds.add_column(style="baw.cmd", width=14, no_wrap=True)
    cmds.add_column(style="baw.muted")
    cmds.add_column(style="baw.cmd", width=14, no_wrap=True)
    cmds.add_column(style="baw.muted")
    cmds.add_column(style="baw.cmd", width=14, no_wrap=True)
    cmds.add_column(style="baw.muted")
    cmds.add_row("/help", "show help", "/model", "switch model", "/tone", "switch tone")
    cmds.add_row("/config", "view config", "/soul", "view SOUL", "/session", "session info")
    cmds.add_row("/clear", "reset chat", "/exit", "quit", "", "")
    plain_console.print(Panel(cmds, title="⌨  Commands", border_style="baw.accent", title_align="left"))

    rt = _recent_sessions_table(5)
    if rt:
        plain_console.print()
        plain_console.print(Panel(rt, title="📂 Recent Sessions", border_style="baw.accent", title_align="left"))

    plain_console.print(Rule(style="baw.accent"))


# ═══════════════════════════════════════════════════════════════════════
# Agent loop — streaming + tool calling
# ═══════════════════════════════════════════════════════════════════════

MAX_TOOL_TURNS = 5

def _run_agent(client, model_id, messages, cfg):
    """Run agent loop: stream response, handle tool calls, return final text + usage."""
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for turn in range(MAX_TOOL_TURNS):
        spinner = Spinner("dots2", text=f"[baw.muted]{model_id} thinking…[/]", style="baw.muted")
        text_buffer = ""
        tool_calls_buffer: list[dict] = []
        current_tool_index = -1
        usage = None

        with Live(spinner, console=plain_console, refresh_per_second=10, transient=True) as live:
            try:
                for chunk in client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    tools=TOOLS,
                    stream=True,
                    stream_options={"include_usage": True},
                ):
                    # Usage
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens or 0,
                            "completion_tokens": chunk.usage.completion_tokens or 0,
                            "total_tokens": chunk.usage.total_tokens or 0,
                        }

                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue

                    # Text content
                    if delta.content:
                        if live.is_started:
                            live.stop()
                            if turn == 0:
                                plain_console.print(Text("🖤  BAW", style="baw.gold"))
                        text_buffer += delta.content
                        plain_console.print(delta.content, end="", style="white")

                    # Tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx >= len(tool_calls_buffer):
                                tool_calls_buffer.append({"id": "", "function": {"name": "", "arguments": ""}})
                            if tc.id:
                                tool_calls_buffer[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_buffer[idx]["function"]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments

                if live.is_started:
                    live.stop()
                    if turn == 0 and not text_buffer and tool_calls_buffer:
                        plain_console.print(Text("🖤  BAW", style="baw.gold"))

            except Exception as e:
                live.stop()
                plain_console.print(f"\n[baw.error]✗ API error:[/] {e}")
                return None, None

        plain_console.print()

        # Accumulate usage
        if usage:
            for k in total_usage:
                total_usage[k] += usage.get(k, 0)

        # If text response (no tool calls) → done
        if text_buffer and not tool_calls_buffer:
            return text_buffer.strip(), total_usage

        # If tool calls → execute and continue
        if tool_calls_buffer:
            tool_icons = {"web_search": "🔍", "read_file": "📄", "write_file": "✏️"}

            # Add assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": text_buffer or None,
                "tool_calls": tool_calls_buffer,
            })

            for tc in tool_calls_buffer:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                icon = tool_icons.get(fn_name, "🔧")
                arg_preview = str(fn_args)[:80]
                plain_console.print(f"  {icon} [baw.muted]{fn_name}({arg_preview})[/]")

                result = TOOL_EXECUTORS.get(fn_name, lambda a: json.dumps({"error": "unknown tool"}))(fn_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result[:2000],  # truncate for context
                })

            continue  # Next agent turn

        # Empty response
        return None, None

    return None, total_usage


def _print_status(cfg, model_id, usage):
    tone = cfg.get("tone", {}).get("default", "casual")
    fc = cfg.get("fact_check", {}).get("mode", "normal")
    ctx = _get_context_window(cfg, model_id)

    parts = [f"[baw.purple]{model_id}[/]"]
    if usage and usage["total_tokens"]:
        up = _human_tokens(usage["prompt_tokens"])
        down = _human_tokens(usage["completion_tokens"])
        total = usage["total_tokens"]
        ctx_str = _human_tokens(ctx)
        pct = total / ctx * 100 if ctx else 0
        pct_color = "green" if pct < 50 else ("yellow" if pct < 80 else "red")
        parts.append(f"[dim]{up}↑/{down}↓[/]")
        parts.append(f"[{pct_color}]{_human_tokens(total)}/{ctx_str}[/] [dim]({pct:.0f}%)[/]")
    parts.append(f"[baw.muted]tone:[/] [baw.purple]{tone}[/]")
    fc_colors = {"strict": "red", "normal": "yellow", "relaxed": "green"}
    fc_color = fc_colors.get(fc, "white")
    parts.append(f"[baw.muted]fc:[/] [{fc_color}]{fc}[/]")
    plain_console.print("  " + " · ".join(parts))
    plain_console.print()


# ═══════════════════════════════════════════════════════════════════════
# Slash commands
# ═══════════════════════════════════════════════════════════════════════

def _slash(cmd: str, cfg, msgs, mid_ref: list) -> str | bool | None:
    parts = cmd.split()
    v = parts[0].lower()

    if v in ("/exit", "/quit", "/q"):
        return "EXIT"
    if v in ("/clear", "/reset"):
        return "CLEAR"

    if v == "/help":
        t = Table(box=box.SIMPLE, border_style="baw.accent", show_header=False, pad_edge=False, expand=True)
        t.add_column("cmd", style="baw.cmd", width=14, no_wrap=True)
        t.add_column("desc", style="baw.muted")
        t.add_row("/help", "this help")
        t.add_row("/model [name]", "switch model")
        t.add_row("/tone [name]", "tone (casual/business/teaching/ot-rt/stepwise)")
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
            mid_ref[0] = parts[1]
            plain_console.print(f"[baw.success]✓ model → {parts[1]}[/]")
        else:
            plain_console.print(f"[baw.purple]current:[/] [baw.gold]{mid_ref[0]}[/]")
            plain_console.print("[baw.muted]usage: /model <name>[/]")
        return True

    if v == "/tone":
        profiles = cfg.get("tone", {}).get("profiles", {})
        if len(parts) > 1:
            new_tone = parts[1]
            if new_tone in profiles:
                cfg["tone"]["default"] = new_tone
                plain_console.print(f"[baw.success]✓ tone → {new_tone}[/] ({profiles[new_tone].get('description','')})[/]")
            else:
                plain_console.print(f"[baw.error]Unknown tone: {new_tone}[/]")
                t = Table(show_header=False, box=None, padding=(0,1))
                t.add_column("name", style="baw.cmd", width=12)
                t.add_column("desc", style="baw.muted")
                for n, i in profiles.items():
                    t.add_row(n, i.get("description","—"))
                plain_console.print(t)
        else:
            current = cfg.get("tone", {}).get("default", "casual")
            plain_console.print(f"[baw.purple]tone:[/] [baw.gold]{current}[/]")
            t = Table(show_header=False, box=None, padding=(0,1))
            t.add_column("name", style="baw.cmd", width=12)
            t.add_column("desc", style="baw.muted")
            for n, i in profiles.items():
                t.add_row(n, i.get("description","—"))
            plain_console.print(t)
        return True

    if v in ("/soul", "/identity"):
        sp = BAW_HOME / "SOUL.md"
        content = sp.read_text() if sp.exists() else "[baw.muted]SOUL.md not found[/]"
        plain_console.print(Panel(content, title="🧠 SOUL.md", border_style="baw.accent", title_align="left"))
        return True

    if v == "/session":
        sid = msgs[0].get("session_id", "N/A") if msgs else "N/A"
        plain_console.print(f"[baw.purple]session:[/] [baw.muted]{sid}[/]  [baw.purple]msgs:[/] [baw.muted]{len(msgs)}[/]")
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
# Main
# ═══════════════════════════════════════════════════════════════════════

def cmd_chat():
    cfg = _cfg()
    mid = cfg.get("model", {}).get("default", "deepseek-v4-flash")
    mid_ref = [mid]
    pname, pinfo = _resolve_provider(cfg, mid)
    base = pinfo.get("base_url", "https://api.deepseek.com/v1")
    key = _key(cfg)

    if not key:
        plain_console.print("[baw.error]✗ No API key. Set it in ~/.baw/.env[/]")
        sys.exit(1)

    client = OpenAI(api_key=key, base_url=base)
    soul = _soul()
    tone = cfg.get("tone", {}).get("default", "casual")

    sysprompt = {
        "role": "system",
        "content": f"""You are BAW — CLI chat mode on Dragon Q6A ARM64 Docker.

{soul[:3000]}

Current tone: {tone}
Tool access: web_search, read_file, write_file (max 5 tool turns per message)
When you need facts, prices, or model names → use web_search.
When you need to check files → use read_file.
When asked to save → use write_file.
Language: Traditional Chinese (繁體中文). Concise, action-oriented.
Identity: BAW. Never say "Hermes" or "Sticky".
""",
    }
    msgs = [sysprompt]
    sid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    _welcome(cfg)

    is_tty = sys.stdin.isatty()
    if not is_tty:
        plain_console.print("[baw.warning]⚠ stdin is not a TTY — chat may not work.[/]")

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
                r = _slash(inp, cfg, msgs, mid_ref)
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
            resp, usage = _run_agent(client, mid_ref[0], msgs, cfg)
            if resp and resp.strip():
                msgs.append({"role": "assistant", "content": resp})
                _print_status(cfg, mid_ref[0], usage)
            elif usage:
                # Had usage but no text — tool calls were made, status still useful
                _print_status(cfg, mid_ref[0], usage)
            else:
                plain_console.print("[baw.warning]⚠ No response. Try again or /exit.[/]")
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
