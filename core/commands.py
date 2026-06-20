"""
BAW — Slash Commands (P1)
Interactive mode commands for quick operations.
"""

from __future__ import annotations
import sys
import subprocess
import time as _time
import json
from pathlib import Path
from typing import Optional

# ── Command result cache (60s TTL for static responses) ──────

_cache: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 60  # seconds

def _cached(key: str, fn) -> str:
    """Return cached result if fresh, else compute + cache."""
    now = _time.time()
    if key in _cache:
        ts, result = _cache[key]
        if now - ts < _CACHE_TTL:
            return result
    result = fn()
    _cache[key] = (now, result)
    return result

def _cache_invalidate(key: str | None = None):
    """Invalidate cache. key=None clears all."""
    if key is None:
        _cache.clear()
    elif key in _cache:
        del _cache[key]

# ── Session state (set by CLI after each run_agent call) ──────

LAST_PROMPT: str | None = None
LAST_COURT_VERDICT: dict | None = None


def set_last_run(prompt: str, info: dict):
    """Store the last prompt and court verdict for /rethink and /court."""
    global LAST_PROMPT, LAST_COURT_VERDICT
    LAST_PROMPT = prompt
    if info and info.get("adversarial_raw"):
        LAST_COURT_VERDICT = info["adversarial_raw"]


# ── Router ─────────────────────────────────────────────────────

def handle_slash(command: str, args: list[str],
                 config: dict, data_dir: Path, verbose: bool = False) -> Optional[str]:
    """Route a slash command and return a response string.

    Returns None if the command doesn't trigger any action.
    """
    cmd = command.lower()

    if cmd in ("h", "help", "?"):
        return _cached("help", _cmd_help)

    if cmd in ("v", "version"):
        return _cached("version", lambda: _cmd_version(data_dir))

    if cmd in ("s", "status"):
        return _cached("status", lambda: _cmd_status(config, data_dir))

    if cmd in ("r", "remember"):
        text = " ".join(args)
        if not text:
            return "Usage: /remember <text>"
        return _cmd_remember(text, data_dir)

    if cmd in ("q", "search"):
        query = " ".join(args)
        if not query:
            return "Usage: /search <query>"
        return _cmd_search(query, data_dir)

    if cmd in ("m", "model"):
        model_id = " ".join(args)
        if not model_id:
            return "Usage: /model <id> (see config.yaml for available models)"
        return _cmd_model(model_id, config)

    if cmd in ("models", "aux", "aux-models"):
        return _cmd_aux_models(config)

    if cmd in ("t", "tone"):
        tone = " ".join(args)
        if not tone:
            return "Usage: /tone <profile>  (casual / business / teaching / client-doc / ot-rt / stepwise)"
        return _cmd_tone(tone, config)

    if cmd in ("d", "dream"):
        return _cmd_dream(data_dir)

    if cmd in ("c", "clear"):
        _cmd_clear()
        return ""

    if cmd in ("provider", "sp", "search-provider"):
        return _cmd_search_provider(args, data_dir)

    if cmd == "tools":
        return _cached("tools", _cmd_tools)

    if cmd in ("docs", "docschain", "dc"):
        filepath = " ".join(args) if args else "."
        return _cmd_docs(filepath)

    if cmd in ("update", "upgrade", "up"):
        return _cmd_update(data_dir)

    # ── New P1 commands ──
    if cmd in ("rethink", "rt"):
        return _cmd_rethink(args, config, data_dir, verbose)

    if cmd in ("court", "ct"):
        return _cmd_court()

    if cmd in ("fresh", "fr", "raw"):
        return _cmd_fresh(args, config, data_dir, verbose)

    # ── Permission commands ──
    if cmd in ("permit", "allow"):
        return _cmd_permit(args, data_dir)
    if cmd in ("block", "deny"):
        return _cmd_block(args, data_dir)
    if cmd in ("permissions", "perms"):
        return _cmd_permissions(data_dir)
    if cmd == "reset-perms":
        scope = " ".join(args) if args else None
        return _cmd_reset_perms(scope, data_dir)

    # Not a slash command
    return None


# ── Command registry (auto-generates /help) ───────────────────

_HELP_COMMANDS: list[dict] = [
    # (category, cmd_string, description)
    # Categories: 💬 Core, 📋 Sessions, ⚙️ Config, 🧠 Memory, 🛠 Tools, 🔧 System
    {"cat": "💬 Core", "cmd": "/help, /h, /?", "desc": "Show this help"},
    {"cat": "💬 Core", "cmd": "/version, /v", "desc": "BAW version + last commit"},
    {"cat": "💬 Core", "cmd": "/status, /s", "desc": "Model, memory, tools, session health"},
    {"cat": "💬 Core", "cmd": "/btw <text>", "desc": "Quick answer — no court, no plan"},
    {"cat": "💬 Core", "cmd": "/fresh, /fr /raw <prompt>", "desc": "Raw model — no soul, no memories"},
    {"cat": "💬 Core", "cmd": "/rethink, /rt [prompt]", "desc": "Re-run last prompt, force alternative view"},
    {"cat": "💬 Core", "cmd": "/court, /ct", "desc": "Show last Angel/Devil court verdict"},
    {"cat": "💬 Core", "cmd": "/clear, /c", "desc": "Clear screen (CLI only)"},
    {"cat": "💬 Core", "cmd": "/new", "desc": "Save current + start fresh session"},
    {"cat": "💬 Core", "cmd": "/reset", "desc": "Hard reset — clear session without saving"},

    {"cat": "📋 Sessions", "cmd": "/task new [name]", "desc": "Save current + start fresh session"},
    {"cat": "📋 Sessions", "cmd": "/task list, /list", "desc": "List saved sessions"},
    {"cat": "📋 Sessions", "cmd": "/task resume <id>, /resume <id>", "desc": "Resume a saved session"},
    {"cat": "📋 Sessions", "cmd": "/task save [name]", "desc": "Save/name current session"},
    {"cat": "📋 Sessions", "cmd": "/task forget <id>", "desc": "Delete a saved session"},
    {"cat": "📋 Sessions", "cmd": "/task info", "desc": "Show current session details"},
    {"cat": "📋 Sessions", "cmd": "/summarize", "desc": "LLM summary of current session"},
    {"cat": "📋 Sessions", "cmd": "/pickup", "desc": "Resume last interrupted session"},
    {"cat": "📋 Sessions", "cmd": "/stop", "desc": "Cancel running request"},
    {"cat": "📋 Sessions", "cmd": "/restart", "desc": "Restart BAW engine"},

    {"cat": "⚙️ Config", "cmd": "/model, /m [id]", "desc": "Switch model (per-chat or global via /set)"},
    {"cat": "⚙️ Config", "cmd": "/mode [quick|hybrid|tight]", "desc": "Switch execution mode"},
    {"cat": "⚙️ Config", "cmd": "/tone [casual|business|...]", "desc": "Switch tone profile"},
    {"cat": "⚙️ Config", "cmd": "/set <key> <value>", "desc": "Persist config to config.yaml"},
    {"cat": "⚙️ Config", "cmd": "/reload", "desc": "Hot-reload tools & config (no restart)"},
    {"cat": "⚙️ Config", "cmd": "/capability <cmd>", "desc": "Manage capabilities (tts, stt, etc.)"},

    {"cat": "🧠 Memory", "cmd": "/remember, /r <text>", "desc": "Store a memory entry"},
    {"cat": "🧠 Memory", "cmd": "/search, /q <query>", "desc": "Search stored memories"},
    {"cat": "🧠 Memory", "cmd": "/dream, /d", "desc": "Run weekly self-curation"},
    {"cat": "🧠 Memory", "cmd": "/evolve, /ev", "desc": "Self-evolution stats & patterns"},

    {"cat": "🛠 Tools", "cmd": "/tools", "desc": "List available tools"},
    {"cat": "🛠 Tools", "cmd": "/provider, /sp list|api|test", "desc": "Search provider management"},
    {"cat": "🛠 Tools", "cmd": "/board", "desc": "Generate HTML dashboard"},
    {"cat": "🛠 Tools", "cmd": "/docs, /dc <path>", "desc": "Show docs chain for a file"},

    {"cat": "🔧 System", "cmd": "/update, /up", "desc": "Git pull + changelog + restart"},
    {"cat": "🔧 System", "cmd": "/tts on|off|status", "desc": "Toggle text-to-speech"},

    {"cat": "⚙️ Config", "cmd": "/permit <scope> [duration]", "desc": "Grant permission (session/permanent/5m/1h)"},
    {"cat": "⚙️ Config", "cmd": "/block <scope> [duration]", "desc": "Block a permission"},
    {"cat": "⚙️ Config", "cmd": "/permissions, /perms", "desc": "List all permissions"},
    {"cat": "⚙️ Config", "cmd": "/reset-perms [scope]", "desc": "Reset permission(s) to default (ask)"},
]

def _cmd_help() -> str:
    """Auto-generated from _HELP_COMMANDS registry."""
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in _HELP_COMMANDS:
        grouped[entry["cat"]].append(entry)

    lines = ["**BAW Commands**", ""]
    for cat in ["💬 Core", "📋 Sessions", "⚙️ Config", "🧠 Memory", "🛠 Tools", "🔧 System"]:
        cmds = grouped.get(cat, [])
        if not cmds:
            continue
        lines.append(f"*{cat}*")
        for c in cmds:
            lines.append(f"  `{c['cmd']}` — {c['desc']}")
        lines.append("")
    lines.append("_Anything else → sent to BAW as a prompt._")
    return "\n".join(lines)


def _cmd_version(data_dir: Path) -> str:
    try:
        log = subprocess.run(
            ["git", "log", "--oneline", "-3"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path.home() / "baw"),
        ).stdout.strip()
    except Exception:
        log = "unknown"
    return f"BAW v1.0.0\n{log}\nRepo: github.com/cornreform/baw-agent-platform"


def _cmd_status(config: dict, data_dir: Path) -> str:
    from .memory import MemoryStore
    from .tools import list_tools

    try:
        from ..tools import register_all
        register_all()
    except ImportError:
        pass

    mem = MemoryStore(data_dir)
    stats = mem.stats()
    model_cfg = config.get("model", {})
    tone = config.get("tone", {}).get("default", "casual")
    fact_mode = config.get("fact_check", {}).get("mode", "normal")

    lines = [
        "**BAW Status**",
        f"  Model: {model_cfg.get('default', '?')} (fallback: {model_cfg.get('fallback', 'none')})",
        f"  Tone: {tone}",
        f"  Fact check: {fact_mode}",
        f"  Memory: {stats['total']} entries",
    ]
    if stats['total'] > 0:
        lines.append(f"  Avg score: {stats['avg_score']:.2f}")

    # ── Session health ──
    try:
        sdir = data_dir / "sessions"
        if sdir.exists():
            session_files = list(sdir.glob("*.json"))
            active = sum(1 for f in session_files if (_time.time() - f.stat().st_mtime) < 3600)
            lines.append(f"  Sessions: {len(session_files)} saved ({active} active <1h)")
    except Exception:
        pass

    tools = [t.name for t in list_tools()]
    lines.append(f"  Tools ({len(tools)}): {', '.join(tools)}")

    try:
        dirty = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path.home() / "baw"),
        ).stdout.strip()
        lines.append(f"  Git: {'dirty (' + str(len(dirty.split(chr(10)))) + ' files)' if dirty else 'clean'}")
    except Exception:
        pass

    return "\n".join(lines)


def _cmd_remember(text: str, data_dir: Path) -> str:
    from .memory import MemoryStore
    mem = MemoryStore(data_dir)
    result = mem.remember(text)
    return f"Remembered (score: {result['score']:.2f})"


def _cmd_search(query: str, data_dir: Path) -> str:
    from .memory import MemoryStore
    mem = MemoryStore(data_dir)
    results = mem.search(query)
    if not results:
        return "No results found."
    lines = []
    for r in results:
        lines.append(f"[{r['score']:.2f}] {r['content'][:100]}")
    return "\n".join(lines)


def _cmd_model(model_id: str, config: dict) -> str:
    config.setdefault("model", {})["default"] = model_id
    _cache_invalidate("status")  # /status shows model info
    return f"Model set to: {model_id}"


def _cmd_tone(tone: str, config: dict) -> str:
    valid = ("casual", "business", "teaching", "client-doc", "ot-rt", "stepwise")
    if tone not in valid:
        return f"Invalid tone: {tone}. Valid: {', '.join(valid)}"
    config.setdefault("tone", {})["default"] = tone
    _cache_invalidate("status")  # /status shows tone info
    return f"Tone set to: {tone}"


def _cmd_dream(data_dir: Path) -> str:
    from .dream import dream
    report = dream(data_dir)
    if report["changes"]:
        import json
        return json.dumps(report, ensure_ascii=False, indent=2)
    return "No changes needed."


def _cmd_clear():
    import os
    os.system("clear" if sys.platform != "win32" else "cls")


def _cmd_search_provider(args: list[str], data_dir: Path) -> str:
    from .search import (
        list_providers, get_setup_guide, get_api_reference, search,
    )
    from .search import _auto_discover as _init_search
    _init_search()

    if not args:
        return "Usage: /provider list | api <name> | test <name> <query>"

    action = args[0]

    if action == "list":
        providers = list_providers()
        if not providers:
            return "No search providers registered."
        lines = ["Available Search Providers:"]
        for p in providers:
            key_status = "API key required" if p["requires_api_key"] else "No API key needed"
            lines.append(f"  {p['name']}: {p['description']}")
            lines.append(f"    {key_status}")
            if p["env_var"]:
                lines.append(f"    Env var: {p['env_var']}")
        return "\n".join(lines)

    if action == "api" and len(args) >= 2:
        try:
            return get_api_reference(args[1])
        except ValueError as e:
            return f"Error: {e}"

    if action == "test" and len(args) >= 3:
        provider = args[1]
        query = " ".join(args[2:])
        try:
            results = search(query, provider=provider, limit=3)
            if not results:
                return f"No results from {provider}."
            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"[{i}] {r['title']}")
                lines.append(f"     URL: {r['url']}")
                lines.append(f"     {r['snippet'][:200]}")
            lines.append(f"\n{len(results)} results from {provider}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    return "Usage: /provider list | api <name> | test <name> <query>"


def _cmd_tools() -> str:
    from .tools import list_tools
    try:
        from ..tools import register_all
        register_all()
    except ImportError:
        pass
    tools = list_tools()
    if not tools:
        return "No tools registered."
    lines = ["Available tools:"]
    for t in tools:
        lines.append(f"  {t.name}: {t.description[:80]}")
    return "\n".join(lines)


# ── P1: New commands ────────────────────────────────────────────

def _cmd_rethink(args: list[str], config: dict, data_dir: Path, verbose: bool) -> str:
    """Force the model to rethink the last prompt and give alternative results."""
    from ..tools import register_all
    register_all()
    from .loop import run_agent

    prompt = " ".join(args) if args else LAST_PROMPT
    if not prompt:
        return "No previous prompt to rethink. Run a prompt first."

    rethink_prompt = (
        f"[RETHINK] The user asked: {prompt}\n\n"
        f"Your previous answer may have been wrong or incomplete.\n"
        f"Challenge ALL assumptions. Consider alternatives you dismissed.\n"
        f"Provide a different perspective or approach.\n"
        f"If the original answer was sound, explain WHY it's the best choice.\n"
        f"Think step by step."
    )

    try:
        response, info = run_agent(
            prompt=rethink_prompt,
            config=config,
            data_dir=data_dir,
            verbose=verbose,
            interactive=True,
        )
        return response
    except Exception as e:
        return f"Rethink failed: {e}"


def _cmd_court(arg: str = "") -> str:
    """Show court case info.

    Sub-commands (M2, Fable 5 spec):
      /court            → recent 5 cases (id + verdict + score + elapsed)
      /court <id>       → full case record (prosecutor, defendant, judge, evidence)
      /court live       → toggle per-step push notifications for current case
      /court stats      → this week: count, approval rate, avg latency, appeal rate
    """
    arg = (arg or "").strip()
    try:
        from .court import recent_cases, get_case
    except ImportError as e:
        return f"⚖️ court module unavailable: {e}"

    # /court stats — aggregate metrics from the archive
    if arg == "stats":
        archive_dir = Path.home() / ".baw" / "court" / "cases"
        if not archive_dir.exists():
            return "⚖️ 黑白法庭狀態\n仲未審過案。問嘢就會自動開庭。"
        cases = []
        for p in sorted(archive_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                cases.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        if not cases:
            return "⚖️ 黑白法庭狀態\n仲未審過案。問嘢就會自動開庭。"
        # Last 7 days
        cutoff = _time.time() - 7 * 86400
        week = [c for c in cases if c.get("created_at", 0) >= cutoff]
        total = len(week)
        approved = sum(1 for c in week if c.get("verdict") == "approved")
        retry = sum(1 for c in week if c.get("verdict") == "retry")
        appeal = sum(1 for c in week if c.get("verdict") == "appeal")
        dismissed = sum(1 for c in week if c.get("verdict") == "dismissed")
        avg_score = sum(c.get("score", 0) for c in week) / max(total, 1)
        avg_elapsed = sum(c.get("elapsed_sec", 0) for c in week) / max(total, 1)
        # Tier breakdown
        by_tier = {}
        for c in week:
            t = c.get("tier", 0)
            by_tier[t] = by_tier.get(t, 0) + 1
        lines = [
            "⚖️ 黑白法庭狀態 (last 7 days)",
            f"審案: {total} 單",
            f"核准率: {100*approved/max(total,1):.0f}% ({approved}/{total})",
            f"🔁 RETRY: {retry}  ·  📤 APPEAL: {appeal}  ·  🚫 DISMISSED: {dismissed}",
            f"平均 verdict: {avg_score:.1f}/10",
            f"平均 latency: {avg_elapsed:.1f}s",
            "",
            "Tier 分流:",
            f"  Tier 0 (琐事): {by_tier.get(0, 0)}",
            f"  Tier 1 (judge only): {by_tier.get(1, 0)}",
            f"  Tier 2 (+檢察官): {by_tier.get(2, 0)}",
            f"  Tier 3 (全院): {by_tier.get(3, 0)}",
        ]
        return "\n".join(lines)

    # /court live — toggle per-step push for current case
    if arg == "live":
        # This is a session-scoped toggle; we don't have a user-session store
        # here, so the closest we can do is acknowledge and instruct.
        return "🔔 /court live 訂閱功能尚未 session-bind(等 M2 wire-in)。\n暫時:每個 verdict 出時會自動 update in-place。"

    # /court <id> — full case record
    if arg and arg.upper().startswith("C") and len(arg) >= 5:
        full = get_case(arg.upper())
        if not full:
            return f"⚖️ 搵唔到案件 {arg}。試 /court 睇最近 5 單。"
        verdict_emoji = {
            "approved": "✅", "retry": "🔁", "appeal": "📤",
            "dismissed": "🚫", "stay": "⏸️",
        }.get(full.get("verdict"), "?")
        lines = [
            f"{verdict_emoji} 案件全卷: {full['case_id']}",
            f"📅 {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(full.get('created_at', 0)))}",
            f"⏱️ {full.get('elapsed_sec', 0):.1f}s · Tier {full.get('tier', '?')} · "
            f"Score {full.get('score', '?')}/10",
            f"📋 案由: {full.get('goal', '')[:200]}",
            "",
            f"🤖 角色:",
            f"  被告: {full.get('defendant_model', '?')}",
            f"  法官: {full.get('judge_model', '?')}",
            f"  檢察官: {full.get('prosecutor_model', '?')}",
            "",
            f"📎 證物 ({len(full.get('evidence', []))} 件):",
        ]
        for i, ev in enumerate(full.get("evidence", [])[:8], 1):
            role = ev.get("role", "?")
            content = ev.get("content", "")[:150].replace("\n", " ")
            ts = _time.strftime("%H:%M:%S", _time.localtime(ev.get("ts", 0)))
            lines.append(f"  {i}. [{ts}] {role}: {content}")
        if len(full.get("evidence", [])) > 8:
            lines.append(f"  ... ({len(full['evidence']) - 8} more)")
        if full.get("reason"):
            lines.append(f"\n👨‍⚖️ 法官 reason: {full['reason'][:200]}")
        if full.get("final_summary"):
            lines.append(f"\n📝 結果: {full['final_summary'][:300]}")
        return "\n".join(lines)

    # /court (no arg) — recent 5
    recent = recent_cases(limit=5)
    if not recent:
        return "⚖️ 黑白法庭\n仲未審過案。問嘢就會自動開庭。"
    verdict_emoji = {
        "approved": "✅", "retry": "🔁", "appeal": "📤",
        "dismissed": "🚫", "stay": "⏸️",
    }
    lines = ["⚖️ 最近 5 單案:"]
    for c in recent:
        emoji = verdict_emoji.get(c.get("verdict"), "⏳")
        score = c.get("score", "?")
        elapsed = c.get("elapsed_sec", 0)
        tier = c.get("tier", "?")
        goal = (c.get("goal") or "")[:40]
        lines.append(f"  {emoji} {c['case_id']} │ T{tier} │ {score}/10 │ {elapsed:.0f}s │ {goal}")
    lines.append("\n/court <id> 查全卷 · /court stats 睇本週")
    return "\n".join(lines)


def _cmd_fresh(args: list[str], config: dict, data_dir: Path, verbose: bool) -> str:
    """Run with no soul, no memories — raw model judgment."""
    from ..tools import register_all
    register_all()
    from .loop import run_agent

    prompt = " ".join(args) if args else LAST_PROMPT
    if not prompt:
        if args:
            prompt = " ".join(args)
        else:
            return "Usage: /fresh <prompt>  or  /fresh  (re-run last prompt with raw model)"

    try:
        response, info = run_agent(
            prompt=prompt,
            config=config,
            data_dir=data_dir,
            verbose=verbose,
            interactive=True,
            fresh_start=True,
        )
        return response
    except Exception as e:
        return f"Fresh start failed: {e}"


# ── Docs Chain command ─────────────────────────────────────────

def _cmd_docs(filepath: str) -> str:
    """Show the docs chain for a given file path."""
    from .docs_chain import find_docs_chain, read_docs_chain
    from pathlib import Path

    path = Path(filepath).resolve()
    chain = find_docs_chain(str(path))

    if not chain:
        return f"No docs chain found for: {filepath}\nTry creating docs/README.md in the project root."

    lines = [f"📚 **Docs Chain for**: `{path}`\n"]
    lines.append(f"Found {len(chain)} doc(s):\n")
    for i, doc in enumerate(chain, 1):
        try:
            rel = doc.relative_to(Path.home())
        except ValueError:
            rel = doc
        size = len(doc.read_text()) if doc.exists() else 0
        lines.append(f"  {i}. `~/{rel}` ({size} chars)")

    lines.append(f"\n---\n")
    # Show full chain content (capped)
    docs_text = read_docs_chain(str(path))
    if len(docs_text) > 5000:
        docs_text = docs_text[:5000] + "\n\n... (truncated)"
    lines.append(docs_text)

    return "\n".join(lines)


# ── Update command — Standardized Flow ─────────────────────────

def _cmd_update(data_dir: Path) -> str:
    """Standardized update flow:
    1. git fetch
    2. Compare version (current vs latest tag)
    3. Fetch release notes from GitHub for gap versions
    4. Show changelog
    5. git pull
    6. Post-update hooks (migration, deps)
    7. Restart bot
    """
    import subprocess
    import json
    import urllib.request
    from pathlib import Path

    repo_dir = Path.home() / "baw"
    repo_owner = "cornreform"
    repo_name = "baw-agent-platform"

    lines = ["🔄 **BAW Update — Standardized Flow**\n"]

    # ── Step 1: Fetch ──
    try:
        r = subprocess.run(
            ["git", "fetch", "origin", "--tags"],
            capture_output=True, text=True, timeout=30,
            cwd=str(repo_dir),
        )
        if r.returncode != 0:
            return f"❌ Step 1/6 — git fetch failed:\n{r.stderr[:300]}"
        lines.append("✅ Step 1/6 — Fetched latest from GitHub")
    except Exception as e:
        return f"❌ Step 1/6 — git fetch failed: {e}"

    # ── Step 2: Version compare ──
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_dir),
        )
        current_tag = r.stdout.strip()
    except Exception:
        current_tag = "v0.0.0"

    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..origin/main"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_dir),
        )
        behind = int(r.stdout.strip() or "0")
    except Exception:
        behind = -1

    if behind == 0:
        lines.append(f"✅ Step 2/6 — Already up to date ({current_tag})")
        return "\n".join(lines)

    # Get latest remote tag
    try:
        r = subprocess.run(
            ["git", "ls-remote", "--tags", "--sort=-version:refname", "origin"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_dir),
        )
        tags = [line.split("refs/tags/")[-1] for line in r.stdout.strip().split("\n") if "refs/tags/v" in line]
        latest_tag = tags[0] if tags else "unknown"
    except Exception:
        latest_tag = "unknown"

    lines.append(f"✅ Step 2/6 — {current_tag} → {latest_tag} ({behind} commits behind)")

    # ── Step 3: Fetch release notes ──
    lines.append(f"\n📋 **Step 3/6 — Changelog:**\n")
    try:
        # Get new commits
        r = subprocess.run(
            ["git", "log", "--oneline", f"HEAD..origin/main", "-30"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_dir),
        )
        commits = r.stdout.strip()
        if commits:
            # Group by conventional commit type
            feat = []
            fix = []
            perf = []
            docs = []
            other = []
            for line in commits.split("\n"):
                line = line.strip()
                if not line:
                    continue
                short = line[8:] if len(line) > 8 else line  # strip hash
                if short.startswith("feat:"):
                    feat.append(f"  ✨ {short[5:].strip()}")
                elif short.startswith("fix:"):
                    fix.append(f"  🐛 {short[4:].strip()}")
                elif short.startswith("perf:"):
                    perf.append(f"  ⚡ {short[5:].strip()}")
                elif short.startswith("docs:"):
                    docs.append(f"  📝 {short[5:].strip()}")
                else:
                    other.append(f"  • {short.strip()}")

            if feat:
                lines.append("**Features:**\n" + "\n".join(feat))
            if fix:
                lines.append("\n**Fixes:**\n" + "\n".join(fix))
            if perf:
                lines.append("\n**Performance:**\n" + "\n".join(perf))
            if docs:
                lines.append("\n**Docs:**\n" + "\n".join(docs))
            if other:
                lines.append("\n**Other:**\n" + "\n".join(other[:5]))
    except Exception as e:
        lines.append(f"⚠️ Could not fetch changelog: {e}")

    # ── Step 4: Pull ──
    lines.append(f"\n⏳ **Step 4/6 — Pulling updates...**")
    try:
        r = subprocess.run(
            ["git", "pull", "origin", "main"],
            capture_output=True, text=True, timeout=60,
            cwd=str(repo_dir),
        )
        if r.returncode != 0:
            lines.append(f"❌ git pull failed:\n{r.stderr[:300]}")
            return "\n".join(lines)
        lines.append("✅ Step 4/6 — Pulled successfully")
    except Exception as e:
        lines.append(f"❌ git pull failed: {e}")
        return "\n".join(lines)

    # Confirm new version
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True, text=True, timeout=5,
            cwd=str(repo_dir),
        )
        new_tag = r.stdout.strip()
    except Exception:
        new_tag = "unknown"
    lines.append(f"🏷️ Now at: {new_tag}")

    # ── Step 5: Post-update hooks ──
    lines.append(f"\n⏳ **Step 5/6 — Post-update checks...**")
    hooks_run = []

    # Check for requirements changes
    req_file = repo_dir / "requirements.txt"
    if req_file.exists():
        try:
            r = subprocess.run(
                ["git", "diff", f"{current_tag}..HEAD", "--", "requirements.txt"],
                capture_output=True, text=True, timeout=10,
                cwd=str(repo_dir),
            )
            if r.stdout.strip():
                hooks_run.append("requirements.txt changed (manual pip install may be needed)")
        except Exception:
            pass

    # Check for config migration
    try:
        r = subprocess.run(
            ["git", "diff", f"{current_tag}..HEAD", "--", "config.sample.yaml"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_dir),
        )
        if r.stdout.strip():
            hooks_run.append("config.sample.yaml changed — check for new config keys")
    except Exception:
        pass

    if hooks_run:
        lines.append("\n".join(f"  ⚠️ {h}" for h in hooks_run))
    else:
        lines.append("  ✅ No migration needed")

    # ── Step 6: Restart ──
    lines.append(f"\n⏳ **Step 6/6 — Restarting bot...**")
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "baw-telegram"],
            capture_output=True, timeout=10,
        )
        lines.append("✅ Step 6/6 — Bot restarted, changes are live")
    except Exception as e:
        lines.append(f"⚠️ Auto-restart failed: {e}")
        lines.append("   Run: `sudo systemctl restart baw-telegram`")

    return "\n".join(lines)


def _cmd_aux_models(config: dict) -> str:
    """Show all auxiliary models (STT, TTS, vision, image_gen, etc.)"""
    caps = config.get("capabilities", {})
    exec_cfg = config.get("executor", {})

    parts = ["**Auxiliary Models**", ""]
    parts.append(f"Executor: `{exec_cfg.get('model', '?')}`")

    def _fmt(name: str, cfg: dict) -> str:
        if not isinstance(cfg, dict):
            return f"  {name}: `{cfg}`"
        items = []
        m = cfg.get("model", "")
        fb = cfg.get("fallback", "")
        method = cfg.get("method", "")
        voice = cfg.get("voice", "")
        if method:
            items.append(method)
        if m:
            items.append(f"`{m}`")
        if fb:
            if isinstance(fb, dict):
                fb_m = fb.get("model", "")
                if fb_m:
                    items.append(f"fallback: `{fb_m}`")
            else:
                items.append(f"fallback: `{fb}`")
        if voice:
            items.append(f"voice: `{voice}`")
        if cfg.get("local"):
            items.append("local")
        first = items[0] if items else "(not configured)"
        rest = " · ".join(items[1:])
        return f"  {name}: {first}" + (f" · {rest}" if rest else "")

    for cap in ["stt", "tts", "vision", "image_generation", "browser", "web_browser", "web_extract"]:
        if cap in caps:
            parts.append(_fmt(cap, caps[cap]))

    cap_exec = caps.get("executor", {}).get("model", "")
    top_exec = exec_cfg.get("model", "")
    if cap_exec and top_exec and cap_exec != top_exec:
        parts.append("")
        parts.append(f"❓ Two executors: capabilities.executor=`{cap_exec}` != executor.model=`{top_exec}`")

    return "\n".join(parts)

# ── Permission handlers ─────────────────────────────────────────
#
# /permit config:providers.openrouter session    — approve once
# /permit config:providers.openrouter permanent  — approve forever
# /permit config:providers. permanent           — approve all provider config
# /block config:providers.openrouter             — block forever
# /permissions                                    — list all
# /reset-perms config:providers.openrouter        — clear approval


def _cmd_permit(args: list[str], data_dir: Path) -> str:
    """Grant a permission. Usage: /permit <scope> [duration]"""
    if not args:
        return (
            "Usage: /permit <scope> [duration]\n"
            "  scope: e.g. config:providers.openrouter, config:providers., config:model.default\n"
            "  duration: session (default), permanent, 5m, 1h, 1d\n"
            "Examples:\n"
            "  /permit config:providers.openrouter session\n"
            "  /permit config:providers.openrouter permanent\n"
            "  /permit config:providers. permanent  — allow all provider config\n"
            "  /permit deploy session"
        )

    from .permissions import grant

    scope = args[0]
    duration = args[1] if len(args) >= 2 else "session"

    grant(scope, duration, data_dir)

    if duration == "session":
        return f"Granted: {scope} (session — cleared on restart)"
    elif duration == "permanent":
        return f"Granted: {scope} (permanent — saved to permissions.json)"
    else:
        return f"Granted: {scope} ({duration})"


def _cmd_block(args: list[str], data_dir: Path) -> str:
    """Block a permission. Usage: /block <scope> [duration]"""
    if not args:
        return "Usage: /block <scope> [session|permanent]"

    from .permissions import block

    scope = args[0]
    duration = args[1] if len(args) >= 2 else "permanent"
    block(scope, duration, data_dir)

    return f"Blocked: {scope} ({duration})"


def _cmd_permissions(data_dir: Path) -> str:
    """List all permissions (session + persistent)."""
    from .permissions import list_perms

    perms = list_perms(data_dir)
    if not perms:
        return "No permissions configured. All sensitive operations will prompt for approval."

    lines = ["Current Permissions:", ""]
    for scope, entry in sorted(perms.items()):
        icon = "G" if entry["level"] == "granted" else ("B" if entry["level"] == "blocked" else "?")
        source = entry.get("source", "")
        expires = f" (expires: {entry.get('expires_in', 'N/A')})" if entry.get("expires_in") else ""
        lines.append(f"  {icon} {scope} — {entry['level']} ({source}){expires}")

    return "\n".join(lines)


def _cmd_reset_perms(scope: str | None, data_dir: Path) -> str:
    """Reset permission(s) to default (ask)."""
    from .permissions import reset

    if scope:
        reset(scope, data_dir)
        return f"Reset: {scope} → will ask next time"
    else:
        reset(None, data_dir)
        return "Reset all permissions → everything will ask next time"
