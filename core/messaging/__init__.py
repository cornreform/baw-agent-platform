"""
BAW — Messaging Platform Connector Framework

Abstract base for all messaging platform connectors.
Each connector implements:
  - connect() / disconnect() — lifecycle
  - on_message(msg) — receive from platform
  - send(chat_id, text) — send to platform
  - start() / stop() — polling loop

Connectors auto-register via the @connector decorator.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("baw.messaging")  # v2-builtin-cmds


@dataclass
class Message:
    """Normalized message from any platform."""
    platform: str          # "telegram", "discord", etc.
    chat_id: str           # Platform-specific chat/channel ID
    user_id: str           # Platform-specific user ID
    text: str              # Message text
    raw: dict = field(default_factory=dict)  # Original platform payload


@dataclass
class ConnectorDef:
    """Registered connector definition."""
    name: str
    description: str
    handler: type
    config_key: str = ""  # Key in config.yaml (e.g. "telegram")


_registry: dict[str, ConnectorDef] = {}


def register(name: str, description: str = "", config_key: str = ""):
    """Decorator to register a connector class."""
    def wrapper(cls):
        _registry[name] = ConnectorDef(
            name=name,
            description=description or cls.__doc__ or "",
            handler=cls,
            config_key=config_key or name,
        )
        return cls
    return wrapper


def list_connectors() -> list[ConnectorDef]:
    """List all registered connectors."""
    return list(_registry.values())


def get_connector(name: str) -> Optional[ConnectorDef]:
    """Get a connector definition by name."""
    return _registry.get(name)


class BaseConnector(ABC):
    """Abstract base for a messaging platform connector."""

    def __init__(self, config: dict, on_message: Callable[[Message], None]):
        self.config = config
        self._on_message = on_message
        self._running = False
        self._thread: threading.Thread | None = None
        self._name = self.__class__.__name__
        self._cancel_event = threading.Event()
        self._busy = False
        self._max_concurrency = self.config.get("max_concurrency", 3)
        self._active_count = 0
        self._active_lock = threading.Lock()
        self._batch_results: list[dict] = []
        self._batch_lock = threading.Lock()
        self._batch_chat_id: str | None = None
        self._restart_requested = False
        self._chat_config = {}  # per-chat overrides: {chat_id: {key: value}}
        self._restart_chat_id: str | None = None
        # ── Session management ──
        self._sessions: dict[str, dict] = {}  # {chat_id: session_dict}
        self._sessions_dir = Path.home() / ".baw" / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._load_session_index()

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the platform. Return True on success."""
        ...

    def _acquire_slot(self) -> bool:
        """Try to acquire a processing slot. Returns True if slot available."""
        with self._active_lock:
            if self._active_count < self._max_concurrency:
                self._active_count += 1
                return True
            return False

    def _release_slot(self):
        """Release a processing slot. Triggers batch synthesis in background when idle."""
        with self._active_lock:
            self._active_count = max(0, self._active_count - 1)
            was_last = self._active_count == 0
        if was_last:
            threading.Thread(target=self._synthesize_batch, daemon=True).start()

    def _record_batch_result(self, chat_id: str, summary: str, msg_type: str = "text"):
        """Record a concurrent task's result for batch synthesis."""
        with self._batch_lock:
            self._batch_results.append({
                "chat_id": chat_id,
                "type": msg_type,
                "summary": summary[:500],
                "ts": time.time(),
            })
            self._batch_chat_id = chat_id or self._batch_chat_id

    def _synthesize_batch(self):
        """Consolidate all batch results and write consolidated memory for evolution."""
        with self._batch_lock:
            results = list(self._batch_results)
            self._batch_results.clear()
            chat_id = self._batch_chat_id
            self._batch_chat_id = None

        if len(results) < 2:
            return  # Single task — no synthesis needed

        logger.info(f"[Synthesis] Consolidating {len(results)} concurrent results")
        try:
            baw = self._baw_ensure()
            data_dir = baw["data_dir"]
            from core.memory import Memory
            mem = Memory(data_dir=data_dir)

            # Build synthesis prompt
            items = "\n\n".join(
                f"[{r['type']}] {r['summary'][:300]}"
                for r in results
            )
            synthesis_prompt = (
                f"[Batch Synthesis — {len(results)} concurrent tasks completed]\n\n"
                f"Tasks:\n{items}\n\n"
                f"Write a concise consolidated summary of all the work done above. "
                f"Focus on: what was accomplished, key decisions, and any patterns observed. "
                f"Keep it brief (3-5 sentences)."
            )

            # Call BAW for synthesis (silent — no user-facing output)
            config = baw["config"]
            model_cfg = config.get("model", {})
            default_model = model_cfg.get("default", "deepseek-v4-flash")
            from core.llm import get_model, call_llm_with_fallback
            model = get_model(config, default_model)
            from core.context import Context

            ctx = Context(
                system_prompt="You are BAW's batch synthesis agent. Consolidate results concisely.",
                temperature=model.temperature,
            )
            ctx.add_user(synthesis_prompt)
            fb = call_llm_with_fallback(
                config, ctx.to_openai_messages(),
                temperature=model.temperature,
            )
            synthesis = fb.response.content or ""

            # Write to memory for evolution
            if synthesis.strip():
                mem.remember(
                    f"[Batch] Concurrent synthesis: {synthesis[:200]}"
                )
                logger.info(f"[Synthesis] Written to memory: {synthesis[:80]}...")
        except Exception as e:
            logger.warning(f"[Synthesis] Failed: {e}")

    @abstractmethod
    def disconnect(self):
        """Disconnect from the platform."""
        ...

    @abstractmethod
    def send(self, chat_id: str, text: str) -> bool:
        """Send a message to a chat. Return True on success."""
        ...

    def start(self):
        """Start the polling loop in a background thread."""
        if self._running:
            return
        if not self.connect():
            logger.error(f"[{self._name}] Failed to connect")
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"[{self._name}] Started")

    def stop(self):
        """Stop the polling loop."""
        self._running = False
        self.disconnect()
        logger.info(f"[{self._name}] Stopped")

    @abstractmethod
    def _poll_loop(self):
        """Platform-specific polling loop."""
        ...

    # ── Session management ──────────────────────────────────────
    _MAX_SESSION_MSGS = 60  # ~30 user/assistant exchanges

    def _load_session_index(self):
        """Load all saved session file IDs into memory (not full history)."""
        if not self._sessions_dir.exists():
            return
        sidx = {}
        for f in sorted(self._sessions_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sidx[data["id"]] = {
                    "id": data["id"],
                    "name": data.get("name", "untitled"),
                    "created": data.get("created", 0.0),
                    "updated": data.get("updated", 0.0),
                    "mode": data.get("mode", "quick"),
                    "path": str(f),
                }
            except Exception:
                continue
        self._session_index = sidx

    def _get_or_create_session(self, chat_id: str) -> dict:
        """Get (or create) the active in-memory session for this chat."""
        if chat_id not in self._sessions:
            sid = f"ses-{uuid.uuid4().hex[:12]}"
            self._sessions[chat_id] = {
                "id": sid,
                "name": "untitled",
                "messages": [],
                "created": time.time(),
                "updated": time.time(),
            }
        return self._sessions[chat_id]

    def _save_session_to_disk(self, session: dict):
        """Write session to disk as JSON."""
        spath = self._sessions_dir / f"{session['id']}.json"
        try:
            spath.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Session save failed [{session['id']}]: {e}")

    def _list_saved_sessions(self) -> str:
        """Return formatted list of all saved sessions."""
        self._load_session_index()
        if not self._session_index:
            return "No saved tasks."
        lines = ["📋 **Saved Tasks:**"]
        for sid, s in sorted(self._session_index.items(),
                             key=lambda x: x[1]["updated"], reverse=True):
            import datetime
            dt = datetime.datetime.fromtimestamp(s["updated"]).strftime("%m-%d %H:%M")
            msg_count = "(active)" if sid in [ses["id"] for ses in self._sessions.values()] else ""
            lines.append(
                f"  `{sid[:12]}` — **{s['name']}** "
                f"({dt}) {msg_count}"
            )
        return "\n".join(lines)

    def _load_session_from_disk(self, session_id: str) -> Optional[dict]:
        """Load a full session from disk. Returns None if not found."""
        spath = self._sessions_dir / f"{session_id}.json"
        if spath.exists():
            try:
                data = json.loads(spath.read_text(encoding="utf-8"))
                return data
            except Exception:
                return None
        # Try prefix match
        for f in self._sessions_dir.glob(f"{session_id}*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                return data
            except Exception:
                continue
        return None

    def _delete_session(self, session_id: str) -> bool:
        """Delete a saved session from disk. Returns True if deleted."""
        spath = self._sessions_dir / f"{session_id}.json"
        if spath.exists():
            spath.unlink()
            # Remove from in-memory active sessions too
            for cid, ses in list(self._sessions.items()):
                if ses["id"] == session_id:
                    del self._sessions[cid]
                    break
            return True
        for f in self._sessions_dir.glob(f"{session_id}*.json"):
            f.unlink()
            for cid, ses in list(self._sessions.items()):
                if ses["id"] == session_id:
                    del self._sessions[cid]
                    break
            return True
        return False

    def _save_restart_chat_id(self, chat_id: str):
        """Save chat_id to .restart_pending so the new process can notify."""
        import json as _json
        self._restart_chat_id = chat_id
        pending_file = Path.home() / ".baw" / ".restart_pending"
        try:
            pending_file.write_text(
                _json.dumps({"chat_id": chat_id, "ts": time.time()}),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save restart pending: {e}")

    def route(self, msg: Message) -> Optional[str]:
        """Route a message to BAW and return the response.

        Handles built-in commands before passing to BAW.
        """
        text = msg.text.strip()

        # Built-in commands
        if text.startswith("/"):
            parts = text[1:].strip().split(maxsplit=1)
            cmd = parts[0].lower() if parts else ""
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ("start", "help", "h"):
                return self._help_text()

            if cmd in ("status", "s"):
                return self._run_baw("--status")

            if cmd == "btw" and arg:
                return self._run_baw(f'--btw "{arg}"')

            if cmd in ("court", "ct"):
                from ..commands import _cmd_court
                return _cmd_court()

            if cmd in ("memory", "remember", "r") and arg:
                return self._run_baw(f'--remember "{arg}"')

            if cmd in ("search", "find") and arg:
                return self._run_baw(f'--search "{arg}"')

            if cmd == "board":
                import subprocess as sp
                p = sp.run(["baw", "--board"], capture_output=True, text=True, timeout=30)
                return p.stdout or p.stderr or "Dashboard generated"

            if cmd in ("version", "v"):
                return self._run_baw("--version")

            if cmd in ("exit", "quit"):
                return "👋 Goodbye!"

            if cmd in ("stop",):
                self._cancel_event.set()
                self._busy = False
                return "⏹ Stopped."

            if cmd in ("restart",):
                self._restart_requested = True
                self._save_restart_chat_id(msg.chat_id)
                return "🔄 Restarting BAW engine..."

            if cmd in ("reload",):
                return self._baw_reload()

            if cmd in ("evolve", "ev"):
                try:
                    from ..evolve import get_evolve_stats
                    return get_evolve_stats()
                except Exception as e:
                    return f"❌ Evolution stats error: {e}"

            # ── Top-level session aliases ──
            if cmd == "new":
                return self._handle_task_command(msg.chat_id, "new", arg)
            if cmd == "list":
                return self._handle_task_command(msg.chat_id, "list", "")
            if cmd == "resume" and arg:
                return self._handle_task_command(msg.chat_id, "resume", arg)
            if cmd == "resume":
                return "Usage: /resume <session_id>\nUse /list to see available sessions."
            if cmd == "summarize":
                return self._summarize_session(msg.chat_id)

            # ── Per-chat config commands ──
            if cmd == "mode" and arg:
                cfg = self._chat_config.setdefault(msg.chat_id, {})
                cfg["mode"] = arg
                return f"✅ Chat mode set to: {arg}"

            if cmd == "mode":
                cc = self._chat_config.get(msg.chat_id, {})
                current = cc.get("mode", self.config.get("mode", "tight"))
                return (
                    f"Current mode: {current}\n"
                    f"Available: quick, hybrid, tight"
                )

            if cmd == "tone" and arg:
                cfg = self._chat_config.setdefault(msg.chat_id, {})
                cfg["tone"] = arg
                return f"✅ Chat tone set to: {arg}"

            if cmd == "tone":
                cc = self._chat_config.get(msg.chat_id, {})
                tones = ["casual", "business", "teaching", "client-doc", "ot-rt", "stepwise"]
                current = cc.get("tone", self.config.get("tone", {}).get("default", "casual"))
                return f"Current tone: {current}\nAvailable: {', '.join(tones)}"

            if cmd in ("model", "m") and arg:
                if arg not in self._MODELS:
                    return f"Model '{arg}' not found. Available: {', '.join(self._MODELS)}"
                cfg = self._chat_config.setdefault(msg.chat_id, {})
                cfg["model"] = arg
                return f"✅ Chat model set to: {arg}"

            if cmd in ("model", "models"):
                cc = self._chat_config.get(msg.chat_id, {})
                current = cc.get("model") or "deepseek-v4-flash"
                return (
                    f"Current model: {current}\n"
                    f"Available:\n"
                    + "\n".join(f"  /model {m}" for m in self._MODELS)
                )


            # ── Capability commands ──
            if cmd == "capability":
                from core.commands_capability import handle_capability_command
                return handle_capability_command(arg, self._baw_ensure())

            # ── Session / Task commands ──
            if cmd == "task" and arg:
                sub_cmd = arg.strip().split(maxsplit=1)
                task_action = sub_cmd[0].lower() if sub_cmd else ""
                task_arg = sub_cmd[1] if len(sub_cmd) > 1 else ""
                return self._handle_task_command(msg.chat_id, task_action, task_arg)

        # Default: pass to BAW (with chat_id for per-chat config + session history)
        # Layer 2: Track user feedback for self-evolution
        try:
            from ..evolve import track_user_feedback
            track_user_feedback(text, session_id=msg.chat_id or "")
        except Exception:
            pass
        return self._run_baw(text, chat_id=msg.chat_id)

    # ── Session / Task command handler ───────────────────────────
    def _handle_task_command(self, chat_id: str, action: str, arg: str) -> str:
        if action == "new":
            # Save current session, start fresh
            self._save_session_to_disk(self._get_or_create_session(chat_id))
            new_sid = f"ses-{uuid.uuid4().hex[:12]}"
            name = arg or "untitled"
            self._sessions[chat_id] = {
                "id": new_sid, "name": name,
                "messages": [], "created": time.time(), "updated": time.time(),
            }
            self._save_session_to_disk(self._sessions[chat_id])
            return f"✅ New task started: **{name}** (`{new_sid[:12]}`)"

        elif action == "list" or action == "ls":
            return self._list_saved_sessions()

        elif action == "resume" or action == "load":
            sid = arg or ""
            if not sid:
                return "Usage: /task resume <session_id>"
            data = self._load_session_from_disk(sid)
            if not data:
                return f"Session `{sid}` not found. Use `/task list` to see saved tasks."
            # Assign to this chat
            self._sessions[chat_id] = {
                "id": data["id"], "name": data.get("name", "untitled"),
                "messages": data.get("messages", []),
                "created": data.get("created", 0.0), "updated": time.time(),
            }
            msg_count = len(self._sessions[chat_id]["messages"])
            return (
                f"📂 Resumed task: **{data.get('name', 'untitled')}** "
                f"(`{data['id'][:12]}`)\n"
                f"Conversation has {msg_count} messages. "
                f"Continue chatting to pick up where you left off."
            )

        elif action == "save" or action == "name":
            ses = self._get_or_create_session(chat_id)
            if arg:
                ses["name"] = arg
            ses["updated"] = time.time()
            self._save_session_to_disk(ses)
            return f"💾 Task saved: **{ses['name']}** (`{ses['id'][:12]}`)"

        elif action in ("forget", "delete", "rm"):
            sid = arg or ""
            if not sid:
                return "Usage: /task forget <session_id>"
            if self._delete_session(sid):
                return f"🗑️ Task `{sid[:12]}` deleted."
            return f"Task `{sid[:12]}` not found."

        elif action in ("info", "show"):
            ses = self._get_or_create_session(chat_id)
            return (
                f"📌 **Current Task**\n"
                f"  ID: `{ses['id'][:12]}`\n"
                f"  Name: **{ses['name']}**\n"
                f"  Messages: {len(ses['messages'])}\n"
                f"  Created: {time.strftime('%m-%d %H:%M', time.localtime(ses['created']))}"
            )

        else:
            return (
                "**Task commands:**\n"
                "  `/task list` — Show saved tasks\n"
                "  `/task new [name]` — Save current & start fresh\n"
                "  `/task resume <id>` — Resume a saved task\n"
                "  `/task save [name]` — Save/name current task\n"
                "  `/task forget <id>` — Delete a saved task\n"
                "  `/task info` — Show current task details"
            )

    def _summarize_session(self, chat_id: str) -> str:
        """Summarize the current session via LLM."""
        ses = self._get_or_create_session(chat_id)
        msgs = ses.get("messages", [])
        if not msgs:
            return "📭 No messages in current session to summarize."

        # Build summary prompt from session messages
        summary_text = "\n".join(
            f"[{m.get('role','?')}] {m.get('content','')[:200]}"
            for m in msgs[-20:]  # Last 20 exchanges
        )

        try:
            baw = self._baw_ensure()
            run_agent = baw["run_agent"]
            config = baw["config"]
            prompt = (
                f"Summarize the following conversation in Traditional Chinese (Cantonese). "
                f"Extract key decisions, important facts, and any pending actions. "
                f"Format as bullet points. Keep it concise.\n\n{summary_text}"
            )
            response, info = run_agent(
                prompt=prompt,
                config=config,
                data_dir=baw["data_dir"],
                mode="quick",
                fresh_start=True,
            )
            return f"📋 **Session Summary** (`{ses['id'][:12]}`)\n\n{response}"
        except Exception as e:
            return f"❌ Summarization failed: {e}"

    # ── In-process BAW engine (lazy-loaded) ──
    _BAW = None  # {'run_agent': fn, 'config': dict, 'data_dir': Path}
    _MODELS = ["deepseek-v4-flash", "kimi-k2.6", "MiniMax-M3"]

    def _baw_ensure(self):
        """Lazy-import BAW modules in-process (no subprocess)."""
        if self._BAW is not None:
            return self._BAW
        import os, sys, yaml
        from pathlib import Path

        # Add BAW to sys.path (same as baw script does)
        baw_root = Path(__file__).resolve().parent.parent.parent  # ~/baw/ (3 levels: messaging → core → baw)
        sys.path.insert(0, str(baw_root))
        sys.path.insert(0, str(baw_root.parent))

        # Import once (project root is ~/baw/; core/ and tools/ are top-level packages)
        from core.loop import run_agent

        # Register tools directly, bypassing tools/__init__.py which uses
        # relative imports that fail when imported as a top-level package
        import importlib.util as _iu
        def _ld(name):
            p = os.path.join(baw_root, 'tools', f'{name}.py')
            s = _iu.spec_from_file_location(f'_tk_{name}', p)
            if s is None or s.loader is None:
                raise ImportError(f"Cannot load tool '{name}' from {p}")
            m = _iu.module_from_spec(s)
            s.loader.exec_module(m)
            return m.TOOL_DEF
        from core.tools import register as _reg, clear as _clear
        _clear()
        _reg(**_ld('bash'))
        _reg(**_ld('read_file'))
        _reg(**_ld('write_file'))
        _reg(**_ld('web_search'))
        _reg(**_ld('delegate_task'))

        # Load config
        data_dir = Path.home() / ".baw"
        config = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))

        # Load env vars (API keys)
        env_file = data_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if v:
                        os.environ.setdefault(k.strip(), v)

        self._BAW = {"run_agent": run_agent, "config": config, "data_dir": data_dir}
        return self._BAW

    def _baw_reload(self) -> str:
        """Hot-reload tools, config, and SOUL without restarting the bot."""
        import os, sys, yaml, importlib.util as _iu
        from pathlib import Path

        baw_root = Path(__file__).resolve().parent.parent.parent
        if str(baw_root) not in sys.path:
            sys.path.insert(0, str(baw_root))

        # Reload tool modules from source (clear + re-register)
        from core.tools import register as _reg, clear as _clear
        _clear()

        tool_names = ["bash", "read_file", "write_file", "web_search", "delegate_task"]
        errors = []
        for name in tool_names:
            try:
                p = os.path.join(baw_root, 'tools', f'{name}.py')
                s = _iu.spec_from_file_location(f'_tk_{name}_r', p)
                if s is None or s.loader is None:
                    errors.append(f"{name}: spec not found")
                    continue
                m = _iu.module_from_spec(s)
                s.loader.exec_module(m)
                _reg(**m.TOOL_DEF)
            except Exception as e:
                errors.append(f"{name}: {e}")

        # Re-read config
        data_dir = Path.home() / ".baw"
        try:
            config = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))
        except Exception as e:
            return f"❌ Reload failed: config error: {e}"

        # Re-load env vars
        env_file = data_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if v:
                        os.environ.setdefault(k.strip(), v)

        # Re-import run_agent (fresh module)
        try:
            import importlib as _im
            _loop = _im.import_module("core.loop")
            _im.reload(_loop)
            run_agent = _loop.run_agent
        except Exception as e:
            return f"❌ Reload failed: loop reload error: {e}"

        self._BAW = {"run_agent": run_agent, "config": config, "data_dir": data_dir}
        status = f"✅ Reloaded {len(tool_names) - len(errors)}/{len(tool_names)} tools"
        if errors:
            status += f" | ⚠️ {len(errors)} errors: {'; '.join(errors[:3])}"
        return status

    def _baw_cfg_set(self, key: str, value: str) -> str:
        """Set a config value both in-file and in-memory cache."""
        import yaml
        baw = self._baw_ensure()
        data_dir = baw["data_dir"]
        config = baw["config"]
        cfg_path = data_dir / "config.yaml"

        # Navigate dotted key (e.g. "model.default")
        keys = key.split(".")
        target = config
        for k in keys[:-1]:
            target = target.setdefault(k, {})
        target[keys[-1]] = value

        # Write back to file
        cfg_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")

        # Sync to self.config (Telegram connector's in-memory config)
        # so _run_baw() reads the updated value immediately
        sync = self.config
        for k in keys[:-1]:
            sync = sync.setdefault(k, {})
        sync[keys[-1]] = value

        return f"✅ Config updated: {key} → {value}"

    _TOOL_ICONS = {
        "bash": "🔎",
        "read_file": "📖",
        "write_file": "✏️",
        "web_search": "🌐",
        "web_extract": "📄",
        "patch": "🔧",
        "search_files": "🔍",
        "terminal": "💻",
        "delegate_task": "🤖",
    }

    @staticmethod
    def _format_tool_log(messages: list[dict]) -> str:
        """Compact step log — one line per tool call, no raw args."""
        if not messages:
            return ""
        tool_lines = []
        count = 0
        for msg in messages:
            role = msg.get("role", "")
            if role == "assistant":
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    count += 1
                    icon = BaseConnector._TOOL_ICONS.get(name, "🛠️")
                    tool_lines.append(f"  {icon} {name}")
        if not tool_lines:
            return ""
        header = f"⚙️ **{count} steps**"
        return header + "\n" + "\n".join(tool_lines)

    def _run_baw(self, prompt: str, chat_id: str | None = None) -> str:
        """Run BAW in-process (no subprocess), with session history."""
        import re
        import asyncio
        from concurrent.futures import ThreadPoolExecutor, TimeoutError

        # If cancel was requested, consume it and return immediately
        if self._cancel_event.is_set():
            self._cancel_event.clear()
            self._busy = False
            return "⏹ Previous request was cancelled."

        try:
            baw = self._baw_ensure()
            run_agent = baw["run_agent"]
            config = baw["config"]
            data_dir = baw["data_dir"]

            # mode from per-chat config > global config > default
            cc = self._chat_config.get(chat_id, {}) if chat_id else {}
            mode = cc.get("mode") or config.get("mode", "quick")

            # ── Session management ──
            session = None
            if chat_id:
                session = self._get_or_create_session(chat_id)
                conv_history = session["messages"][-self._MAX_SESSION_MSGS:] if session["messages"] else None

                # ── Context Window Monitoring ──
                # Estimate token usage from session messages
                if conv_history and config:
                    _msg_tokens = sum(
                        len(m.get("content", "") or "") * 0.25
                        for m in conv_history
                    )
                    _estimated_tokens = int(_msg_tokens)

                    # Get default model's context window
                    _cw = 65536  # safe default
                    _model_id = config.get("model", {}).get("default", "deepseek-v4-flash")
                    for _p in config.get("providers", {}).values():
                        for _m in _p.get("models", []):
                            if _m["id"] == _model_id:
                                _cw = _m.get("context_window", 65536)
                                break

                    _usage_pct = (_estimated_tokens / _cw) * 100
                    if _usage_pct > 70:
                        _warn = (
                            f"[System Note: Context ~{_usage_pct:.0f}% used "
                            f"(~{_estimated_tokens:,}/{_cw:,} tokens). "
                            f"Consider summarizing key points, saving to memory (/memory), "
                            f"or starting a new session (/task new).]"
                        )
                        # Inject as user message (loop.py only handles user/assistant/tool roles)
                        conv_history = conv_history + [{"role": "user", "content": _warn}]
                        logger.info(f"[Context] {_usage_pct:.0f}% full — warning injected")
                    elif _usage_pct > 50:
                        logger.info(f"[Context] {_usage_pct:.0f}% full — monitoring")
            else:
                conv_history = None

            # ── Progress tracking (per-event timeout) ──
            _last_progress = time.time()
            _progress_lock = threading.Lock()

            def _on_progress():
                with _progress_lock:
                    nonlocal _last_progress
                    _last_progress = time.time()

            # Run BAW with a timeout via thread pool
            with ThreadPoolExecutor(1) as pool:
                fut = pool.submit(
                   run_agent,
                   prompt=prompt,
                   config=config,
                   data_dir=data_dir,
                   mode=mode,
                   verbose=False,
                   conversation_history=conv_history,
                   progress_callback=_on_progress,
                )
                # Poll for result with cancel checking every 1s
                import time as _time
                _elapsed = 0
                _max_wait = 300
                while _elapsed < _max_wait:
                   try:
                       response, info = fut.result(timeout=1)
                       break
                   except TimeoutError:
                       _elapsed += 1
                       # Reset timeout if progress was made recently
                       with _progress_lock:
                           if _last_progress > time.time() - 60:
                               _elapsed = 0
                       if self._cancel_event.is_set():
                           fut.cancel()
                           return "⏹ Cancelled."
                else:
                    # Timeout after 120s without result
                    fut.cancel()
                    return "⏳ Task took too long (>5min). Try /stop to cancel, or split into smaller steps."

            output = response or ""

            # ── Format tool call log from session messages ──
            tool_log = self._format_tool_log(info.get("new_session_messages", []))
            if tool_log:
                output = tool_log + "\n\n" + output

            # ── Save session history ──
            if session and info:
                new_msgs = info.get("new_session_messages", [])
                if new_msgs:
                    session["messages"].extend(new_msgs)
                    # Trim to max message count (keep latest)
                    if len(session["messages"]) > self._MAX_SESSION_MSGS:
                        session["messages"] = session["messages"][-self._MAX_SESSION_MSGS:]
                session["updated"] = time.time()
                self._save_session_to_disk(session)

            # Strip HTML tags for Telegram display
            output = re.sub(r'<[^>]+>', '', output)
            # Limit to 4000 chars
            if len(output) > 4000:
                output = output[:3997] + "..."
            return output.strip()

        except Exception as e:
            return f"❌ BAW error: {e}"

    @staticmethod
    def _help_text() -> str:
        return (
            "🤖 **BAW Bot** — Multi-platform Agent Interface\n\n"
            "Simply type anything and BAW will process it.\n\n"
            "**Commands:**\n"
            "/stop — Stop current processing and cancel (per-chat)\n"
            "/restart — Restart BAW engine (per-chat)\n"
            "/btw `<text>` — Quick answer (no court)\n"
            "/model `<name>` — Switch model (deepseek / kimi / minimax)\n"
            "/models — List available models\n"
            "/mode `quick|hybrid|tight` — Switch execution mode\n"
            "/tone `<profile>` — Switch tone\n"
            "/status — BAW system status\n"
            "/court — Show last Angel/Devil verdict\n"
            "/memory `<text>` — Save a memory\n"
            "/search `<query>` — Search memories\n"
            "/board — Generate HTML dashboard\n"
            "/version — BAW version\n"
            "/new [name] — Start a new session (alias for /task new)\n"
            "/list — List saved sessions (alias for /task list)\n"
            "/resume <id> — Resume a saved session\n"
            "/summarize — Summarize current session to memory\n"
            "/task `<action>` — Task session manager\n"
            "/help — This message\n\n"
            "**Task commands:**\n"
            "  `/task list` — Show saved tasks\n"
            "  `/task new [name]` — Save current & start fresh\n"
            "  `/task resume <id>` — Resume a saved task\n"
            "  `/task save [name]` — Save/name current task\n"
            "  `/task forget <id>` — Delete a saved task\n"
            "  `/task info` — Show current task details\n\n"
            "**Examples:**\n"
            "• list files in current directory\n"
            "• check disk space\n"
            "• /btw What time is it?\n"
            "• /tone teaching\n"
            "• /model kimi-k2.6"
        )
