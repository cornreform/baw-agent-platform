"""baw tui-chat — Textual TUI chat with persistent status bar.
Like Hermes CLI but with BAW identity (purple+gold, ⚡ prompt).
Top bar: model · provider · tone · fact-check · tokens · context %
Middle: scrollable conversation
Bottom: input field
"""
from __future__ import annotations
import json, os, sys, uuid
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Input, RichLog, Label
from textual.binding import Binding
from textual.reactive import reactive
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich import box

BAW_HOME = Path.home() / ".baw"
BAW_ROOT = Path(__file__).resolve().parent.parent.parent

# ═══════════════════════════════════════════════════════════════════════
# Helpers (same as chat.py)
# ═══════════════════════════════════════════════════════════════════════

def _soul() -> str:
    p = BAW_HOME / "SOUL.md"
    return p.read_text()[:3000] if p.exists() else ""

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

def _get_context_window(cfg, model_id: str) -> int:
    for pinfo in cfg.get("providers", {}).values():
        for m in pinfo.get("models", []):
            if m.get("id") == model_id:
                return m.get("context_window", 131072)
    return 131072

def _human_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)

# ═══════════════════════════════════════════════════════════════════════
# Web search (same POST approach)
# ═══════════════════════════════════════════════════════════════════════

def _exec_web_search(query: str) -> str:
    try:
        import urllib.request, urllib.parse, re
        url = "https://lite.duckduckgo.com/lite/"
        data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        results = []
        all_links = re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]+)</a>', html)
        snippets = re.findall(r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>', html, re.DOTALL)
        for i, (url, title) in enumerate(all_links):
            if "duckduckgo" in url or "javascript:" in url:
                continue
            snippet = re.sub(r'<[^>]+>', '', snippets[i] if i < len(snippets) else "").strip()[:300]
            results.append({"title": title.strip(), "snippet": snippet, "url": url.strip()})
            if len(results) >= 5:
                break
        return json.dumps({"results": results}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "results": []})

def _exec_read_file(path: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return json.dumps({"error": f"File not found: {path}"})
        return json.dumps({"content": p.read_text()[:5000], "path": str(p)})
    except Exception as e:
        return json.dumps({"error": str(e)})

TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]

TOOL_EXEC = {
    "web_search": lambda a: _exec_web_search(a.get("query", "")),
    "read_file": lambda a: _exec_read_file(a.get("path", "")),
}


# ═══════════════════════════════════════════════════════════════════════
# TUI Chat App
# ═══════════════════════════════════════════════════════════════════════

class BAWChat(App):
    """Textual TUI chat with persistent status bar."""

    CSS = """
    Screen { background: #0d0d12; }
    #status-bar {
        height: 2;
        dock: top;
        background: #131320;
        border-bottom: solid #2a1535;
        padding: 0 1;
    }
    #status-bar Label {
        width: 1fr;
        text-align: center;
        color: #aaa;
    }
    #status-bar .title { color: magenta; text-style: bold; }
    #status-bar .highlight { color: yellow; text-style: bold; }
    #status-bar .dim { color: #666; }
    #conversation {
        margin: 0;
        padding: 0 1;
        background: #0d0d12;
        overflow-y: auto;
    }
    #input-area {
        height: 3;
        dock: bottom;
        background: #131320;
        border-top: solid #2a1535;
        padding: 0 1;
    }
    Input {
        background: #1a1a2e;
        color: white;
        border: solid #2a1535;
        margin: 0;
    }
    Input:focus {
        border: solid magenta;
    }
    RichLog { background: #0d0d12; }
    """

    BINDINGS = [
        Binding("escape", "focus_input", "Chat", show=False),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    prompt_tokens = reactive(0)
    completion_tokens = reactive(0)
    context_used = reactive(0)
    context_max = reactive(65536)

    def _resolve_provider(self, cfg, model_id: str):
        """Find which provider has this model, return (pname, pinfo)."""
        for pname, pinfo in cfg.get("providers", {}).items():
            for m in pinfo.get("models", []):
                if m.get("id") == model_id:
                    return pname, pinfo
        # fallback: first provider
        pname = list(cfg.get("providers", {}))[:1]
        pname = pname[0] if pname else "deepseek"
        return pname, cfg.get("providers", {}).get(pname, {})

    def __init__(self):
        super().__init__()
        cfg = _cfg()
        self._cfg = cfg
        self._model_id = cfg.get("model", {}).get("default", "deepseek-v4-flash")
        self._pname, pinfo = self._resolve_provider(cfg, self._model_id)
        base = pinfo.get("base_url", "https://api.deepseek.com/v1")
        key = _key(cfg)
        self._client = OpenAI(api_key=key, base_url=base) if key else None
        self._tone = cfg.get("tone", {}).get("default", "casual")
        self._fc = cfg.get("fact_check", {}).get("mode", "normal")
        self._ctx_max = _get_context_window(cfg, self._model_id)
        self._messages = [{"role": "system", "content": self._build_sysprompt()}]
        self._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        # Detect if provider supports tool calling
        self._tools_supported = self._pname.lower() not in ("minimax",)

    def _build_sysprompt(self) -> str:
        soul = _soul()
        tools_note = "Tool access: web_search, read_file (max 5 tool turns per message)" if self._tools_supported else "Tool access: disabled (provider does not support function calling)"
        return f"""You are BAW — TUI chat mode on Dragon Q6A ARM64 Docker.

{soul[:2000]}

Current tone: {self._tone}
{tools_note}
Language: Traditional Chinese (繁體中文). Concise, action-oriented.
Identity: BAW. Never say "Hermes" or "Sticky"."""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="status-bar"):
            yield Label("", id="status-model")
            yield Label("", id="status-tokens")
            yield Label("", id="status-tone")
        yield RichLog(id="conversation", highlight=True, markup=True, wrap=True)
        with Container(id="input-area"):
            yield Input(placeholder="⚡ Type your message…", id="msg-input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🖤 BAW Chat"
        self.sub_title = "Black And White"
        self._refresh_status()
        self.query_one("#msg-input", Input).focus()
        # Welcome
        log = self.query_one("#conversation", RichLog)
        log.write(Panel(
            f"🖤  BAW ready — [magenta]{self._model_id}[/] [dim]({self._pname})[/] · "
            f"tone: [yellow]{self._tone}[/] · fc: [{self._fc_color()}] {self._fc}[/]\n"
            f"Tools: {'[green]enabled[/]' if self._tools_supported else '[dim]disabled[/]'}\n"
            f"Type /help for commands, /exit to quit.",
            title="Welcome", border_style="magenta"
        ))

    def _fc_color(self) -> str:
        return {"strict": "red", "normal": "yellow", "relaxed": "green"}.get(self._fc, "white")

    def _refresh_status(self) -> None:
        self.query_one("#status-model", Label).update(
            f"[bold magenta]🖤  BAW[/]  [yellow]{self._model_id}[/]  [dim]{self._pname}[/]"
        )

        tok = self._total_usage
        pct = tok["total_tokens"] / self._ctx_max * 100 if self._ctx_max else 0
        pct_color = "green" if pct < 50 else ("yellow" if pct < 80 else "red")
        self.query_one("#status-tokens", Label).update(
            f"[dim]{_human_tokens(tok['prompt_tokens'])}↑/{_human_tokens(tok['completion_tokens'])}↓[/] "
            f"[{pct_color}]{_human_tokens(tok['total_tokens'])}/{_human_tokens(self._ctx_max)}[/] "
            f"[dim]({pct:.0f}%)[/]"
        )

        fc_color = self._fc_color()
        tone_profiles = self._cfg.get("tone", {}).get("profiles", {})
        tone_desc = tone_profiles.get(self._tone, {}).get("description", "—")
        self.query_one("#status-tone", Label).update(
            f"[dim]tone:[/] [yellow]{self._tone}[/] [dim]({tone_desc})[/]  "
            f"[dim]fc:[/] [{fc_color}]{self._fc}[/]"
        )

    def action_focus_input(self) -> None:
        self.query_one("#msg-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        msg = event.value.strip()
        if not msg:
            return
        event.input.value = ""

        log = self.query_one("#conversation", RichLog)

        # Slash commands
        if msg.startswith("/"):
            await self._handle_slash(msg, log)
            return

        # User message
        log.write(f"[bold magenta]⚡[/] [white]{msg}[/]")

        # Agent loop
        self._messages.append({"role": "user", "content": msg})
        reply, usage = await self._run_agent(log)

        if reply:
            self._messages.append({"role": "assistant", "content": reply})
            if usage:
                for k in self._total_usage:
                    self._total_usage[k] += usage.get(k, 0)
            self._refresh_status()

        self.query_one("#msg-input", Input).focus()

    def _clean_messages(self) -> list[dict]:
        """Return messages array safe for the current provider.
        Strips tool-related messages if provider doesn't support tools."""
        msgs = self._messages
        if not self._tools_supported:
            # Strip assistant messages with tool_calls and all tool role messages
            msgs = [m for m in msgs if m.get("role") != "tool"
                    and not (m.get("role") == "assistant" and "tool_calls" in m)]
        return msgs

    async def _run_agent(self, log: RichLog) -> tuple[str | None, dict | None]:
        """Agent loop with streaming + tool calling."""
        MAX_TURNS = 5
        usage_total = {}

        use_tools = self._tools_supported and TOOLS

        for turn in range(MAX_TURNS):
            text = ""
            tool_calls = []
            usage = None

            clean_msgs = self._clean_messages()
            kwargs = dict(
                model=self._model_id,
                messages=clean_msgs,
                stream=True,
                stream_options={"include_usage": True},
            )
            if use_tools:
                kwargs["tools"] = TOOLS

            try:
                for chunk in self._client.chat.completions.create(**kwargs):
                    if hasattr(chunk, "usage") and chunk.usage:
                        usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens or 0,
                            "completion_tokens": chunk.usage.completion_tokens or 0,
                            "total_tokens": chunk.usage.total_tokens or 0,
                        }
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue
                    if delta.content:
                        text += delta.content
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            while len(tool_calls) <= tc.index:
                                tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if tc.id:
                                tool_calls[tc.index]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls[tc.index]["function"]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls[tc.index]["function"]["arguments"] += tc.function.arguments
            except Exception as e:
                log.write(f"[red]✗ API error: {e}[/]")
                return None, usage_total

            if text:
                log.write(Text(text, style="white"))

            if usage:
                for k in usage_total:
                    usage_total[k] = usage_total.get(k, 0) + usage.get(k, 0)

            if text and not tool_calls:
                return text.strip(), usage_total

            if tool_calls:
                self._messages.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls})
                for tc in tool_calls:
                    fn = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    log.write(f"  [dim]🔍 {fn}({str(args)[:60]})[/]")
                    result = TOOL_EXEC.get(fn, lambda a: json.dumps({"error": "unknown"}))(args)
                    self._messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result[:2000]})
                continue

            return None, usage_total

        return None, usage_total

    async def _handle_slash(self, cmd: str, log: RichLog) -> None:
        parts = cmd.split()
        v = parts[0].lower()

        if v in ("/exit", "/quit", "/q"):
            self.exit()
            return
        if v in ("/clear", "/reset"):
            self._messages = [{"role": "system", "content": self._build_sysprompt()}]
            self._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            log.clear()
            log.write("[dim]🧹 Cleared[/]")
            self._refresh_status()
            return

        if v == "/help":
            t = Table(box=box.SIMPLE, border_style="magenta", show_header=False)
            t.add_column("cmd", style="bold magenta", width=14)
            t.add_column("desc", style="dim")
            for c, d in [
                ("/help", "Show help"), ("/model NAME", "Switch model"),
                ("/tone NAME", "Switch tone"), ("/clear", "Reset chat"),
                ("/exit", "Quit"),
            ]:
                t.add_row(c, d)
            log.write(t)
            return

        if v == "/model" and len(parts) > 1:
            self._model_id = parts[1]
            self._cfg["model"]["default"] = parts[1]
            self._pname, pinfo = self._resolve_provider(self._cfg, self._model_id)
            self._ctx_max = _get_context_window(self._cfg, self._model_id)
            self._tools_supported = self._pname.lower() not in ("minimax",)
            # Reconnect client if provider changed
            key = _key(self._cfg)
            base = pinfo.get("base_url", "https://api.deepseek.com/v1")
            self._client = OpenAI(api_key=key, base_url=base) if key else None
            log.write(f"[green]✓ model → {parts[1]} ({self._pname})[/]")
            self._refresh_status()
            return

        if v == "/tone" and len(parts) > 1:
            profiles = self._cfg.get("tone", {}).get("profiles", {})
            if parts[1] in profiles:
                self._tone = parts[1]
                self._cfg["tone"]["default"] = parts[1]
                log.write(f"[green]✓ tone → {parts[1]}[/]")
                self._refresh_status()
            else:
                log.write(f"[red]Unknown tone: {parts[1]}[/]")
            return

        if v == "/tone":
            t = Table(box=None, show_header=False, padding=(0, 1))
            t.add_column("name", style="bold magenta", width=12)
            t.add_column("desc", style="dim")
            for n, i in self._cfg.get("tone", {}).get("profiles", {}).items():
                t.add_row(n, i.get("description", "—"))
            log.write(t)
            return

        log.write(f"[dim]Unknown: {cmd}. Type /help[/]")


def cmd_tui_chat():
    BAWChat().run()
