"""
BAW — Slash Commands (P1)
Interactive mode commands for quick operations.
"""

from __future__ import annotations
import sys
import subprocess
import time as _time
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
            return "Usage: /model <id>  (see config.yaml for available models)"
        return _cmd_model(model_id, config)

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
]

def _cmd_help() -> str:
    """Auto-generated from _HELP_COMMANDS registry."""
    from collections import defaultdict
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in _HELP_COMMANDS:
        grouped[entry["cat"]].append(entry)

    lines = ["⚡ **BAW Commands**", ""]
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
        "⚡ BAW Status",
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


def _cmd_court() -> str:
    """Show the last court verdict — both voices, independent."""
    global LAST_COURT_VERDICT
    if not LAST_COURT_VERDICT:
        return "No previous court verdict. Run a prompt with adversarial enabled first."

    d = LAST_COURT_VERDICT
    return (
        f"⚖️ **BAW Court — Independent Analysis**\n\n"
        f"👿 **Devil (Independent Critique)** — Risk: {d.get('devil_score', '?')}/10\n"
        f"{d.get('devil', {}).get('content', 'N/A')}\n\n"
        f"😇 **Angel (Independent Support)** — Feasibility: {d.get('angel_score', '?')}/10\n"
        f"{d.get('angel', {}).get('content', 'N/A')}\n\n"
        f"━━━ **Assessment** ───\n"
        f"Agreement level: {d.get('agreement_level', '?')} "
        f"(gap: {d.get('score_gap', '?')} pts)\n"
        f"Neither voice has execution power in court. "
        f"BAW synthesizes both perspectives neutrally."
    )


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
