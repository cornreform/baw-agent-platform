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
        # ── Message queue ──
        self._message_queue: list[dict] = []  # [{chat_id, user_id, user_name, text, msg, reply_to}]
        self._queue_lock = threading.Lock()
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
        """Release a processing slot. If messages are queued, process the next one."""
        with self._active_lock:
            self._active_count = max(0, self._active_count - 1)
            was_last = self._active_count == 0
        if was_last:
            threading.Thread(target=self._synthesize_batch, daemon=True).start()
        # ── Process next queued message if any ──
        self._dequeue_next()

    def _enqueue_message(self, chat_id: str, user_id: int, user_name: str, text: str, msg: dict, msg_type: str = "text") -> int:
        """Enqueue a message for later processing. Returns queue position (1-indexed).

        msg_type: 'text', 'photo', 'document', or 'voice' — determines which handler is called.
        """
        with self._queue_lock:
            self._message_queue.append({
                "chat_id": chat_id,
                "user_id": user_id,
                "user_name": user_name,
                "text": text,
                "msg": msg,
                "msg_type": msg_type,
            })
            return len(self._message_queue)

    def _dequeue_next(self):
        """Try to dequeue and process the next message. Called on slot release."""
        with self._queue_lock:
            if not self._message_queue:
                return
            next_msg = self._message_queue.pop(0)

        # Acquire the slot (should always succeed since we just released)
        if self._acquire_slot():
            self._cancel_event.clear()
            threading.Thread(
                target=self._dispatch_queued,
                args=(next_msg,),
                daemon=True,
            ).start()

    def _dispatch_queued(self, item: dict):
        """Dispatch a queued message to the appropriate handler based on msg_type."""
        msg_type = item.get("msg_type", "text")
        chat_id = item["chat_id"]
        # Better queue UX: show position and ETA
        queue_pos = len(self._message_queue) + 1
        eta = queue_pos * 8  # rough 8s per task (conservative for mobile)
        self.send(chat_id, f"⏳ Queued (#{queue_pos}, ~{eta}s)...")
        if msg_type == "text":
            self._process_message(
                chat_id, item["user_id"], item["user_name"],
                item["text"], item["msg"]
            )
        elif msg_type == "photo":
            photo_data = max(item["msg"].get("photo", []), key=lambda p: p.get("file_size", 0))
            self._process_image_file(chat_id, photo_data, item["msg"])
        elif msg_type == "document":
            self._process_document_file(chat_id, item["msg"].get("document"), item["msg"])
        elif msg_type == "voice":
            voice_data = item["msg"].get("audio") or item["msg"].get("voice")
            self._process_voice_file(chat_id, voice_data, item["msg"])
        else:
            logger.warning(f"[Queue] Unknown msg_type '{msg_type}' — falling back to text")
            self._process_message(
                chat_id, item["user_id"], item["user_name"],
                item["text"], item["msg"]
            )

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

    def _get_or_create_session(self, chat_id: str, first_message: str = "") -> dict:
        """Get (or create) the active in-memory session for this chat.
        If creating new, auto-name from first_message (first 40 chars, stripped)."""
        if chat_id not in self._sessions:
            sid = f"ses-{uuid.uuid4().hex[:12]}"
            # Auto-name from first message
            name = "untitled"
            if first_message.strip():
                clean = first_message.strip().replace("\n", " ")[:40]
                name = clean if len(clean) >= 3 else "untitled"
            self._sessions[chat_id] = {
                "id": sid,
                "name": name,
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

            if cmd in ("update", "upgrade", "up"):
                return self._update_with_progress(msg.chat_id)

            if cmd in ("evolve", "ev"):
                try:
                    from ..evolve import get_evolve_stats
                    return get_evolve_stats()
                except Exception as e:
                    return f"❌ Evolution stats error: {e}"

            # ── Set config value (persist to config.yaml) ──
            if cmd == "set" and arg:
                parts = arg.strip().split(maxsplit=1)
                if len(parts) < 2:
                    return "Usage: /set <key> <value>\nExample: `/set model.default deepseek-v4-flash`"
                key, value = parts
                try:
                    baw = self._baw_ensure()
                    cfg = baw["config"]
                    # Navigate dotted key (e.g. "model.default")
                    keys = key.split(".")
                    target = cfg
                    for k in keys[:-1]:
                        target = target.setdefault(k, {})
                    target[keys[-1]] = value
                    # Persist to yaml
                    import yaml
                    data_dir = baw["data_dir"]
                    (data_dir / "config.yaml").write_text(
                        yaml.dump(cfg, default_flow_style=False, allow_unicode=True),
                        encoding="utf-8",
                    )
                    return f"✅ `{key}` set to `{value}` (saved to config.yaml)"
                except Exception as e:
                    return f"❌ Failed to set `{key}`: {e}"

            # ── Top-level session aliases ──
            if cmd == "new":
                return self._handle_task_command(msg.chat_id, "new", arg)
            if cmd == "reset":
                # Hard reset — clear current session without saving
                if msg.chat_id in self._sessions:
                    old = self._sessions[msg.chat_id]
                    # Delete saved session file too
                    self._delete_session(old["id"])
                new_sid = f"ses-{uuid.uuid4().hex[:12]}"
                self._sessions[msg.chat_id] = {
                    "id": new_sid, "name": "fresh",
                    "messages": [], "created": time.time(), "updated": time.time(),
                }
                return "🔄 Session reset — starting fresh."
            if cmd == "list":
                return self._handle_task_command(msg.chat_id, "list", "")
            if cmd == "resume" and arg:
                return self._handle_task_command(msg.chat_id, "resume", arg)
            if cmd == "resume":
                return "Usage: /resume <session_id>\nUse /list to see available sessions."
            if cmd == "summarize":
                return self._summarize_session(msg.chat_id)
            if cmd == "pickup":
                return self._pickup_last_session(msg.chat_id)

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
                # Handle [modelname] syntax from inline keyboard callback
                clean_arg = arg.strip("[]")
                cfg = self._chat_config.setdefault(msg.chat_id, {})
                cfg["model"] = clean_arg
                return (
                    f"✅ Chat model set to: `{clean_arg}`\n\n"
                    f"💡 Set as default:  `/set model.default {clean_arg}`\n"
                    f"   Angel override: `/set adversarial.angel_model {clean_arg}`\n"
                    f"   Devil override: `/set adversarial.devil_model {clean_arg}`"
                )

            if cmd in ("model", "models"):
                cc = self._chat_config.get(msg.chat_id, {})
                current = cc.get("model") or "deepseek-v4-flash"
                # Return role-first model selector
                return (
                    f"[MODEL_ROLE_SELECT]\n"
                    f"**Select Model Role:**\n"
                    f"{current}\n"
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
        try:
            return self._run_baw(text, chat_id=msg.chat_id)
        except Exception as e:
            logger.error(f"[route] BAW error: {e}")
            try:
                from core.guards import bail
                return bail("api_error", status="internal", message=str(e)[:200])
            except Exception:
                return f"❌ BAW error — please try again.\n({str(e)[:100]})"

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

    def _pickup_last_session(self, chat_id: str) -> str:
        """Find the most recent interrupted session and resume it."""
        self._load_session_index()
        if not self._session_index:
            return "📭 No saved sessions found."

        # Get current session id to exclude it
        current_sid = self._sessions.get(chat_id, {}).get("id", "")

        # Sort by updated time descending, pick first that isn't current
        candidates = sorted(
            self._session_index.values(),
            key=lambda s: s["updated"],
            reverse=True,
        )
        target = None
        for s in candidates:
            if s["id"] != current_sid:
                target = s
                break

        if not target:
            return "📭 No other sessions to pick up from."

        # Load full session
        data = self._load_session_from_disk(target["id"])
        if not data:
            return f"❌ Failed to load session `{target['id'][:12]}`."

        # Load messages into current chat session
        self._sessions[chat_id] = {
            "id": data["id"],
            "name": data.get("name", "untitled"),
            "messages": data.get("messages", []),
            "created": data.get("created", 0.0),
            "updated": time.time(),
        }

        # Build context summary from last few messages
        msgs = data.get("messages", [])
        last_msgs = msgs[-4:] if len(msgs) >= 4 else msgs

        context_lines = []
        for m in last_msgs:
            role = "👤" if m["role"] == "user" else "🤖"
            content = m.get("content", "")
            # Trim long content
            if len(content) > 200:
                content = content[:200] + "..."
            context_lines.append(f"{role} {content}")

        context = "\n".join(context_lines)

        import datetime as _dt
        updated_dt = _dt.datetime.fromtimestamp(target["updated"]).strftime("%m-%d %H:%M")

        return (
            f"📂 **Picked Up:** `{data['id'][:12]}` — {data.get('name', 'untitled')}\n"
            f"   ({len(msgs)} msgs, last activity: {updated_dt})\n\n"
            f"**Last context:**\n{context}\n\n"
            f"Continue chatting to pick up where you left off. 🚀"
        )

    # ── In-process BAW engine (lazy-loaded) ──
    _BAW = None  # {'run_agent': fn, 'config': dict, 'data_dir': Path}

    @property
    def _MODELS(self) -> list:
        """Derive model list from config, with fallback."""
        try:
            baw = self._baw_ensure()
            config = baw["config"]
            providers = config.get("providers", {})
            models = []
            for pcfg in providers.values():
                for m in pcfg.get("models", []):
                    models.append(m["id"])
            return models or ["deepseek-v4-flash", "kimi-k2.6", "MiniMax-M2.5"]
        except Exception:
            return ["deepseek-v4-flash", "kimi-k2.6", "MiniMax-M2.5"]

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

        # Load config FIRST — so we can check tool enablement
        data_dir = Path.home() / ".baw"
        config = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))

        # Check which stub tools are enabled
        tools_cfg = config.get("tools", {})
        _stub_enabled = lambda name: tools_cfg.get(name, {}).get("enabled", False)

        # ── Core tools (always registered) ──
        _reg(**_ld('bash'))
        _reg(**_ld('read_file'))
        _reg(**_ld('write_file'))
        _reg(**_ld('web_search'))
        _reg(**_ld('web_extract'))
        _reg(**_ld('search_files'))
        _reg(**_ld('patch'))
        _reg(**_ld('memory'))
        _reg(**_ld('todo'))
        _reg(**_ld('delegate_task'))
        _reg(**_ld('vision'))
        _reg(**_ld('tts'))

        # ── Stub tools (only if enabled in config) ──
        _stub_tools = ['browser', 'execute_code']
        _registered_stubs = []
        for _tn in _stub_tools:
            if _stub_enabled(_tn):
                _reg(**_ld(_tn))
                _registered_stubs.append(_tn)
        if _registered_stubs:
            logger.info(f"[Tools] Stub tools enabled: {', '.join(_registered_stubs)}")

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

        # Read config first to check tool enablement
        data_dir = Path.home() / ".baw"
        try:
            config = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))
        except Exception as e:
            return f"❌ Reload failed: config error: {e}"

        tools_cfg = config.get("tools", {})
        _stub_enabled = lambda name: tools_cfg.get(name, {}).get("enabled", False)

        # Core tools (always registered)
        core_tool_names = ["bash", "read_file", "write_file", "web_search", "web_extract",
                           "search_files", "patch", "memory", "todo", "delegate_task", "vision",
                           "image_generate", "tts"]
        # Stub tools (only if enabled)
        stub_tool_names = ["browser", "execute_code"]
        all_tool_names = core_tool_names + [t for t in stub_tool_names if _stub_enabled(t)]

        errors = []
        for name in all_tool_names:
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
        status = f"✅ Reloaded {len(all_tool_names) - len(errors)}/{len(all_tool_names)} tools"
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

    def _update_with_progress(self, chat_id: str) -> str:
        """Standardized update flow with per-step progress display."""
        import subprocess
        import time as _time
        from pathlib import Path

        repo_dir = Path.home() / "baw"
        total_steps = 6
        step = 0

        def _step(label: str):
            nonlocal step
            step += 1
            self.send(chat_id, f"🔄 **BAW Update** — Step {step}/{total_steps}\n{label}")

        def _done(label: str):
            self.send(chat_id, f"  ✅ Step {step}/{total_steps} — {label}")

        def _warn(label: str):
            self.send(chat_id, f"  ⚠️ Step {step}/{total_steps} — {label}")

        # ── Step 1/6: Fetch ──
        _step("Fetching latest from GitHub...")
        try:
            r = subprocess.run(
                ["git", "fetch", "origin", "--tags"],
                capture_output=True, text=True, timeout=30,
                cwd=str(repo_dir),
            )
            if r.returncode != 0:
                _warn(f"git fetch failed: {r.stderr[:200]}")
                return ""
            _done("Fetched latest tags")
        except Exception as e:
            _warn(f"git fetch error: {e}")
            return ""

        # ── Step 2/6: Version compare ──
        _step("Comparing versions...")
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
                ["git", "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=10,
                cwd=str(repo_dir),
            )
            behind = int(r.stdout.strip() or "0")
        except Exception:
            behind = -1

        if behind == 0:
            _done(f"Already up to date ({current_tag})")
            return ""

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

        _done(f"{current_tag} → {latest_tag} ({behind} commits behind)")

        # ── Step 3/6: Changelog ──
        _step("Reading changelog...")
        changelog_parts = []
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", "HEAD..origin/main", "-30"],
                capture_output=True, text=True, timeout=10,
                cwd=str(repo_dir),
            )
            commits = r.stdout.strip()
            if commits:
                feat, fix, perf, docs, other = [], [], [], [], []
                for line in commits.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    short = line[8:] if len(line) > 8 else line
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
                    changelog_parts.append("**Features:**\n" + "\n".join(feat))
                if fix:
                    changelog_parts.append("**Fixes:**\n" + "\n".join(fix))
                if perf:
                    changelog_parts.append("**Performance:**\n" + "\n".join(perf))
                if docs:
                    changelog_parts.append("**Docs:**\n" + "\n".join(docs))
                if other:
                    changelog_parts.append("**Other:**\n" + "\n".join(other[:5]))
            _done(f"{len(commits.split(chr(10))) if commits else 0} commits grouped")
        except Exception as e:
            _warn(f"Changelog unavailable: {e}")

        # Send changelog as a separate message
        if changelog_parts:
            self.send(chat_id, "\n\n".join(changelog_parts))

        # ── Step 4/6: Pull ──
        _step("Pulling updates...")
        try:
            r = subprocess.run(
                ["git", "pull", "origin", "main"],
                capture_output=True, text=True, timeout=60,
                cwd=str(repo_dir),
            )
            if r.returncode != 0:
                _warn(f"git pull failed: {r.stderr[:200]}")
                return ""
            _done("Pulled successfully")
        except Exception as e:
            _warn(f"git pull error: {e}")
            return ""

        try:
            r = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                capture_output=True, text=True, timeout=5,
                cwd=str(repo_dir),
            )
            new_tag = r.stdout.strip()
            self.send(chat_id, f"🏷️ Now at: **{new_tag}**")
        except Exception:
            pass

        # ── Step 5/6: Post-update checks ──
        _step("Running post-update checks...")
        hooks = []
        req_file = repo_dir / "requirements.txt"
        if req_file.exists():
            try:
                r = subprocess.run(
                    ["git", "diff", f"{current_tag}..HEAD", "--", "requirements.txt"],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(repo_dir),
                )
                if r.stdout.strip():
                    hooks.append("requirements.txt changed — run pip install")
            except Exception:
                pass
        try:
            r = subprocess.run(
                ["git", "diff", f"{current_tag}..HEAD", "--", "config.sample.yaml"],
                capture_output=True, text=True, timeout=10,
                cwd=str(repo_dir),
            )
            if r.stdout.strip():
                hooks.append("config.sample.yaml changed — check new keys")
        except Exception:
            pass

        if hooks:
            for h in hooks:
                _warn(h)
        else:
            _done("No migration needed")

        # ── Step 6/6: Restart ──
        _step("Restarting bot...")
        try:
            subprocess.run(
                ["sudo", "systemctl", "restart", "baw-telegram"],
                capture_output=True, timeout=10,
            )
            _done("Bot restarted — changes live")
        except Exception as e:
            _warn(f"Manual restart needed: sudo systemctl restart baw-telegram")

        return ""  # All output sent via self.send()

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

            # ── In-task model override: [model: X] [stt: X] [tts: X] [img: X] ──
            from ..capabilities import parse_model_overrides, apply_overrides_to_config
            model_overrides: dict[str, str] = {}
            if prompt:
                prompt, model_overrides = parse_model_overrides(prompt)
                if model_overrides:
                    config = apply_overrides_to_config(config, model_overrides)
                    logger.info(
                        f"[Override] Per-task model overrides applied: "
                        + ", ".join(f"{k}→{v}" for k, v in model_overrides.items())
                    )

            # mode from per-chat config > global config > default
            cc = self._chat_config.get(chat_id, {}) if chat_id else {}
            mode = cc.get("mode") or config.get("mode", "quick")

            # ── Session management ──
            session = None
            if chat_id:
                session = self._get_or_create_session(chat_id, first_message=prompt or "")
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
                    # Track context window for display
                    try:
                        from ..loop import set_context_window
                        set_context_window(_model_id, _cw, _usage_pct)
                    except Exception:
                        pass
                    # Lazy summarization: trigger earlier at 50% to keep context lean
                    _next_estimate = _estimated_tokens + (len(prompt) * 0.25) if prompt else 0
                    if _usage_pct > 50 or (_next_estimate / _cw) > 0.60:
                        logger.info(f"[Context] {_usage_pct:.0f}% full — auto-summarizing...")
                        # Generate summary via direct LLM call (bypass run_agent to avoid recursion)
                        _summary = "[Conversation auto-compressed]"
                        try:
                            from ..llm import get_model, call_llm_with_fallback
                            _sum_text = "\n".join(
                                m.get("content", "")[:200]
                                for m in conv_history[:30]
                            )
                            _sum_resp = call_llm_with_fallback(
                                config,
                                [{"role": "user", "content": f"Summarize this conversation in Traditional Chinese, capturing key decisions, facts, and pending actions. Bullet points only.\n\n{_sum_text}"}],
                                temperature=0.3,
                            )
                            _summary = _sum_resp.response.content.strip() or _summary
                        except Exception as e:
                            logger.warning(f"[Context] Summarization via LLM failed: {e}")

                        # Save summary to memory
                        try:
                            from ..memory import MemoryStore
                            _mem = MemoryStore(data_dir)
                            _mem.remember(
                                f"[Session Auto-Summary] {_summary}",
                                tags=["session-summary", "auto"],
                                source="agent",
                            )
                            logger.info(f"[Context] Summary saved to memory")
                        except Exception as e:
                            logger.warning(f"[Context] Memory save failed: {e}")

                        # Compress session: keep last 4 msgs + summary header
                        _keep = 4
                        _compressed = conv_history[-_keep:]
                        _compressed.insert(0, {
                            "role": "user",
                            "content": f"[Session auto-compressed. Earlier conversation summarized to memory.]\n{_summary}",
                        })
                        conv_history = _compressed
                        session["messages"] = _compressed
                        logger.info(f"[Context] Compressed to {len(_compressed)} messages")
                    elif _usage_pct > 50:
                        logger.info(f"[Context] {_usage_pct:.0f}% full — monitoring")
            else:
                conv_history = None

            # ── Intent Shift Detection (keyword heuristic, ~50ms vs ~1.5s LLM) ──
            if conv_history and chat_id:
                _last_user_msgs = [
                    m.get("content", "") for m in conv_history
                    if m.get("role") == "user"
                ]
                if len(_last_user_msgs) >= 2:
                    _prev_topic = _last_user_msgs[-2][:300]
                    # Keyword overlap heuristic
                    _prev_words = set((_prev_topic or "").lower().split())
                    _curr_words = set((prompt or "")[:300].lower().split())
                    if _prev_words and _curr_words:
                        _overlap = len(_prev_words & _curr_words) / max(len(_prev_words | _curr_words), 1)
                        if _overlap < 0.25:  # <25% keyword overlap → topic shift
                            logger.info(
                                f"[Intent] Shift detected (overlap={_overlap:.2f}): "
                                f"'{_prev_topic[:60]}' → '{prompt[:60]}'"
                            )
                            conv_history = None
                            prompt = (
                                f"[Topic shift — previous conversation was about: "
                                f"{_prev_topic[:100]}]\n\n{prompt}"
                            )

            # ── Progress tracking + real-time Telegram updates (inline edit) ──
            _last_progress = time.time()
            _progress_lock = threading.Lock()
            _progress_msg_id = ""  # current message being edited
            _progress_lines: list[str] = []  # accumulated lines for current batch

            def _on_progress(step_type: str = "", name: str = "", args: dict = None):
                nonlocal _progress_msg_id, _progress_lines
                with _progress_lock:
                    nonlocal _last_progress
                    _last_progress = time.time()
                if not chat_id or not step_type:
                    return
                try:
                    if step_type == "plan":
                        # Plan message — edit if exists, else new
                        meta = args or {}
                        total = meta.get("steps", 0)
                        plan_text = f"🗺️ Route plan: {total} step{'s' if total != 1 else ''}"
                        if _progress_msg_id:
                            # Edit existing plan message (dynamic update)
                            self.send(chat_id, plan_text, edit_msg_id=_progress_msg_id)
                        else:
                            _progress_lines = [plan_text]
                            _progress_msg_id = self.send(chat_id, "\n".join(_progress_lines))
                    elif step_type == "tool" and name:
                        _progress_lines.append(f"🔧 `{name[:30]}`")
                        _lines = _progress_lines[-6:]
                        if len(_progress_lines) > 6:
                            _lines.insert(0, "  ...")
                        if _progress_msg_id:
                            self.send(chat_id, "\n".join(_lines),
                                       edit_msg_id=_progress_msg_id)
                        else:
                            _progress_msg_id = self.send(chat_id, "\n".join(_lines))
                    elif step_type == "delegate":
                        meta = args or {}
                        s = meta.get("step", "")
                        t = meta.get("total", "")
                        g = meta.get("goal", "")[:50]
                        _grp = meta.get("group", "A")
                        _gsi = meta.get("step_in_group", s)
                        _ggt = meta.get("group_total", t)
                        if t and int(t) <= 1:
                            _progress_lines.append(f"  ✅ {g}")
                        else:
                            _progress_lines.append(f"  ✅ Step {_grp} {_gsi}/{_ggt}: {g}")
                        # Keep last 6 lines only for inline editing
                        _lines = _progress_lines[-6:]
                        if len(_progress_lines) > 6:
                            _lines.insert(0, "  ...")
                        if _progress_msg_id:
                            self.send(chat_id, "\n".join(_lines),
                                       edit_msg_id=_progress_msg_id)
                        else:
                            _progress_msg_id = self.send(chat_id, "\n".join(_lines))
                    elif step_type == "recalc":
                        nonlocal _recalc_total
                        _recalc_total += 1
                        meta = args or {}
                        if _recalc_total > _MAX_RECALC_THRESHOLD:
                            logger.warning(f"[Loop] {_recalc_total} recalculations — forcing stop")
                            self._cancel_event.set()
                            return
                        _progress_lines.append(f"↻ Recalculating... (step {meta.get('step','?')})")
                        if _progress_msg_id:
                            self.send(chat_id, "\n".join(_progress_lines[-8:]),
                                       edit_msg_id=_progress_msg_id)
                except Exception:
                    pass

            # Run BAW with a timeout via thread pool — multi-round loop
            # If goal not achieved, auto-feed output back as next prompt (max 3 rounds)
            _MAX_AUTO_ROUNDS = 1  # Single round only — no auto-continuation (was 3)
            _MAX_RECALC_THRESHOLD = 2  # Hard cap on recalculations per round (was 5)
            output = ""
            info = {}
            all_plan_recaps = []
            _recalc_total = 0

            # Send typing indicator
            if chat_id:
                self.send_typing(chat_id)

            for _round in range(1, _MAX_AUTO_ROUNDS + 1):
                _current_prompt = prompt if _round == 1 else output  # Feed output back

                # Refresh typing indicator each round
                if chat_id and _round > 1:
                    self.send_typing(chat_id)

                with ThreadPoolExecutor(1) as pool:
                    fut = pool.submit(
                       run_agent,
                       prompt=_current_prompt,
                       config=config,
                       data_dir=data_dir,
                       mode=mode,
                       verbose=False,
                       conversation_history=conv_history if _round == 1 else None,
                       progress_callback=_on_progress if _round == 1 else None,
                    )
                    # Poll for result with cancel checking every 1s
                    import time as _time
                    _stuck_seconds = 0
                    _max_stuck = 600         # 10 min with no progress callbacks → truly stuck
                    _max_total = 1800         # 30 min absolute max
                    _total_elapsed = 0
                    while _total_elapsed < _max_total:
                       try:
                           response, info = fut.result(timeout=1)
                           break
                       except TimeoutError:
                           _total_elapsed += 1
                           with _progress_lock:
                               _progress_since = time.time() - _last_progress
                           # Future is alive — but are we getting progress callbacks?
                           if _progress_since > _max_stuck:
                               _stuck_seconds += 1
                               if _stuck_seconds > 30:
                                   fut.cancel()
                                   return "⏳ Task stuck (no progress for >10min)."
                           else:
                               _stuck_seconds = 0
                           if self._cancel_event.is_set():
                               fut.cancel()
                               return "⏹ Cancelled."
                    else:
                        fut.cancel()
                        return "⏳ Task took too long (>30min)."

                output = response or ""

                # Collect plan recaps
                if info and info.get("plan_recap"):
                    all_plan_recaps.append(info["plan_recap"])

                # ── Auto-continue if goal NOT achieved ──
                goal_achieved = info.get("goal_achieved", True) if info else True
                if goal_achieved:
                    break
                # Goal not achieved — auto-continue to next round
                if _round >= _MAX_AUTO_ROUNDS:
                    output += "\n\n⚠️ Max auto-rounds reached. Goal may be incomplete."
                    break
                if self._cancel_event.is_set():
                    break

                # Feed into next round
                conv_history = None  # Clear context for auto-continuation

            # ── Prepend plan recap to output (single message, no delay) ──
            if all_plan_recaps:
                plan_recap_clean = "\n".join(
                    re.sub(r'<[^>]+>', '', p) for p in all_plan_recaps if p
                )
                if plan_recap_clean:
                    output = plan_recap_clean + "\n" + output

            # ── Append court verdict to output (single message, below plan+result) ──
            if info and info.get("adversarial_raw"):
                cv = info["adversarial_raw"]
                try:
                    devil_content = (cv.get("devil", {}) or {}).get("content", "")[:200]
                    angel_content = (cv.get("angel", {}) or {}).get("content", "")[:200]
                    devil_score = cv.get("devil_score", 0)
                    angel_score = cv.get("angel_score", 0)
                    agreement = cv.get("agreement_level", "unknown")
                    court_msg = (
                        f"\n⚖️ Court: {agreement} (gap {cv.get('score_gap', 0)})\n"
                        f"👿 Devil ({devil_score}/10): {devil_content}\n"
                        f"😇 Angel ({angel_score}/10): {angel_content}"
                    )
                    output += court_msg
                except Exception:
                    pass

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
            # ── Guarantee non-empty output — user must always see a result ──
            if not output.strip():
                output = "✅ Completed. (No additional output — check inline progress above for step details.)"
            return output.strip()

        except Exception as e:
            return f"❌ BAW error: {e}"

    @staticmethod
    def _help_text() -> str:
        return (
            "🤖 **BAW Bot** — Multi-platform Agent Interface\n\n"
            "Simply type anything and BAW will process it.\n\n"
            "**💬 Core:**\n"
            "/help — This message\n"
            "/status — BAW system status + sessions\n"
            "/btw `<text>` — Quick answer (no court, no plan)\n"
            "/fresh `<prompt>` — Raw model — no soul, no memories\n"
            "/court — Show last Angel/Devil verdict\n"
            "/stop — Cancel running request\n"
            "/restart — Restart BAW engine\n\n"
            "**📋 Sessions:**\n"
            "/task new [name] — Save current & start fresh\n"
            "/task list, /list — List saved sessions\n"
            "/task resume <id>, /resume <id> — Resume a saved session\n"
            "/task save [name] — Save/name current session\n"
            "/task forget <id> — Delete a saved session\n"
            "/task info — Show current session details\n"
            "/summarize — LLM summary of current session\n"
            "/pickup — Resume last interrupted session\n\n"
            "**⚙️ Config:**\n"
            "/model — Model selector (or /model `<id>` to switch directly)\n"
            "/mode `quick|hybrid|tight` — Switch execution mode\n"
            "/tone `<profile>` — Switch tone (casual/business/teaching/...)\n"
            "/set `<key>` `<value>` — Persist config to config.yaml\n"
            "/reload — Hot-reload tools & config (no restart)\n"
            "/capability `<cmd>` — Manage capabilities\n\n"
            "**🧠 Memory:**\n"
            "/memory `<text>` — Save a memory\n"
            "/search `<query>` — Search memories\n"
            "/evolve — Self-evolution stats\n\n"
            "**🛠 Tools:**\n"
            "/board — Generate HTML dashboard\n"
            "/version — BAW version\n\n"
            "**🔧 System:**\n"
            "/update — Git pull + changelog + restart\n"
            "/tts on|off|status — Toggle text-to-speech"
        )
