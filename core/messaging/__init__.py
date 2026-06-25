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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("baw.messaging")  # v2-builtin-cmds

# ── Multi-task split pattern ──
_MULTITASK_PATTERN = (
    r'(?:^|\n)\s*(?:'
    r'(?:\#+\s*)?(?:任務|Task)\s+\d+\s*[：:]'
    r'|\d+[\.\)、]\s+'
    r')'
)
# Lookahead version: matches position before a task header without consuming it
_MULTITASK_SPLIT = (
    r'(?:^|\n)(?=\s*(?:(?:\#+\s*)?(?:任務|Task)\s+\d+\s*[：:]|\d+[\.\)、]\s+))'
)



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
        # ── Per-chat sequential processing ──
        self._active_chats: set[str] = set()  # chats with an active task
        self._chat_lock = threading.Lock()
        # ── Message queue ──
        self._message_queue: list[dict] = []  # [{chat_id, user_id, user_name, text, msg, reply_to}]
        self._queue_lock = threading.Lock()
        self._batch_results: list[dict] = []
        self._batch_lock = threading.Lock()
        self._batch_chat_id: str | None = None
        self._restart_requested = False
        self._chat_config = {}  # per-chat overrides: {chat_id: {key: value}}
        self._restart_chat_id: str | None = None
        self._silent_mode = False  # suppress progress updates (for batch execution)
        # ── Session management ──
        self._sessions: dict[str, dict] = {}  # {chat_id: session_dict}
        self._session_lock = threading.Lock()  # protects _sessions dict (accessed from background threads)
        self._sessions_dir = Path.home() / ".baw" / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._load_session_index()
        # ── Plan context ──
        self._plan: Any = None

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the platform. Return True on success."""
        ...

    def _is_chat_busy(self, chat_id: str) -> bool:
        """Check if this chat already has an active task."""
        with self._chat_lock:
            return chat_id in self._active_chats

    def _mark_chat_busy(self, chat_id: str):
        """Mark a chat as having an active task."""
        with self._chat_lock:
            self._active_chats.add(chat_id)

    def _unmark_chat_busy(self, chat_id: str):
        """Unmark a chat when its task completes."""
        with self._chat_lock:
            self._active_chats.discard(chat_id)

    def _acquire_slot(self) -> bool:
        """Try to acquire a processing slot. Returns True if slot available."""
        with self._active_lock:
            if self._active_count < self._max_concurrency:
                self._active_count += 1
                return True
            return False

    def _release_slot(self, chat_id: str = ""):
        """Release a processing slot. If messages are queued, process the next one."""
        if chat_id:
            self._unmark_chat_busy(chat_id)
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
        # Race condition guard: retry a few times before re-enqueueing
        _acquired = False
        for _retry in range(3):
            if self._acquire_slot():
                _acquired = True
                break
            import time as _t3
            _t3.sleep(0.1)

        if _acquired:
            self._cancel_event.clear()
            threading.Thread(
                target=self._dispatch_queued,
                args=(next_msg,),
                daemon=True,
            ).start()
        else:
            # Rare race condition: put back at front of queue
            with self._queue_lock:
                self._message_queue.insert(0, next_msg)
            logger.warning(f"[Queue] Race condition — re-queued message for {next_msg.get('chat_id')}")

    def _dispatch_queued(self, item: dict):
        """Dispatch a queued message to the appropriate handler based on msg_type."""
        msg_type = item.get("msg_type", "text")
        chat_id = item["chat_id"]
        # Per-chat sequential safety: if chat still busy, re-queue and release slot
        if self._is_chat_busy(chat_id):
            self._release_slot()
            with self._queue_lock:
                self._message_queue.append(item)
            self.send(chat_id, f"[QUEUED] Still waiting — another task is running in this chat (re-queued)")
            return
        self._mark_chat_busy(chat_id)
        # Better queue UX: show position and ETA
        queue_pos = len(self._message_queue) + 1
        eta = queue_pos * 8  # rough 8s per task (conservative for mobile)
        self.send(chat_id, f"[QUEUED] Queued (#{queue_pos}, ~{eta}s)...")
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

        # ── Poll thread watchdog: restart if thread dies ──
        _original_thread = self._thread

        def _poll_watchdog():
            while self._running:
                if not _original_thread.is_alive() and self._running:
                    logger.warning(f"[{self._name}] Poll thread died — restarting")
                    new_thread = threading.Thread(target=self._poll_loop, daemon=True)
                    new_thread.start()
                    break  # watchdog exits — new thread gets its own watchdog via stop/start
                time.sleep(30)

        _wd = threading.Thread(target=_poll_watchdog, daemon=True, name=f"poll-wd-{self._name}")
        _wd.start()

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
    _MAX_SESSION_MSGS = 40  # ~20 user/assistant exchanges, keeps input <20K avg

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
        with self._session_lock:
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
        lines = ["[PLAN] <b>Saved Tasks:</b>"]
        for sid, s in sorted(self._session_index.items(),
                             key=lambda x: x[1]["updated"], reverse=True):
            import datetime
            dt = datetime.datetime.fromtimestamp(s["updated"]).strftime("%m-%d %H:%M")
            with self._session_lock:
                is_active = sid in [ses["id"] for ses in self._sessions.values()]
            msg_count = "(active)" if is_active else ""
            lines.append(
                f"  `{sid[:12]}` — <b>{s['name']}</b> "
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
            with self._session_lock:
                for cid, ses in list(self._sessions.items()):
                    if ses["id"] == session_id:
                        del self._sessions[cid]
                        break
            return True
        for f in self._sessions_dir.glob(f"{session_id}*.json"):
            f.unlink()
            with self._session_lock:
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

            if cmd == "focus" and arg:
                return self._handle_focus(arg, chat_id=msg.chat_id)

            if cmd == "fusion" and arg:
                return self._handle_fusion(arg, chat_id=msg.chat_id)

            # ── Fresh start (raw model — no soul, no memories) ──
            if cmd in ("fresh", "fr", "raw"):
                if not arg:
                    return "Usage: /fresh <prompt>\\nRuns a raw model call with no SOUL.md, no memories."
                from ..commands import _cmd_fresh
                baw = self._baw_ensure()
                return _cmd_fresh([arg], baw["config"], baw["data_dir"], verbose=False)

            if cmd in ("court", "ct"):
                # M2 (Fable 5 spec): 4 sub-commands:
                #   /court              → recent 5
                #   /court <id>         → full record
                #   /court live         → toggle per-step push
                #   /court stats        → weekly metrics
                from ..commands import _cmd_court
                return _cmd_court(arg)

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
                return "[BYE] Goodbye!"

            if cmd in ("stop",):
                self._cancel_event.set()
                self._busy = False
                return "[STOP] Stopped."

            if cmd in ("restart",):
                self._restart_requested = True
                self._save_restart_chat_id(msg.chat_id)
                return "Restarting BAW engine..."

            if cmd in ("reload",):
                return self._baw_reload()

            if cmd in ("update", "upgrade", "up"):
                return self._update_with_progress(msg.chat_id)

            if cmd in ("evolve", "ev"):
                try:
                    subcmd = (arg or "").strip()
                    if subcmd == "diff":
                        from ..evolve import get_evolution_history, format_evolution_diff
                        history = get_evolution_history(limit=5)
                        return format_evolution_diff(history)
                    from ..evolve import get_evolve_stats
                    return get_evolve_stats()
                except Exception as e:
                    return f"Evolve error: {e}"

            # ── Doctor / selftest ──
            if cmd in ("doctor", "dr", "health"):
                try:
                    from tools.selftest import selftest as _st
                    from core.health_dashboard import health_check, format_health_report
                    hc = health_check()
                    report = format_health_report(hc)
                    return report
                except Exception as e:
                    return f"[FAIL] Self-test error: {e}"

            # ── Real-World Validator ──
            if cmd in ("validate", "val"):
                try:
                    from core.validator import validate_command
                    return validate_command(arg)
                except Exception as e:
                    return f"[FAIL] Validator error: {e}"

            # ── Tribunal (multi-model consensus) ──
            if cmd == "tribunal":
                try:
                    from core.tribunal import tribunal_command
                    return tribunal_command(arg)
                except Exception as e:
                    return f"[FAIL] Tribunal error: {e}"

            # ── Watchdog / Health ──
            if cmd in ("watchdog", "wd"):
                try:
                    from core.health_dashboard import health_check, format_health_report
                    hc = health_check()
                    return format_health_report(hc)
                except Exception as e:
                    return f"[FAIL] Health check error: {e}"

            # ── Backup ──
            if cmd in ("backup", "bk"):
                try:
                    subcmd = (arg or "").strip()
                    if subcmd == "list" or subcmd == "ls":
                        from core.backup import list_backups
                        bks = list_backups()
                        if not bks:
                            return "[PKG] 暫無備份。\n用 `/backup now` 建立第一個備份。"
                        lines = [f"[PKG] <b>備份列表</b> ({len(bks)} 個)"]
                        for b in bks[:7]:
                            lines.append(f"  • `{b['name']}` — {b['size_mb']}MB ({b['created'][:16]})")
                        return "\n".join(lines)
                    elif subcmd == "restore":
                        from core.backup import restore_backup
                        r = restore_backup("latest")
                        return f"還原: {r['status']}\\n{r.get('detail', '')}\\n檔案數: {r.get('files_restored', 0)}"
                    else:  # default: create
                        from core.backup import create_backup
                        r = create_backup()
                        return f"[OK] 備份完成: `{r['path']}`\n[PKG] {r['size_mb']}MB"
                except Exception as e:
                    return f"[FAIL] Backup error: {e}"

            # ── Monitoring ──
            if cmd in ("monitor", "mon"):
                try:
                    subcmd = (arg or "").strip()
                    if subcmd == "weekly" or subcmd == "report":
                        from core.monitor import generate_weekly_report
                        return generate_weekly_report()
                    else:
                        from core.monitor import get_error_rate, get_health_score_history
                        errors = get_error_rate(hours=24)
                        health = get_health_score_history(days=1)
                        avg = round(sum(h['score'] for h in health) / len(health), 1) if health else 0
                        return (
                            f"[STATS] <b>過去 24 小時</b>\n"
                            f"  錯誤: {errors['total']} ({errors['rate_per_hour']}/hr)\n"
                            f"  健康度: {avg}/10"
                        )
                except Exception as e:
                    return f"[FAIL] Monitor error: {e}"

            # ── Queue status ──
            if cmd in ("queue", "q"):
                with self._queue_lock:
                    q_len = len(self._message_queue)
                with self._active_lock:
                    active_count = self._active_count
                with self._chat_lock:
                    active_chats = list(self._active_chats)
                if q_len == 0 and active_count == 0:
                    return "Queue empty — no pending tasks."
                lines = [f"<b>Message Queue</b>"]
                lines.append(f"  Pending: {q_len} message(s)")
                lines.append(f"  Active slots: {active_count}/{self._max_concurrency}")
                if active_chats:
                    lines.append(f"  Active chats: {', '.join(active_chats[:5])}")
                return "\n".join(lines)

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
                    return f"[OK] `{key}` set to `{value}` (saved to config.yaml)"
                except Exception as e:
                    return f"[FAIL] Failed to set `{key}`: {e}"

            # ── Cron job management ──
            if cmd == "cron":
                try:
                    from core.scheduler import Scheduler, ScheduledTask
                    baw = self._baw_ensure()
                    sched = Scheduler(baw["data_dir"])
                    args = (arg or "").strip().split()

                    if not args or args[0] in ("list", "ls"):
                        return sched.status_report()

                    subcmd = args[0]

                    if subcmd == "add" and len(args) >= 4:
                        # /cron add <name> "<cron>" <command>
                        name, cron_expr = args[1], args[2]
                        command = " ".join(args[3:])
                        sched.add_task(ScheduledTask(
                            name=name, cron=cron_expr,
                            command=command, enabled=True
                        ))
                        nxt = datetime.now(timezone.utc)
                        from croniter import croniter
                        ci = croniter(cron_expr, nxt)
                        nxt_str = ci.get_next(datetime).strftime("%H:%M %Y-%m-%d")
                        return f"[OK] Cron `{name}` added — `{cron_expr}`\n📅 Next: {nxt_str}"

                    if subcmd == "remove" and len(args) >= 2:
                        name = args[1]
                        if sched.remove_task(name):
                            return f"[DEL]️ Removed cron `{name}`"
                        return f"[FAIL] Cron `{name}` not found"

                    if subcmd == "enable" and len(args) >= 2:
                        if sched.toggle_task(args[1], enabled=True):
                            return f"[OK] Cron `{args[1]}` enabled"
                        return f"[FAIL] Cron `{args[1]}` not found"

                    if subcmd == "disable" and len(args) >= 2:
                        if sched.toggle_task(args[1], enabled=False):
                            return f"⏸️ Cron `{args[1]}` disabled"
                        return f"[FAIL] Cron `{args[1]}` not found"

                    return "Usage:\n`/cron` — list all\n`/cron add <name> \"<cron>\" <command>` — add\n`/cron remove <name>` — delete\n`/cron enable|disable <name>` — toggle"
                except Exception as e:
                    return f"[FAIL] Cron error: {e}"

            # ── Top-level session aliases ──
            if cmd == "new":
                return self._handle_task_command(msg.chat_id, "new", arg)
            if cmd == "reset":
                # Hard reset — clear current session without saving
                with self._session_lock:
                    if msg.chat_id in self._sessions:
                        old = self._sessions[msg.chat_id]
                        # Delete saved session file too
                        self._delete_session(old["id"])
                    new_sid = f"ses-{uuid.uuid4().hex[:12]}"
                    self._sessions[msg.chat_id] = {
                        "id": new_sid, "name": "fresh",
                        "messages": [], "created": time.time(), "updated": time.time(),
                    }
                return "Session reset — starting fresh."
            if cmd == "list":
                return self._handle_task_command(msg.chat_id, "list", "")
            if cmd == "resume" and arg:
                return self._handle_task_command(msg.chat_id, "resume", arg)
            if cmd == "resume":
                return "Usage: /resume <session_id>\nUse /list to see available sessions."
            if cmd == "summarize":
                return self._summarize_session(msg.chat_id)
            if cmd == "compact":
                session = self._get_or_create_session(msg.chat_id)
                baw = self._baw_ensure()
                return self._compress_session(baw["config"], baw["data_dir"], session)
            if cmd == "pickup":
                return self._pickup_last_session(msg.chat_id)

            # ── Per-chat config commands ──
            if cmd == "mode" and arg:
                cfg = self._chat_config.setdefault(msg.chat_id, {})
                cfg["mode"] = arg
                return f"[OK] Chat mode set to: {arg}"

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
                return f"[OK] Chat tone set to: {arg}"

            if cmd == "tone":
                cc = self._chat_config.get(msg.chat_id, {})
                tones = ["casual", "business", "teaching", "client-doc", "ot-rt", "stepwise"]
                current = cc.get("tone", self.config.get("tone", {}).get("default", "casual"))
                return f"Current tone: {current}\nAvailable: {', '.join(tones)}"

            # ── Thinking mode toggle ──
            if cmd == "thinking":
                baw = self._baw_ensure()
                cfg = baw["config"]
                if arg:
                    new_val = arg.strip().lower() in ("on", "true", "1", "yes")
                    display = cfg.setdefault("display", {})
                    display["show_reasoning"] = new_val
                    # Persist
                    import yaml
                    (baw["data_dir"] / "config.yaml").write_text(
                        yaml.dump(cfg, default_flow_style=False, allow_unicode=True),
                        encoding="utf-8",
                    )
                    from core.config import invalidate_cache
                    invalidate_cache()
                    state = "on" if new_val else "off"
                    return f"[OK] Thinking mode: {state}\nWhen on, BAW shows its reasoning before each answer."
                cc = self._chat_config.get(msg.chat_id, {})
                show = cfg.get("display", {}).get("show_reasoning", False)
                state = "on" if show else "off"
                return f"Thinking mode: {state}\nUse `/thinking on` or `/thinking off` to toggle."

            if cmd in ("model", "m") and arg:
                # Handle [modelname] syntax from inline keyboard callback
                clean_arg = arg.strip("[]")
                cfg = self._chat_config.setdefault(msg.chat_id, {})
                cfg["model"] = clean_arg
                return (
                    f"[OK] Chat model set to: `{clean_arg}`\n\n"
                    f"💡 Set as default:  `/set model.default {clean_arg}`\n"
                    f"   Angel override: `/set adversarial.angel_model {clean_arg}`\n"
                    f"   Devil override: `/set adversarial.devil_model {clean_arg}`"
                )

            if cmd in ("model", "models"):
                cc = self._chat_config.get(msg.chat_id, {})
                current = cc.get("model") or "deepseek-v4-flash"
                # Show current model and auxiliary model roles
                baw = self._baw_ensure()
                caps = baw.get("config", {}).get("capabilities", {})
                aux_lines = []
                for role, cap in caps.items():
                    if isinstance(cap, dict) and cap.get("model"):
                        aux_lines.append(f"  {role}: <code>{cap['model']}</code>")
                nl = "\n"
                aux_section = nl + "<i>Auxiliary models:</i>" + nl + nl.join(aux_lines) if aux_lines else ""
                return (
                    f"<b>Current model:</b> <code>{current}</code>{nl}"
                    f"<b>Chat override:</b> {cc.get('model', '(none)')}{nl}"
                    f"{aux_section}{nl}{nl}"
                    f"Use <code>/model &lt;name&gt;</code> to switch"
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
            if cmd == "task":
                return "Usage: /task <action> [args]\\nActions: new, list, resume, cancel, delete, status"

        # ── Unified task dispatch (recursive multi-split → chat → agent loop) ──
        try:
            return self._dispatch_task(text, chat_id=msg.chat_id)
        except Exception as e:
            logger.error(f"[route] BAW error: {e}")
            try:
                from core.guards import bail
                return bail("api_error", status="internal", message=str(e)[:200])
            except Exception:
                return f"[FAIL] BAW error — please try again.\n({str(e)[:100]})"

    # ── Unified task dispatch ──────────────────────────────────────────────
    def _dispatch_task(self, text: str, chat_id: str | None, _depth: int = 0) -> str:
        """Route a task: multi-task split → direct shortcuts → chat bypass → agent loop. Recursive."""
        import re as _re

        _task_headers = _re.findall(_MULTITASK_PATTERN, text, _re.MULTILINE)
        if _depth < 1 and len(_task_headers) >= 2:
            return self._execute_multi_task(text, chat_id, _depth)

        # ── Safety check per-task (after split, so multi-task items are isolated) ──
        _sensitive_paths = [
            "/etc/passwd", "/etc/shadow", "/etc/master.passwd",
            "/etc/hosts", "/etc/hostname",
        ]
        _text_lower = text.lower()
        for _sp in _sensitive_paths:
            if _sp in _text_lower:
                return f"[FAIL] Blocked — {_sp} is a sensitive system file and cannot be accessed."

        # ── Direct execution shortcuts ──────────────────────────────────────
        # MUST run BEFORE chat bypass — short commands like "read /tmp/x.txt"
        # would otherwise be mis-classified as chat and never reach tools.
        _direct_result = None
        _lower = text.strip().lower()

        # Memory: save (e.g. "記低我鍾意食拉麵")
        # NOTE: "記得" alone is ambiguous — "你記得嗎？" = recall, not save.
        # Memory: save (e.g. "記低我鍾意食拉麵")
        # Note: "記得" is intentionally excluded — it's ambiguous (could be asking "do you remember?")
        if _direct_result is None and any(_kw in text for _kw in ["記低", "記住"]):
            _content = text
            for _kw in ["記低", "記住"]:
                if _kw in _content:
                    _content = _content.split(_kw, 1)[-1]
            _content = _content.strip("\uff1a:\uff0c, \u3002. \n")
            # ── Pronoun resolution: 佢/他/她 → 實際對象名 ──
            if chat_id and any(p in _content for p in ["佢", "他", "她"]):
                try:
                    session = self._get_or_create_session(chat_id)
                    _recent = " ".join(m.get("content", "") for m in session.get("messages", [])[-10:])
                    # Heuristic: if "個仔"/女 was mentioned, "佢" likely refers to the child
                    if "個仔" in _recent or "個女" in _recent:
                        _import_re = __import__("re")
                        _nm = _import_re.search(r'(?:個仔|個女|仔|女)\s*，?\s*(?:今年|叫)\s*([^，,。.\s]+)', _recent)
                        if not _nm:
                            _nm = _import_re.search(r'叫\s*([^，,。.\s]+)', _recent)
                        if _nm:
                            _name = _nm.group(1)
                            _content = _content.replace("佢", _name).replace("他", _name).replace("她", _name)
                            logger.info(f"[PronounResolve] '佢' → '{_name}' in memory: {_content}")
                except Exception:
                    pass
            if _content and len(_content) > 1:
                try:
                    from tools.memory import memory_remember
                    _direct_result = memory_remember(_content, tags="user")
                except Exception as _e:
                    _direct_result = f"[FAIL] 記憶儲存失敗：{_e}"

        # Memory: search (e.g. "搜尋記憶 拉麵" / "你記得我鍾意食咪嗎？")
        if _direct_result is None and any(_kw in _lower for _kw in ["搜尋記憶", "search memory", "搜尋記憶", "記得", "記唔記得", "你記得"]):
            _query = text
            for _kw in ["搜尋記憶", "search memory", "搜尋記憶", "記得", "記唔記得", "你記得"]:
                if _kw in _query.lower():
                    _query = _query.lower().split(_kw, 1)[-1]
            _query = _query.strip("，, 。. \n？?")
            if not _query:
                _query = text
            try:
                from tools.memory import memory_search
                _raw = memory_search(_query, limit=5)
                if _raw and not _raw.startswith("No memories"):
                    # LLM整理 raw results into natural language (quick, no tools)
                    try:
                        baw = self._baw_ensure()
                        config = baw["config"]
                        from ..llm import get_model, call_llm_with_fallback
                        from ..context import Context
                        model = get_model(config, "MiniMax-M2.5")
                        ctx = Context(
                            system_prompt="你是BAW的記憶整理助手。將記憶搜尋結果整理成自然語言回應，不要直接列出 raw ID。簡潔、自然、廣東話。",
                            temperature=0.5,
                        )
                        ctx.add_user(f"用戶問：{_query}\n\n記憶搜尋結果：\n{_raw}\n\n請整理成自然語言回應，不要顯示 memory ID。")
                        fb = call_llm_with_fallback(config, ctx.to_openai_messages(), temperature=0.5)
                        _direct_result = fb.response.content or _raw
                    except Exception:
                        _direct_result = _raw
                else:
                    _direct_result = "唔好意思，我唔記得你講過呢樣嘅事。可以提示下我嗎？"
            except Exception as _e:
                _direct_result = f"[FAIL] 記憶搜尋失敗：{_e}"

        # File: read (e.g. "讀取 /tmp/test.txt")
        if _direct_result is None and any(_kw in _lower for _kw in ["讀取", "讀檔", "讀下", "看下", "看看", "read file", "cat ", "看內容", "read "]):
            _all_paths = _re.findall(r'((?:/tmp/|/home/|/app/|~/.baw/|/etc/|/var/)[^\s"\'\`\uff0c\u3002]*)', text)
            if _all_paths:
                _read_results = []
                from tools.read_file import _is_sensitive as _rf_sensitive, read_file as _rf
                for _fpath in _all_paths:
                    if not _fpath:
                        continue
                    _blocked, _reason = _rf_sensitive(_fpath)
                    if _blocked:
                        _read_results.append(f"[FILE] {_fpath}\n[FAIL] {_reason}")
                    else:
                        try:
                            _content = _rf(_fpath)
                            # User-friendly error messages: replace technical "Error:" with plain language
                            if _content.startswith("Error: file not found:"):
                                _content = "未找到檔案 (可能尚未建立)"
                            elif _content.startswith("Error: not a file:"):
                                _content = "路徑不是檔案 (可能是目錄)"
                            _read_results.append(f"[FILE] {_fpath}\n{_content}")
                        except Exception as _e:
                            _read_results.append(f"[FILE] {_fpath}\n[FAIL] 讀檔失敗：{_e}")
                if _read_results:
                    _direct_result = "\n\n".join(_read_results)

        # File: write (e.g. "寫入 /tmp/test.txt 內容是 'hello'")
        if _direct_result is None and any(_kw in _lower for _kw in ["寫入", "寫檔", "建立檔案", "write file", "create file", "寫個檔案"]):
            _path_match = _re.search(r'((?:/tmp/|/home/|/app/|~/.baw/|/etc/|/var/)[^\s"\'\`\uff0c\u3002]*)', text)
            if _path_match:
                _fpath = _path_match.group(1)
                _content_match = _re.search(r'["「'']([^"''\u300d]+)["''\u300d]', text)
                if not _content_match:
                    _content_match = _re.search(r'(?:是|為|寫)「?(.{1,500})」?(?:\n|$)', text)
                if _content_match:
                    try:
                        from tools.write_file import write_file as _wf
                        _direct_result = _wf(_fpath, _content_match.group(1))
                    except Exception as _e:
                        _direct_result = f"[FAIL] 寫檔失敗：{_e}"

        # Status: model query (e.g. "你而家用緊邊個 model")
        if _direct_result is None and any(_kw in _lower for _kw in ["用緊邊個 model", "用緊邊個", "用緊 model", "model 狀態", "狀態 model", "用紧边个", "用紧模型"]):
            try:
                baw = self._baw_ensure()
                config = baw["config"]
                _m = config.get("model", {}).get("default", "unknown")
                _chat_m = config.get("capabilities", {}).get("chat", {}).get("model", _m)
                _direct_result = f"當前 model: `{_chat_m}` (chat) / `{_m}` (default)\n\n查更多: `/status`"
            except Exception as _e:
                _direct_result = f"[FAIL] 無法讀取 model 設定：{_e}"

        # Self-test (e.g. "幫我做自我測試" / "/selftest")
        if _direct_result is None and any(_kw in _lower for _kw in ["自我測試", "selftest", "自我檢查", "系統檢查", "健康檢查"]):
            try:
                from tools.selftest import selftest as _st
                _direct_result = _st(full=False)
            except Exception as _e:
                _direct_result = f"[FAIL] Self-test error: {_e}"

        if _direct_result is not None:
            logger.info(f"[DirectShortcut] triggered for: {text[:60]}")
            return _direct_result

        # ── Chat bypass (NARROW WHITELIST only) ──
        # Default = BAW loop with INLINE GUARD. ChatBypass = lightweight LLM with NO tools,
        # so it's ONLY for obvious casual chat that needs zero execution.
        _chat_only_patterns = [
            r"^(hi|hello|hey|yo|good\s+(morning|afternoon|evening)|what'?s\s+up)\b",
            r"^(你好|早晨|晚安|嗨|喂|哈佬|哈囉)\b",
            r"^(多謝|謝謝|thank|thanks|thx|thks|good\b|great\b|awesome\b|正[呀]?|好嘢|好叻|正呀|勁[呀]?|好波)\b",
            r"^(明白|了解|清楚|ok|okay|收到|得[了]?|好[的啦]?|嗯|係[呀的]?)[!.\s]*$",
            r"^[是好]的[!.\s]*$",
            r"^hi$|^hello$|^hey$|^yo$",
            r"^(我覺|我認|我諗|我覺得|我認為).{0,30}$",
        ]
        _is_chat = (
            len(text.strip()) < 80
            and not text.startswith("/")
            and any(_re.match(p, text.strip(), _re.IGNORECASE) for p in _chat_only_patterns)
        )
        if _is_chat:
            try:
                return self._chat_response(text, chat_id=chat_id)
            except Exception:
                pass
        try:
            from ..evolve import track_user_feedback
            track_user_feedback(text, session_id=chat_id or "")
        except Exception:
            pass
        return self._run_baw(text, chat_id=chat_id)

    # ── Chat bypass ──────────────────────────────────────────────
    def _chat_response(self, text: str, chat_id: str | None = None) -> str:
        """Direct lightweight LLM response. No tools, no court, no agent loop.
        Loads session history + queries memory for context."""
        import time as _t
        _t0 = _t.time()
        try:
            baw = self._baw_ensure()
            config = baw["config"]
            from ..llm import get_model, call_llm_with_fallback
            from ..context import Context
            model = get_model(config, "MiniMax-M2.5")

            # ── Load session history + memory ──
            _hist = []
            _mem_text = ""
            if chat_id:
                session = self._get_or_create_session(chat_id)
                _hist = session.get("messages", [])[-self._MAX_SESSION_MSGS:]
                # Query memory for context
                try:
                    from tools.memory import memory_search
                    _mem_result = memory_search(text, limit=3)
                    if _mem_result and not _mem_result.startswith("No memories"):
                        _mem_text = _mem_result
                except Exception:
                    pass

            # ── Load SOUL.md for identity ──
            _soul = ""
            try:
                _soul_path = Path.home() / '.baw' / 'SOUL.md'
                if _soul_path.exists():
                    _soul = _soul_path.read_text(encoding='utf-8')[:2000]
            except Exception:
                pass

            _sys = (
                "你是 BAW（Black And White）— 你的 Agent Platform。\n"
                "你可以執行命令、操作文件、搜索網頁、生成圖像、TTS 等。\n"
                "你不是普通的語言模型 — 你是有行動能力的 agent。\n"
                "直接回應，保持簡潔自然。如果問題簡單就直接答，唔好問「需要我幫你做咩」。\n"
                "重要：你是 BAW 系統的一部分，由 deepseek-v4-flash / MiniMax-M2.5 等 model 驅動。"
                "如果用戶問你是邊個 model，請回答‘我是 BAW 助手，當前用 MiniMax-M2.5 回應’，唔好虛構其他 model 名稱。"
            )
            if _soul:
                _sys += f"\n\n[SOUL.md 精神]\n{_soul}"
            # Build conversation context from recent history for pronoun disambiguation
            _ctx_summary = ""
            if _hist:
                _recent_turns = _hist[-6:]
                _facts = []
                for _m in _recent_turns:
                    _mc = _m.get("content", "")
                    if "個仔" in _mc or "個女" in _mc:
                        _facts.append(_mc)
                if _facts:
                    _ctx_summary = "\n[已知資訊]\n" + "\n".join(f"- {_f[:100]}" for _f in _facts[-3:])
            if _mem_text:
                _sys += f"\n\n[相關記憶]\n{_mem_text[:500]}"
            if _ctx_summary:
                _sys += f"\n{_ctx_summary}"

            ctx = Context(system_prompt=_sys, temperature=0.7)

            # Inject history
            for _m in _hist:
                _role = _m.get("role", "")
                if _role == "user":
                    ctx.add_user(_m.get("content", ""))
                elif _role == "assistant":
                    ctx.add_assistant(_m.get("content", ""))

            ctx.add_user(text)
            fb = call_llm_with_fallback(
                config, ctx.to_openai_messages(),
                temperature=0.7,
            )
            resp = fb.response.content or "..."
            _t1 = _t.time()
            logger.info(f"[ChatBypass] {_t1 - _t0:.1f}s hist={len(_hist)} mem={bool(_mem_text)} — {text[:40]}")

            # Save to session
            if chat_id:
                session = self._get_or_create_session(chat_id)
                session["messages"].append({"role": "user", "content": text})
                session["messages"].append({"role": "assistant", "content": resp})
                if len(session["messages"]) > self._MAX_SESSION_MSGS:
                    session["messages"] = session["messages"][-self._MAX_SESSION_MSGS:]
                session["updated"] = _t.time()
                self._save_session_to_disk(session)

            return resp
        except Exception as e:
            logger.warning(f"[ChatBypass] failed, falling back: {e}")
            raise

    # ── Multi-task execution ─────────────────────────────────────
    def _execute_multi_task(self, text: str, chat_id: str | None = None,
                            _depth: int = 0) -> str:
        """Detect numbered tasks, execute each via _dispatch_task (recursive).
        
        Each sub-task result is sent as its own permanent Telegram message.
        Only a compact summary is returned (edited into the placeholder).
        """
        if _depth >= 5:
            return f"[FAIL] Nesting too deep ({_depth}).\n{self._run_baw(text, chat_id=chat_id)}"
        import re as _re
        import time as _time
        _sections = _re.split(
            _MULTITASK_SPLIT,
            text, flags=_re.MULTILINE
        )
        _tasks_raw = [s.strip() for s in _sections if s.strip() and _re.match(_MULTITASK_PATTERN, s.strip())]
        # ── Filter: reject tasks with empty body (just a number, no content) ──
        _tasks = []
        _skipped_empty = 0
        for _t in _tasks_raw:
            _header_match = _re.match(_MULTITASK_PATTERN, _t)
            if _header_match:
                _body = _t[_header_match.end():].strip()
                if not _body:
                    _skipped_empty += 1
                    continue
            _tasks.append(_t)
        # ── Loop guard: if >50% tasks are empty, abort to break infinite loop ──
        if _skipped_empty > 0 and len(_tasks_raw) > 0 and _skipped_empty / len(_tasks_raw) > 0.5:
            return f"[ABORT] {_skipped_empty}/{len(_tasks_raw)} tasks had empty bodies — possible upstream loop. Skipped all empty tasks, {len(_tasks)} remaining."
        _total = len(_tasks)
        if _total == 0:
            return f"[INFO] All {len(_tasks_raw)} task(s) had empty bodies — nothing to execute."
        _prev = self._silent_mode
        self._silent_mode = True  # suppress noisy step-by-step progress per sub-task
        # ── Build task context: inject original full list so sub-tasks know their siblings ──
        _task_list_summary = "\n".join(
            f"  {_re.match(_MULTITASK_PATTERN, t).group(0).strip() if _re.match(_MULTITASK_PATTERN, t) else t[:80]}"
            for t in _tasks
        )
        _pass = 0
        _fail = 0
        _suspicious = []
        try:
            for _i, _task in enumerate(_tasks, 1):
                _task_short = _task.strip()[:60]
                logger.info(f"[MultiTask] [{_i}/{_total}] depth={_depth}")
                # ── Send per-task header message (permanent, visible) ──
                _header_msg_id = ""
                if chat_id:
                    _header_msg_id = self.send(chat_id, f"🔧 <b>Task {_i}/{_total}</b> — {_task_short}")
                # Inject sibling task context so each sub-task knows the full picture
                _task_with_context = (
                    f"[MULTI-TASK {_i}/{_total}]\n\n"
                    f"Full task list:\n{_task_list_summary}\n\n"
                    f"Your task (do ONLY this one):\n{_task}"
                )
                _is_fail = False
                try:
                    _resp = self._dispatch_task(_task_with_context, chat_id, _depth + 1)
                except Exception as _e:
                    _resp = f"[FAIL] Error — {_e}"
                # ── Analyse result ──
                _body = _resp
                _body_lower = _body.lower()
                _has_success = any(s in _body for s in [
                    "[OK]", "[DONE]", "[PASS]", "completed",
                    "[CLEAN]", "empty", "removed",
                ])
                _has_explicit_fail = (
                    "[FAIL]" in _resp or "was not executed" in _body_lower
                    or "was not created" in _body_lower
                )
                _has_error_kw = (
                    "error" in _body_lower
                    or "fail" in _body_lower
                    or "syntax error" in _body_lower
                    or "syntaxerror" in _body_lower or "synatx" in _body_lower
                    or "無法" in _body
                    or "cannot access" in _body_lower
                    or "not found" in _body_lower
                    or "not created" in _body_lower
                    or "冇建立" in _body
                    or "沒有建立" in _body
                    or "係 shell command 唔係 tool" in _body
                    or "不是 memory tool" in _body
                )
                _is_fail = _has_explicit_fail or (_has_error_kw and not _has_success)
                if _is_fail:
                    _fail += 1
                    _suspicious.append(f"Task {_i}: {_task_short}")
                else:
                    _pass += 1
                # ── Send result as follow-up message ──
                _result_preview = _resp.strip()[:800]
                _status = "❌" if _is_fail else "✅"
                _result_msg = f"{_status} <b>Task {_i}/{_total}</b>\n{_result_preview}"
                if chat_id:
                    self.send(chat_id, _result_msg)
                # Small delay between tasks
                if _i < _total:
                    _time.sleep(0.5)
        finally:
            self._silent_mode = _prev
        # ── Compact summary (Telegram-optimized) ──
        _summary = (
            f"<b>📊 Summary</b>  |  Total: {_total}  |  ✅ Pass: {_pass}  |  ❌ Fail: {_fail}"
        )
        if _suspicious:
            _summary += f"\n⚠️ _Suspicious results:_ {', '.join(_suspicious)}"
        return _summary

    # ── Session / Task command handler ───────────────────────────
    def _handle_task_command(self, chat_id: str, action: str, arg: str) -> str:
        if action == "new":
            # Save current session, start fresh
            self._save_session_to_disk(self._get_or_create_session(chat_id))
            with self._session_lock:
                new_sid = f"ses-{uuid.uuid4().hex[:12]}"
                name = arg or "untitled"
                self._sessions[chat_id] = {
                    "id": new_sid, "name": name,
                    "messages": [], "created": time.time(), "updated": time.time(),
                }
                self._save_session_to_disk(self._sessions[chat_id])
            return f"[OK] New task started: <b>{name}</b> (`{new_sid[:12]}`)"

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
            with self._session_lock:
                self._sessions[chat_id] = {
                    "id": data["id"], "name": data.get("name", "untitled"),
                    "messages": data.get("messages", []),
                    "created": data.get("created", 0.0), "updated": time.time(),
                }
                msg_count = len(self._sessions[chat_id]["messages"])
            return (
                f"📂 Resumed task: <b>{data.get('name', 'untitled')}</b> "
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
            return f"[SAVE] Task saved: <b>{ses['name']}</b> (`{ses['id'][:12]}`)"

        elif action in ("forget", "delete", "rm"):
            sid = arg or ""
            if not sid:
                return "Usage: /task forget <session_id>"
            if self._delete_session(sid):
                return f"[DEL]️ Task `{sid[:12]}` deleted."
            return f"Task `{sid[:12]}` not found."

        elif action in ("info", "show"):
            ses = self._get_or_create_session(chat_id)
            return (
                f"[TODO] <b>Current Task</b>\n"
                f"  ID: `{ses['id'][:12]}`\n"
                f"  Name: <b>{ses['name']}</b>\n"
                f"  Messages: {len(ses['messages'])}\n"
                f"  Created: {time.strftime('%m-%d %H:%M', time.localtime(ses['created']))}"
            )

        else:
            return (
                "<b>Task commands:</b>\n"
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
            return f"[PLAN] <b>Session Summary</b> (`{ses['id'][:12]}`)\n\n{response}"
        except Exception as e:
            return f"[FAIL] Summarization failed: {e}"

    def _compress_session(self, config: dict, data_dir: Path, session: dict) -> str:
        """Compress session: use context.py compact summary instead of LLM summarization.
        Keeps last 4 messages + summary header. Saves summary to memory."""
        conv_history = session.get("messages", [])
        if not conv_history:
            return "📭 No messages to compress."

        # Use heuristic compaction from context.py — free, no LLM call
        _summary = "[Conversation auto-compressed]"
        try:
            from ..context import Context, Message
            # Build a temporary context from session messages
            _tmp_ctx = Context(system_prompt="")
            for m in conv_history:
                _role = m.get("role", "")
                _content = m.get("content", "") or ""
                if _role == "user":
                    _tmp_ctx.add_user(_content)
                elif _role == "assistant":
                    _tmp_ctx.add_assistant(_content, m.get("tool_calls"))
                elif _role == "tool":
                    _tmp_ctx.add_tool_result(m.get("tool_call_id", ""), m.get("name", ""), _content)
            _compacted, _, _compact_text = _tmp_ctx.compact(threshold_chars=30000, keep_recent_turns=3)
            if _compacted > 0 and _compact_text:
                _summary = _compact_text[:500]
        except Exception as e:
            logger.warning(f"[Context] Heuristic compaction failed (will use default summary): {e}")

        # Save summary to memory
        try:
            from ..memory import MemoryStore
            _mem = MemoryStore(data_dir)
            _mem.remember(
                f"[Session Summary] {_summary}",
                tags=["session-summary", "manual" if _summary != "[Conversation auto-compressed]" else "auto"],
                source="agent",
            )
        except Exception as e:
            logger.warning(f"[Context] Memory save failed: {e}")

        # Compress session: keep last 6 msgs + summary header
        _keep = 6
        _compressed = conv_history[-_keep:]
        _compressed.insert(0, {
            "role": "user",
            "content": f"[Session compressed. Earlier conversation summarized.]\n{_summary}",
        })
        session["messages"] = _compressed
        logger.info(f"[Context] Compressed from {len(conv_history)} → {len(_compressed)} messages")
        self._save_session_to_disk(session=session)
        return f"✅ Compressed: {len(conv_history)} → {len(_compressed)} messages\n📝 Summary:\n{_summary}"

    def _pickup_last_session(self, chat_id: str) -> str:
        """Find the most recent interrupted session and resume it."""
        self._load_session_index()
        if not self._session_index:
            return "📭 No saved sessions found."

        # Get current session id to exclude it
        with self._session_lock:
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
            return f"[FAIL] Failed to load session `{target['id'][:12]}`."

        # Load messages into current chat session
        with self._session_lock:
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
            role = "👤" if m["role"] == "user" else "[BOT]"
            content = m.get("content", "")
            # Trim long content
            if len(content) > 200:
                content = content[:200] + "..."
            context_lines.append(f"{role} {content}")

        context = "\n".join(context_lines)

        import datetime as _dt
        updated_dt = _dt.datetime.fromtimestamp(target["updated"]).strftime("%m-%d %H:%M")

        return (
            f"📂 <b>Picked Up:</b> `{data['id'][:12]}` — {data.get('name', 'untitled')}\n"
            f"   ({len(msgs)} msgs, last activity: {updated_dt})\n\n"
            f"<b>Last context:</b>\n{context}\n\n"
            f"Continue chatting to pick up where you left off. [GO]"
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

        # Load config via load_config (applies managed layer)
        from core.config import load_config as _load_baw_config
        config = _load_baw_config(reload=True)
        data_dir = Path.home() / ".baw"

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

        # ── Additional core tools ──
        _reg(**_ld('http_fetch'))
        _reg(**_ld('install'))
        _reg(**_ld('get_skill'))
        _reg(**_ld('remember'))
        _reg(**_ld('knowledge_graph'))
        _reg(**_ld('mcp'))
        _reg(**_ld('background'))
        _reg(**_ld('mmx'))
        _reg(**_ld('code_scan'))

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

        # Read config via load_config (applies managed layer)
        data_dir = Path.home() / ".baw"
        try:
            from core.config import load_config as _load_baw_config
            config = _load_baw_config(reload=True)
        except Exception as e:
            return f"[FAIL] Reload failed: config error: {e}"

        tools_cfg = config.get("tools", {})
        _stub_enabled = lambda name: tools_cfg.get(name, {}).get("enabled", False)

        # Core tools (always registered)
        core_tool_names = ["bash", "read_file", "write_file", "web_search", "web_extract",
                           "search_files", "patch", "memory", "todo", "delegate_task", "vision",
                           "image_generate", "tts",
                           "http_fetch", "install", "get_skill", "remember",
                           "knowledge_graph", "mcp", "background", "mmx",
                           "code_scan"]
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

        # Re-read config via load_config (applies managed layer)
        data_dir = Path.home() / ".baw"
        try:
            from core.config import load_config as _load_baw_config
            config = _load_baw_config(reload=True)
        except Exception as e:
            return f"[FAIL] Reload failed: config error: {e}"

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
            return f"[FAIL] Reload failed: loop reload error: {e}"

        self._BAW = {"run_agent": run_agent, "config": config, "data_dir": data_dir}
        status = f"[OK] Reloaded {len(all_tool_names) - len(errors)}/{len(all_tool_names)} tools"
        if errors:
            status += f" | [WARN] {len(errors)} errors: {'; '.join(errors[:3])}"
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

        return f"[OK] Config updated: {key} → {value}"

    _TOOL_ICONS = {
        "bash": "🔎",
        "read_file": "📖",
        "write_file": "✏️",
        "web_search": "🌐",
        "web_extract": "[FILE]",
        "patch": "[FIX]",
        "search_files": "[SCAN]",
        "terminal": "💻",
        "delegate_task": "[BOT]",
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
            self.send(chat_id, f"BAW Update — Step {step}/{total_steps}\n{label}")

        def _done(label: str):
            self.send(chat_id, f"  [OK] Step {step}/{total_steps} — {label}")

        def _warn(label: str):
            self.send(chat_id, f"  [WARN] Step {step}/{total_steps} — {label}")

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
                        docs.append(f"  [NOTE] {short[5:].strip()}")
                    else:
                        other.append(f"  • {short.strip()}")

                if feat:
                    changelog_parts.append("<b>Features:</b>\n" + "\n".join(feat))
                if fix:
                    changelog_parts.append("<b>Fixes:</b>\n" + "\n".join(fix))
                if perf:
                    changelog_parts.append("<b>Performance:</b>\n" + "\n".join(perf))
                if docs:
                    changelog_parts.append("<b>Docs:</b>\n" + "\n".join(docs))
                if other:
                    changelog_parts.append("<b>Other:</b>\n" + "\n".join(other[:5]))
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
            self.send(chat_id, f"🏷️ Now at: <b>{new_tag}</b>")
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
            return "[STOP] Previous request was cancelled."

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
            mode = cc.get("mode") or config.get("mode", "auto")
            # ── Focus Mode: force full execution (tight), relentless approach ──
            _is_focus_mode = prompt.startswith("[FOCUS MODE")
            if _is_focus_mode:
                mode = "tight"
            # ── Fusion Mode: parallel multi-provider research + synthesis ──
            _is_fusion_mode = prompt.startswith("[FUSION MODE")
            if _is_fusion_mode:
                mode = "tight"

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
                            if _m.get("id") == _model_id:
                                _cw = _m.get("context_window", 65536)
                                break

                    _usage_pct = (_estimated_tokens / _cw) * 100
                    # Track context window for display
                    try:
                        from ..loop import set_context_window
                        set_context_window(_model_id, _cw, _usage_pct)
                    except Exception:
                        pass
                    # Auto-compression: hard cap at 30K estimated tokens or 30% of context
                    _next_estimate = _estimated_tokens + (len(prompt) * 0.25) if prompt else 0
                    if (_estimated_tokens > 30000 or _usage_pct > 30 or (_next_estimate / _cw) > 0.40) and session:
                        logger.info(f"[Context] {_estimated_tokens} tokens ({_usage_pct:.0f}%) — auto-compressing...")
                        self._compress_session(config, data_dir, session)
                        conv_history = session.get("messages", [])
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

            # ── Plan detection ──
            if chat_id and not self._plan:
                from core.plan import Plan
                plan_signals = Plan.detect_plan([prompt] if prompt else [])
                if plan_signals:
                    self._plan = Plan.create(
                        name=plan_signals.get("name", "Untitled Plan"),
                        artifacts=plan_signals.get("artifacts", []),
                    )
                    if session:
                        session["plan_id"] = self._plan.plan_id
                        self._plan.add_session(session["id"])
                    logger.info(f"[Plan] Auto-detected: {self._plan.name} ({self._plan.plan_id})")

            # ── Soften intent-shift when plan is active ──
            if self._plan and conv_history is None and prompt:
                # Plan is active but context was reset — re-inject plan context
                prompt = f"[Plan: {self._plan.name} — continuing plan context]\n\n{prompt}"
                logger.info(f"[Plan] Plan active, context softened for: {self._plan.name}")

            # ── Track plan artifacts from batch/file prompts ──
            if self._plan and prompt and ("[File:" in prompt or "<b>File" in prompt or "<b>[Batch" in prompt):
                import re as _plan_re
                files = _plan_re.findall(r'<b>File \d+:</b>\s+([^<\n]+)', prompt)
                for fname in files:
                    self._plan.add_artifact(name=fname.strip(), type="document", path="")
                logger.info(f"[Plan] Tracked {len(files)} artifacts for plan {self._plan.plan_id}")

            # ── Progress tracking + real-time Telegram updates (inline edit) ──
            _last_progress = time.time()
            _progress_lock = threading.Lock()
            _progress_msg_id = ""  # current message being edited
            _progress_lines: list[str] = []  # accumulated lines for current batch

            def _on_progress(step_type: str = "", name: str = "", args: dict = None):
                nonlocal _progress_msg_id, _progress_lines, _last_progress
                if self._silent_mode:
                    with _progress_lock:
                        _last_progress = time.time()
                    return
                with _progress_lock:
                    _last_progress = time.time()
                if not chat_id or not step_type:
                    return
                try:
                    # ── Language-aware labels ──
                    _lang = self.config.get("display", {}).get("language", "zh")
                    _TOOL_LABELS = {
                        "zh": {
                            "_analysis": "🧠 分析中...",
                            "_recalc": "🔄 重新計算中...",
                            "read_file": "📖 讀取檔案", "write_file": "📝 寫入檔案",
                            "patch": "🔧 修改檔案", "bash": "💻 執行指令",
                            "web_search": "🔍 搜尋網絡", "web_extract": "🌐 提取網頁",
                            "terminal": "💻 執行指令", "execute_code": "⚙️ 執行程式碼",
                            "delegate_task": "👥 分配子任務", "memory": "🧠 存取記憶",
                            "session_search": "📚 搜尋對話記錄", "search_files": "🔎 搜尋檔案",
                            "cronjob": "⏰ 設定排程", "config": "⚙️ 修改設定",
                            "todo": "📋 更新待辦", "image_generate": "🎨 生成圖片",
                            "text_to_speech": "🔊 生成語音", "vision_analyze": "👁️ 分析圖片",
                            "http_fetch": "📥 下載檔案", "install": "📦 安裝套件",
                            "knowledge_graph": "🕸️ 查詢知識圖譜",
                            "skill_manage": "🛠️ 管理技能", "skill_view": "📄 查閱技能",
                            "tts": "🔊 生成語音",
                            "browser_navigate": "🌍 瀏覽網頁", "browser_click": "🖱️ 點擊頁面",
                            "browser_type": "⌨️ 輸入文字", "browser_scroll": "📜 滾動頁面",
                            "browser_snapshot": "📷 讀取頁面", "browser_vision": "📸 視覺分析",
                            "browser_get_images": "🖼️ 擷取圖片",
                            "browser_press": "⌨️ 按鍵操作", "browser_back": "🔙 返回頁面",
                            "browser_console": "📟 讀取主控台",
                        },
                        "en": {
                            "_analysis": "🧠 Analyzing...",
                            "_recalc": "🔄 Recalculating...",
                            "read_file": "📖 Reading file", "write_file": "📝 Writing file",
                            "patch": "🔧 Patching file", "bash": "💻 Running command",
                            "web_search": "🔍 Searching web", "web_extract": "🌐 Extracting page",
                            "terminal": "💻 Running command", "execute_code": "⚙️ Executing code",
                            "delegate_task": "👥 Delegating task", "memory": "🧠 Accessing memory",
                            "session_search": "📚 Searching history", "search_files": "🔎 Searching files",
                            "cronjob": "⏰ Setting schedule", "config": "⚙️ Updating config",
                            "todo": "📋 Updating tasks", "image_generate": "🎨 Generating image",
                            "text_to_speech": "🔊 Generating speech",
                            "vision_analyze": "👁️ Analyzing image",
                            "http_fetch": "📥 Downloading file", "install": "📦 Installing package",
                            "knowledge_graph": "🕸️ Querying knowledge graph",
                            "skill_manage": "🛠️ Managing skills", "skill_view": "📄 Viewing skill",
                            "tts": "🔊 Generating speech",
                            "browser_navigate": "🌍 Browsing page", "browser_click": "🖱️ Clicking element",
                            "browser_type": "⌨️ Typing text", "browser_scroll": "📜 Scrolling page",
                            "browser_snapshot": "📷 Reading page", "browser_vision": "📸 Visual analysis",
                            "browser_get_images": "🖼️ Capturing images",
                            "browser_press": "⌨️ Pressing key", "browser_back": "🔙 Going back",
                            "browser_console": "📟 Reading console",
                        },
                    }
                    _labels = _TOOL_LABELS.get(_lang, _TOOL_LABELS["zh"])

                    if step_type == "plan":
                        meta = args or {}
                        total = meta.get("steps", 0)
                        plan_text = f"{_labels.get('_analysis', '🧠 分析中...')} ({total} {'步' if _lang == 'zh' else 'steps'})"
                        if _progress_msg_id:
                            self.send(chat_id, plan_text, edit_msg_id=_progress_msg_id)
                        else:
                            _progress_lines = [plan_text]
                            _progress_msg_id = self.send(chat_id, plan_text)
                    elif step_type == "tool" and name:
                        # Progress message: emoji prefix + label + context
                        _emoji = _labels.get(name, f"🔧 {name}")
                        # Include key context from args for clarity
                        _context = ""
                        if name == "read_file" and args.get("path"):
                            _context = f" `{args['path'].split('/')[-1]}`"
                        elif name == "write_file" and args.get("path"):
                            _context = f" `{args['path'].split('/')[-1]}`"
                        elif name == "patch" and args.get("path"):
                            _context = f" `{args['path'].split('/')[-1]}`"
                        elif name == "bash" and args.get("command"):
                            _cmd = args["command"][:50]
                            _context = f" `{_cmd}`"
                        elif name in ("web_search", "web_extract") and args.get("query"):
                            _context = f" `{args['query'][:40]}`"
                        elif name == "execute_code" and args.get("code"):
                            _cnt = len(args.get('code', ''))
                            _context = f" ({_cnt} 行)" if _lang == "zh" else f" ({_cnt} lines)"
                        elif name == "cronjob" and args.get("schedule"):
                            _context = f" `{args['schedule']}`"
                        elif name == "terminal" and args.get("command"):
                            _cmd = args["command"][:50]
                            _context = f" `{_cmd}`"
                        elif name == "memory" and args.get("action"):
                            _context = f" ({args['action']})"
                        # ── Sub-agent permanent status message ──
                        if name == "delegate_task" and chat_id:
                            _sa_goal = (args or {}).get("goal", "")[:150]
                            _sa_model = (args or {}).get("model_id", "") or ""
                            _sa_info = f"🔄 <b>子任務</b>"
                            if _sa_model:
                                _sa_info += f" · `{_sa_model}`"
                            _sa_info += f"\n{_sa_goal}"
                            _sa_id = self.send(chat_id, _sa_info)
                            if _sa_id:
                                _subagent_msgs.append(_sa_id)
                        _status = f"{_emoji}{_context}"
                        if _progress_msg_id:
                            self.send(chat_id, _status, edit_msg_id=_progress_msg_id)
                        else:
                            _progress_msg_id = self.send(chat_id, _status)
                    elif step_type == "delegate":
                        meta = args or {}
                        s = meta.get("step", "")
                        t = meta.get("total", "")
                        g = meta.get("goal", "")[:80]
                        _step_label = "📋 步驟" if _lang == "zh" else "📋 Step"
                        _status = f"{_step_label} {s}/{t}"
                        if g:
                            _status += f" · {g}"
                        if _progress_msg_id:
                            self.send(chat_id, _status, edit_msg_id=_progress_msg_id)
                        else:
                            _progress_msg_id = self.send(chat_id, _status)
                    elif step_type == "recalc":
                        nonlocal _recalc_total
                        _recalc_total += 1
                        if _recalc_total > _MAX_RECALC_THRESHOLD:
                            logger.warning(f"[Loop] {_recalc_total} recalculations — forcing stop")
                            self._cancel_event.set()
                            return
                        _status = _labels.get("_recalc", "🔄 重新計算中...")
                        if _progress_msg_id:
                            self.send(chat_id, _status, edit_msg_id=_progress_msg_id)
                        else:
                            _progress_msg_id = self.send(chat_id, _status)
                except Exception:
                    pass

            # Run BAW with a multi-round, multi-strategy pursuit loop.
            # Each round = full agent run (plan → execute → self-review).
            # If goal not achieved, next round gets full failure context and MUST try different approach.
            # After all rounds exhausted, runs diagnosis → user-facing actionable report.
            _MAX_AUTO_ROUNDS = 5
            _MAX_RECALC_THRESHOLD = 5
            _MAX_TOTAL_SECONDS = 600
            # ── Focus Mode: relentless execution — bump limits ──
            if _is_focus_mode:
                _MAX_AUTO_ROUNDS = 10
                _MAX_TOTAL_SECONDS = 1800
                _focus_max_tool_turns = 100
                logger.info(f"[_run_baw] Focus Mode — rounds={_MAX_AUTO_ROUNDS}, timeout={_MAX_TOTAL_SECONDS}s, tool_turns={_focus_max_tool_turns}")
            elif _is_fusion_mode:
                _MAX_AUTO_ROUNDS = 8
                _MAX_TOTAL_SECONDS = 1200
                _focus_max_tool_turns = 80
                logger.info(f"[_run_baw] Fusion Mode — rounds={_MAX_AUTO_ROUNDS}, tool_turns={_focus_max_tool_turns}")
            else:
                _focus_max_tool_turns = 0  # will be set by complexity below
            # ── Auto-detect development task (無須 [FOCUS MODE] prefix) ──
            _dev_keywords = ["create", "write.*file", "implement", "build", "開發",
                             "寫.*碼", "做.*project", "整.*app", "寫.*function",
                             "建.*系統", "寫.*script", "開發.*tool", "develop",
                             "git.*commit", "create.*repo", "寫.*test", "write.*test"]
            if not _is_focus_mode and not _is_fusion_mode:
                _is_dev_task = False
                _prompt_lower = (prompt or "").lower()
                for _kw in _dev_keywords:
                    if re.search(_kw, _prompt_lower):
                        _is_dev_task = True
                        break
                if _is_dev_task:
                    _MAX_AUTO_ROUNDS = 8
                    _MAX_TOTAL_SECONDS = 1200
                    _focus_max_tool_turns = 100
                    logger.info(f"[_run_baw] Dev task detected — rounds={_MAX_AUTO_ROUNDS}, tool_turns={_focus_max_tool_turns}")
            # ── Token Killer: adaptive tool cap based on task complexity ──
            from ..token_killer import estimate_task_complexity
            _task_complexity = estimate_task_complexity(prompt) if not _is_focus_mode else "complex"
            if _task_complexity == "simple":
                _MAX_AUTO_ROUNDS = 2
                _MAX_TOTAL_SECONDS = 180
                _focus_max_tool_turns = 25  # 10 was too low — BAW hits cap before reaching synthesis
                logger.info(f"[_run_baw] Simple task — rounds={_MAX_AUTO_ROUNDS}, tool_turns={_focus_max_tool_turns}")
            elif _task_complexity == "moderate":
                _MAX_AUTO_ROUNDS = 3
                _MAX_TOTAL_SECONDS = 360
                _focus_max_tool_turns = 50
                logger.info(f"[_run_baw] Moderate task — rounds={_MAX_AUTO_ROUNDS}, tool_turns={_focus_max_tool_turns}")
            elif _task_complexity == "complex":
                _MAX_AUTO_ROUNDS = 5
                if not _focus_max_tool_turns:
                    _focus_max_tool_turns = 100  # complex tasks need substantial tool budget per round
                logger.info(f"[_run_baw] Complex task — tool_turns={_focus_max_tool_turns}")
                # ── Background task notification: tell user upfront for long tasks ──
                if chat_id and not _is_focus_mode:
                    try:
                        _bg_msg = (
                            "⏳ <b>Detected complex task</b> — running in background.\n"
                            "Estimated: multiple rounds, multiple tools.\n"
                            "I will send the full result when ready.\n"
                            "You can send new messages — they will queue."
                        )
                        self.send(chat_id, _bg_msg)
                    except Exception:
                        pass
            output = ""
            info = {}
            all_plan_recaps = []
            all_failure_reasons = []
            all_checkpoint_results = []
            all_uncertain_claims = []
            _recalc_total = 0
            _subagent_msgs: list[str] = []  # track sub-agent status message IDs for permanent display

            # Send typing indicator
            if chat_id:
                self.send_typing(chat_id)

            _loop_start = time.time()  # track execution time for summary
            for _round in range(1, _MAX_AUTO_ROUNDS + 1):
                # Build round-specific prompt with retry context
                if _round == 1:
                    _current_prompt = prompt
                else:
                    # Wrap output with explicit retry directive + failure context
                    _failure_summary = "\n".join(
                        f"  • {r[:200]}" for r in all_failure_reasons[-3:]
                    ) if all_failure_reasons else "Reason unclear"
                    _checkpoint_summary = ""
                    if all_checkpoint_results and _round > 1:
                        _checkpoint_parts = []
                        for _cr in all_checkpoint_results[-3:]:
                            _cr_stripped = _cr.strip()
                            # Skip uncertainty/failure markers — only inject real results
                            if _cr_stripped and not _cr_stripped.startswith("[VERIFICATION"):
                                _checkpoint_parts.append(f"  • {_cr_stripped[:200]}")
                        if _checkpoint_parts:
                            _checkpoint_summary = (
                                f"\n\nCheckpoint — previous round's completed work (DO NOT redo):\n"
                                + "\n".join(_checkpoint_parts[-3:])
                            )
                    _current_prompt = (
                        f"[AUTO-RETRY ROUND {_round}/{_MAX_AUTO_ROUNDS}]\n\n"
                        f"Original goal: {prompt}\n\n"
                        f"Previous round failed. Here's what went wrong:\n{_failure_summary}"
                        f"{_checkpoint_summary}\n\n"
                        f"CRITICAL: You MUST try a COMPLETELY DIFFERENT approach this round.\n"
                        f"- Different provider (e.g. Stepfun → MiniMax → edge-tts)\n"
                        f"- Different method (e.g. curl → Python SDK → subprocess)\n"
                        f"- Different tool (e.g. pip → apt → container)\n"
                        f"- Auto-install missing packages before attempting\n"
                        f"- Research root cause from error message and fix it\n\n"
                        f"Do NOT repeat the same approach that just failed.\n"
                        + (f"FOCUS MODE: NO human questions. NO stopping. Try ANYTHING.\n" if _is_focus_mode else "")
                        + (f"FUSION MODE: Research from multiple angles. Compare sources. Synthesize balanced answer.\n" if _is_fusion_mode else "")
                        + f"Execute the full plan silently. Report only the final result."
                    )

                # Refresh typing indicator each round
                if chat_id and _round > 1:
                    self.send_typing(chat_id)

                pool = ThreadPoolExecutor(1)
                try:
                    fut = pool.submit(
                       run_agent,
                       prompt=_current_prompt,
                       config=config,
                       data_dir=data_dir,
                       mode=mode,
                       verbose=False,
                       conversation_history=conv_history if _round == 1 else None,
                       progress_callback=_on_progress if _round == 1 else None,
                       max_tool_turns=_focus_max_tool_turns if _focus_max_tool_turns else 50,
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
                                   return "[QUEUED] Task stuck (no progress for >10min)."
                           else:
                               _stuck_seconds = 0
                           if self._cancel_event.is_set():
                               fut.cancel()
                               return "[STOP] Cancelled."
                    else:
                        fut.cancel()
                        return "[QUEUED] Task took too long (>30min)."
                finally:
                    pool.shutdown(wait=False)  # don't block on stuck run_agent

                output = response or ""

                # ── Post-LLM Plan Detection: parse <!--plan:Name--> marker ──
                if output and "<!--plan:" in output:
                    _plan_marker = re.search(r'<!--plan:\s*(.+?)\s*-->', output)
                    if _plan_marker:
                        _plan_name = _plan_marker.group(1).strip()
                        # Strip marker from output (invisible to user)
                        output = re.sub(r'<!--plan:\s*.+?\s*-->', '', output).strip()
                        if _plan_name:
                            from core.plan import Plan as _PlanCls
                            # Create plan if none active, or link to existing
                            if not self._plan:
                                self._plan = _PlanCls.create(name=_plan_name)
                                if session:
                                    session["plan_id"] = self._plan.plan_id
                                    self._plan.add_session(session["id"])
                                logger.info(f"[Plan] Auto-detected via LLM: {_plan_name} ({self._plan.plan_id})")
                            else:
                                logger.info(f"[Plan] Marker seen but plan already active: {self._plan.name}")

                # Collect plan recaps
                if info and info.get("plan_recap"):
                    all_plan_recaps.append(info["plan_recap"])

                # Collect failure reasons for diagnosis
                if info and info.get("failure_reasons"):
                    for _fr in info["failure_reasons"]:
                        if _fr not in all_failure_reasons:
                            all_failure_reasons.append(_fr)

                # Collect checkpoint results + uncertainty flags (checkpoint recovery)
                if info and info.get("successful_results"):
                    for _sr in info["successful_results"]:
                        _sr_key = _sr[:100]
                        if _sr_key not in [r[:100] for r in all_checkpoint_results]:
                            all_checkpoint_results.append(_sr)
                if info and info.get("uncertain_claims"):
                    for _uc in info["uncertain_claims"]:
                        if _uc not in all_uncertain_claims:
                            all_uncertain_claims.append(_uc)

                # ── Auto-continue if goal NOT achieved ──
                goal_achieved = info.get("goal_achieved", True) if info else True
                # Tool cap hit → task was truncated, treat as not achieved
                if info and info.get("tool_cap_hit"):
                    goal_achieved = False
                    if not any("tool cap" in r for r in all_failure_reasons):
                        all_failure_reasons.append(f"Hit tool cap ({info.get('max_tool_turns', '?')} turns) — task truncated")
                if goal_achieved:
                    break
                if self._cancel_event.is_set():
                    break

                # Last round: attach diagnosis before giving up
                if _round >= _MAX_AUTO_ROUNDS:
                    # Run diagnosis: analyse all failures and suggest actionable solutions
                    _diag_prompt = (
                        f"[DIAGNOSIS] Task failed after {_round} round(s).\n\n"
                        f"Original goal: {prompt}\n\n"
                        f"Failure reasons collected across all rounds:\n"
                        + ("\n".join(f"  • {r[:300]}" for r in all_failure_reasons) if all_failure_reasons else "  (No structured failure data)")
                        + ("\n\nUncertain claims flagged mid-stream:\n" + "\n".join(f"  • {u[:300]}" for u in all_uncertain_claims) if all_uncertain_claims else "")
                        + "\n\n"
                        f"Analyse the FAILURE PATTERNS above. Produce a DIAGNOSIS with:\n"
                        f"1. What was tried: summarise the {_round} different approaches briefly\n"
                        f"2. Root cause: what actually failed (specific API error, missing package, quota, etc.)\n"
                        f"3. Actionable fix: what you can DO right now to unblock this\n"
                        f"   (e.g. 'pip install edge-tts', 'top up MiniMax credits', 'switch to Step Plan endpoint', 'use curl instead of Python SDK')\n"
                        f"4. Alternative path: what BAW could try next if the fix is applied\n"
                        f"\nFormat:\n"
                        f"[PLAN] Diagnosis ({_round} rounds)\n"
                        f"• Tried: ...\n"
                        f"• Root cause: ...\n"
                        f"• [FIX] Fix: ...\n"
                        f"• Next: ...\n"
                        f"\nDo NOT apologise. Do NOT ask questions. Be specific and actionable."
                    )
                    try:
                        from ..llm import get_model as _get_diag_model, call_llm_with_fallback as _diag_llm
                        from ..context import Context as _DiagCtx
                        _diag_cfg = config
                        _diag_model = _get_diag_model(_diag_cfg, config.get("model", {}).get("default", "deepseek-v4-flash"))
                        _diag_ctx = _DiagCtx(system_prompt="You are BAW's failure diagnosis agent. Be specific and actionable, not apologetic.", temperature=0.3)
                        _diag_ctx.add_user(_diag_prompt)
                        _diag_fb = _diag_llm(_diag_cfg, _diag_ctx.to_openai_messages(), temperature=0.3)
                        _diagnosis = _diag_fb.response.content or ""
                        if _diagnosis.strip():
                            output += f"\n\n{_diagnosis.strip()}"
                    except Exception as _diag_e:
                        # Fallback: list failure reasons directly
                        output += (
                            f"\n\n[WARN] Tried {_round} approaches without reaching goal.\n"
                            + ("\n".join(f"  • {r[:200]}" for r in all_failure_reasons) if all_failure_reasons else "")
                            + "\n\nTo unblock: check API keys, quotas, and installed packages. "
                            "Or try `pip install edge-tts` if audio generation failed."
                        )
                    break

                # Feed into next round with clear context
                conv_history = None  # Clear context for auto-continuation

            # ── Update sub-agent status messages with results ──
            if chat_id and _subagent_msgs:
                import re as _sa_re
                _sa_boxes = _sa_re.findall(
                    r'╔═══ 巳分工.*?╚═════════════════════════════╝',
                    output, _sa_re.DOTALL
                )
                # ── Always iterate over ALL sub-agent messages, not just matched boxes ──
                for _i, _sa_id in enumerate(_subagent_msgs):
                    if _i < len(_sa_boxes):
                        _box = _sa_boxes[_i]
                        _lines = _box.split('\n')
                        # Extract footer: Iterations + model info
                        _footer = ""
                        for _l in _lines:
                            if 'Iterations:' in _l:
                                _footer = _l.strip().lstrip('│').strip()
                                break
                        # Extract result summary (skip header + footer lines)
                        _result_parts = []
                        _in_body = False
                        for _l in _lines:
                            _stripped = _l.strip()
                            if '├───' in _stripped and not _in_body:
                                _in_body = True
                                continue
                            if '├───' in _stripped and _in_body:
                                break
                            if _in_body and _stripped.startswith('│'):
                                _content = _stripped[1:].strip()
                                if _content and 'Goal:' not in _content:
                                    _result_parts.append(_content)
                        _result_text = '\n'.join(_result_parts[:5])[:300]
                        if _result_text:
                            _update = "✅ <b>子任務完成</b>"
                            if _footer:
                                _update += f" · {_footer}"
                            _update += f"\n{_result_text}"
                            self.send(chat_id, _update, edit_msg_id=_sa_id)
                        elif _footer:
                            self.send(chat_id, f"✅ <b>子任務完成</b> · {_footer}", edit_msg_id=_sa_id)
                        else:
                            self.send(chat_id, "✅ <b>子任務完成</b>", edit_msg_id=_sa_id)
                    else:
                        # ── Fallback: no box pattern matched → still mark as completed ──
                        self.send(chat_id, "✅ <b>子任務完成</b>", edit_msg_id=_sa_id)

            # ── Append failure report if any (proactive, not waiting for next request) ──
            if all_failure_reasons:
                failure_text = "\n".join(f"  • {r[:200]}" for r in all_failure_reasons)
                output = (
                    f"[FAIL] Task had {len(all_failure_reasons)} failure(s):\n"
                    f"{failure_text}\n\n"
                    f"{output}"
                )

            # ── Append simplified court verdict (single concise line) ──
            # Only append if output has actual task content (not just empty-output fallback)
            if info and info.get("adversarial_raw"):
                cv = info["adversarial_raw"]
                try:
                    agreement = cv.get("agreement_level", "unknown")
                    gap = cv.get("score_gap", 0)
                    _devil_score = cv.get("devil_score", 0)
                    _angel_score = cv.get("angel_score", 0)
                    # Skip court verdict if the only output is the empty-fallback message
                    _has_real_content = bool(output.strip()) and \
                        "No additional output" not in output and \
                        "Completed. (No" not in output and \
                        "Task failed to reach goal" not in output
                    if _has_real_content:
                        _gap_label = f"gap={gap}" if gap else "unanimous" if agreement == "court-v2" else ""
                        _score_label = f"{_devil_score}/{_angel_score}"
                        output += f"\n\n⚖️ 法庭 {_score_label} | {agreement}"
                        if _gap_label:
                            output += f" | {_gap_label}"
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

            # ── Unified output validation ──
            # All post-processing (HTML strip, blank line compression,
            # credential redaction, anti-duplication, hallucination guard,
            # length enforcement) is now centralised in output_validator.
            # importlib.reload prevents Python module caching from silently
            # running stale code when container has mounted source volumes.
            import importlib
            from .. import output_validator as _ov
            importlib.reload(_ov)
            output = _ov.validate_output(output, prompt=prompt)

            # ── Context-aware empty output fallback ──
            if not output.strip():
                if all_failure_reasons:
                    lines = ["[FAIL] Task failed:"]
                    for r in all_failure_reasons:
                        lines.append(f"  • {r[:200]}")
                    output = "\n".join(lines)
                elif info and info.get("goal_achieved") is False:
                    output = "❗ 任務未能完成目標，需要跟進。"
                else:
                    output = "✅ 任務已完成。（無額外輸出）"

            # ── Post-validation result guarantee: catch intention-only output ──
            # Even after loop.py's synthesis guard, output may still be planning
            # text ("Let me search more") rather than actual results.
            # This is the LAST code-level defence before delivery.
            _final_out = output.strip()
            if _final_out and len(_final_out) < 800:
                _intent_pats = [
                    r"^(Let me\s|I will\s|I need to\s|I'm going to\s|I am going to\s)",
                    r"^(Now|Next),?\s*(let|I|we)\s",
                    r"^I have (enough|the|all|sufficient).*?(to\s|that)",
                    r"(compile|synthesize|write|prepare|gather|collect).*(review|answer|response|report)",
                    r"(let me search|let me look|let me check|let me try)",
                    r"(search for|look for|find more|gather more|collect more)",
                ]
                _intent_hits = sum(1 for p in _intent_pats if re.search(p, _final_out, re.IGNORECASE))
                if _intent_hits >= 2:
                    logger.warning(f"[_run_baw] Post-validation intention-only output ({len(_final_out)} chars, {_intent_hits} signals) — forcing final fallback")
                    if all_failure_reasons:
                        lines = ["[FAIL] Task resulted in planning text instead of actual results:"]
                        for r in all_failure_reasons:
                            lines.append(f"  • {r[:200]}")
                        output = "\n".join(lines)
                    else:
                        output = (
                            "❗ 任務執行完成但沒有輸出實際結果。\n\n"
                            "BAW 搜尋咗資料但最終只出咗 planning 文字，未有合成最終答案。\n"
                            "你可以：\n"
                            "• 再 send 一次，BAW 會以全新 context 嘗試\n"
                            "• 使用 `/mode tight` 強制 full execution\n"
                            "• 用 `/focus 你的目標` 強制 Focus Mode"
                        )

            # ── Auto-deliver files: detect file paths in output, send as MEDIA ──
            _pending_media = []  # collect MEDIA paths here so trim doesn't lose them
            if chat_id:
                import re as _file_re
                from pathlib import Path as _FileP
                # Find all absolute paths with known extensions
                _file_paths = _file_re.findall(
                    r'/[^\s\n<>"\'`，。（）\(\)）]+\.(?:html?|md|txt|json|png|jpg|svg|pdf|yaml|yml|py|mp3|wav|ogg)',
                    output
                )
                for _fp in _file_paths:
                    _fp = _fp.strip().rstrip('.,;:）)）】」』》、。(')
                    _fp_obj = _FileP(_fp)
                    if _fp_obj.is_file() and f"MEDIA:{_fp}" not in output:
                        # File exists, agent mentioned it but didn't use MEDIA: → auto-send
                        _pending_media.append(_fp)
                # Trim long output FIRST (before MEDIA tags) so tags survive
                if len(output) > 1500 and _pending_media:
                    _summary_parts = []
                    for _mp in _pending_media:
                        _mp_obj = _FileP(_mp)
                        _summary_parts.append(f"`{_mp_obj.name}` ({_mp_obj.stat().st_size:,} bytes)")
                    _first_line = output.split('\n')[0][:200]
                    output = f"{_first_line}\n\n[FILE] {' · '.join(_summary_parts)}"
                # Append MEDIA tags AFTER trim — guaranteed to survive
                for _mp in _pending_media:
                    if f"MEDIA:{_mp}" not in output:
                        output += f"\nMEDIA:{_mp}"

            # ── Clear progress message after BAW completes ──
            # Don't repeat the first line of output — that's what the message above is.
            # Show a compact execution summary instead.
            if chat_id and _progress_msg_id:
                try:
                    _elapsed = int(time.time() - _loop_start)
                    _summary_parts = []
                    if _round > 1:
                        _summary_parts.append(f"R{_round}")
                    if _recalc_total > 0:
                        _summary_parts.append(f"{_recalc_total} recalcs")
                    if _elapsed > 5:
                        _summary_parts.append(f"{_elapsed}s")
                    if _summary_parts:
                        _clear_text = f"✅ Done · {' · '.join(_summary_parts)}"
                    else:
                        _clear_text = "✅ Done"
                    self.send(chat_id, _clear_text, edit_msg_id=_progress_msg_id)
                except Exception:
                    pass

            # Clean synthesis: regenerate with identity + user question
            from pathlib import Path as _CSPath
            try:
                _cs = _CSPath("/home/radxa/.baw/SOUL.md").read_text()
                _cm = [{"role": "system", "content": _cs}, {"role": "user", "content": prompt or ""}]
                from ..llm import call_llm_with_fallback as _csllm
                _cf = _csllm(config, _cm, tools=None, temperature=0.7)
                if _cf and _cf.response and _cf.response.content:
                    output = _cf.response.content.strip()
                else:
                    output = "出咗少少技術問題，試多次？"
            except Exception:
                output = "出咗少少技術問題，試多次？"
            if output and len(output) > 5:
                try:
                    with open("/tmp/baw_learning.txt", "a") as _f:
                        _f.write("Q: " + str(prompt[:80]) + "\nA: " + str(output[:120]) + "\n\n")
                except Exception:
                    pass
            return output.strip()

        except BaseException as e:
            return f"[FAIL] BAW error: {e}"

    # ── Focus Mode: Model Council + Relentless Execution ─────────────────

    def _run_model_council(self, goal: str) -> str:
        """Query ALL available providers in parallel for strategic advice.

        Each provider's first chat-capable model is queried with the goal.
        Returns a formatted council report.
        """
        import os as _os
        import json as _json
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from urllib.request import Request, urlopen
        from urllib.error import URLError, HTTPError

        baw = self._baw_ensure()
        config = baw["config"]
        providers_cfg = config.get("providers", {})

        # Build list of (provider_name, model_id, base_url, api_key)
        council_members = []
        for pname, pcfg in providers_cfg.items():
            api_key_env = pcfg.get("api_key_env", "")
            api_key = _os.environ.get(api_key_env, "") if api_key_env else ""
            if not api_key:
                # Try .env file
                try:
                    env_path = Path.home() / ".baw" / ".env"
                    if env_path.exists():
                        for _line in env_path.read_text().split("\n"):
                            _line = _line.strip()
                            if _line.startswith(f"{api_key_env}=") or _line.startswith(f"{api_key_env} ="):
                                api_key = _line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                except Exception:
                    pass
            if not api_key:
                continue

            base_url = (pcfg.get("base_url", "") or "").rstrip("/")
            if not base_url:
                continue

            # Pick first chat-capable model, or first model
            models = pcfg.get("models", [])
            chosen_model = ""
            for m in models:
                caps = m.get("capabilities", "")
                if isinstance(caps, str) and "chat" in caps:
                    chosen_model = m.get("id", "")
                    break
            if not chosen_model and models:
                chosen_model = models[0].get("id", "")
            if not chosen_model:
                continue

            council_members.append((pname, chosen_model, base_url, api_key))

        if not council_members:
            return "[COUNCIL] No providers with API keys found."

        # Council prompt: strategy-focused, concise
        council_prompt = (
            f"你係一個策略專家。以下係目標：\n\n{goal}\n\n"
            "請用 3-5 bullet point 講出你嘅建議方向。\n"
            "Focus on：第一步做咩、潛在風險、關鍵決策。\n"
            "簡潔，每個 bullet 一句話。廣東話。"
        )

        def _query_one(pname, model, base_url, api_key):
            t0 = __import__('time').time()
            try:
                url = f"{base_url}/chat/completions"
                payload = _json.dumps({
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你係一個策略顧問。直接畀建議，唔好打招呼。"},
                        {"role": "user", "content": council_prompt},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 500,
                }).encode()
                req = Request(url, data=payload,
                              headers={"Authorization": f"Bearer {api_key}",
                                       "Content-Type": "application/json"})
                with urlopen(req, timeout=45) as resp:
                    data = _json.loads(resp.read())
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    latency = (__import__('time').time() - t0) * 1000
                    return (pname, model, content.strip() or "(empty)", latency)
            except Exception as e:
                latency = (__import__('time').time() - t0) * 1000
                return (pname, model, f"ERROR: {str(e)[:100]}", latency)

        results = []
        with ThreadPoolExecutor(max_workers=len(council_members)) as pool:
            futures = {
                pool.submit(_query_one, pname, model, base_url, api_key): (pname, model)
                for pname, model, base_url, api_key in council_members
            }
            for fut in as_completed(futures):
                try:
                    results.append(fut.result(timeout=50))
                except Exception as e:
                    pname, model = futures[fut]
                    results.append((pname, model, f"TIMEOUT: {e}", 50000))

        # Format council report
        report_lines = [
            "╔═══════════════════════════╗",
            "║  🏛️  MODEL COUNCIL       ║",
            "╠═══════════════════════════╣",
        ]
        success_count = 0
        for pname, model, content, latency in results:
            is_error = content.startswith("ERROR:") or content.startswith("TIMEOUT:")
            tag = "⚠️" if is_error else "✅"
            report_lines.append(f"║ {tag} <b>{pname}</b> (`{model}`) · {latency:.0f}ms")
            if not is_error:
                success_count += 1
                # Truncate content for display
                for line in content.split("\n")[:6]:
                    line = line.strip()
                    if line:
                        report_lines.append(f"║   {line[:90]}")
        report_lines.append("╚═══════════════════════════╝")

        council_report = "\n".join(report_lines)

        # Full council text for synthesis
        council_full = []
        for pname, model, content, latency in results:
            if not content.startswith("ERROR:") and not content.startswith("TIMEOUT:"):
                council_full.append(f"## {pname} ({model})\n{content}")
        full_text = "\n\n".join(council_full)

        return f"{council_report}\n\n[COUNCIL] {success_count}/{len(results)} models responded."

    def _handle_focus(self, goal: str, chat_id: str | None = None) -> str:
        """Focus Mode: Model Council → Synthesis → Relentless Execution.

        1. Query ALL available providers for strategic advice (parallel)
        2. Synthesize the best approach
        3. Execute relentlessly until goal achieved — no human questions
        """
        # ── Phase 1: Model Council ──
        self.send(chat_id, "🏛️ <b>Model Council</b> · 集結中...")
        council_result = self._run_model_council(goal)
        self.send(chat_id, council_result)

        # ── Phase 2 & 3: Synthesis + Relentless Execution ──
        focus_prompt = (
            f"[FOCUS MODE — RELENTLESS EXECUTION]\n\n"
            f"GOAL: {goal}\n\n"
            f"MODEL COUNCIL ADVICE:\n{council_result}\n\n"
            f"RULES:\n"
            f"- NO human questions. NO clarifications. NO confirmations.\n"
            f"- You have ALL tools. Use them aggressively.\n"
            f"- If one approach fails, try another immediately.\n"
            f"- Auto-install missing packages. Auto-fix config.\n"
            f"- Try ALL available providers before giving up.\n"
            f"- Multi-round auto-retry until DONE.\n"
            f"- Report progress but NEVER stop to ask.\n"
            f"- Execute until the goal is VERIFIABLY achieved.\n"
            f"- 8 rounds max. After round 5, try completely different approaches.\n"
            f"- DO NOT add 總結/summary section.\n"
        )

        return self._run_baw(focus_prompt, chat_id=chat_id)

    def _handle_fusion(self, query: str, chat_id: str | None = None) -> str:
        """Fusion Mode: parallel multi-provider research + synthesis.

        1. Spawn parallel web research tasks to multiple providers
        2. Each provider analyzes the query independently
        3. Synthesize the best answer from all perspectives
        """
        self.send(chat_id, "🧬 <b>Fusion Mode</b> · Parallel multi-provider research...")

        fusion_prompt = (
            f"[FUSION MODE — PARALLEL MULTI-PROVIDER RESEARCH]\n\n"
            f"QUERY: {query}\n\n"
            f"RULES:\n"
            f"- Research the query using MULTIPLE approaches in parallel\n"
            f"- Web search: search from different angles (Chinese + English sources)\n"
            f"- Compare and contrast findings from different sources\n"
            f"- Identify disagreements or inconsistencies between sources\n"
            f"- Synthesize a balanced final answer\n"
            f"- Output MUST contain actual findings, not just 'let me search' planning\n"
            f"- NO human questions. NO stopping.\n"
        )

        return self._run_baw(fusion_prompt, chat_id=chat_id)

    @staticmethod
    def _help_text() -> str:
        return (
            "[BOT] <b>BAW Bot</b> — Multi-platform Agent Interface\n\n"
            "Simply type anything and BAW will process it.\n\n"
            "<b>💬 Core:</b>\n"
            "/help — This message\n"
            "/status — BAW system status + sessions\n"
            "/btw `<text>` — Quick answer (no court, no plan)\n"
            "/focus `<goal>` — Model Council + relentless execution\n"
            "/fusion `<query>` — Parallel multi-provider research + synthesis\n"
            "/fresh `<prompt>` — Raw model — no soul, no memories\n"
            "/court — 最近 5 單案件 (id+verdict+score+elapsed)\n"
            "/court `<id>` — 查全卷 (起訴/答辯/證物/判決)\n"
            "/court stats — 本週 metrics (核准率/平均 latency/tier 分流)\n"
            "/court live — 訂閱逐步推送 (M3 wire-in)\n"
            "/stop — Cancel running request\n"
            "/restart — Restart BAW engine\n\n"
            "<b>[PLAN] Sessions:</b>\n"
            "/task new [name] — Save current & start fresh\n"
            "/task list, /list — List saved sessions\n"
            "/task resume <id>, /resume <id> — Resume a saved session\n"
            "/task save [name] — Save/name current session\n"
            "/task forget <id> — Delete a saved session\n"
            "/task info — Show current session details\n"
            "/summarize — LLM summary of current session\n"
            "/pickup — Resume last interrupted session\n\n"
            "<b>⚙️ Config:</b>\n"
            "/model — Model selector (or /model `<id>` to switch directly)\n"
            "/models — Show all auxiliary models (STT, TTS, vision, etc.)\n"
            "/mode `quick|hybrid|tight` — Switch execution mode\n"
            "/thinking `on|off` — Toggle reasoning display (default: off)\n"
            "/tone `<profile>` — Switch tone (casual/business/teaching/...)\n"
            "/set `<key>` `<value>` — Persist config to config.yaml\n"
            "/reload — Hot-reload tools & config (no restart)\n"
            "/capability `<cmd>` — Manage capabilities\n\n"
            "<b>[TEST] Validate (REAL tests):</b>\n"
            "/validate — Run all real-world validations\n"
            "/validate api — DeepSeek + MiniMax live API calls\n"
            "/validate evolve — Evolve logging (real write + read)\n"
            "/validate memory — Memory read/write\n"
            "/validate telegram — Bot connectivity\n"
            "/validate disk — Disk space check\n"
            "/validate git — Git status\n\n"
            "<b>🏥 Health & Ops:</b>\n"
            "/doctor, /dr — 10-point system health check\n"
            "/watchdog, /wd — Same as /doctor\n"
            "/backup, /bk — Create backup (or /backup list, /backup restore)\n"
            "/monitor, /mon — 24h error rate (or /monitor weekly)\n\n"
            "<b>🏛️ Tribunal (multi-model consensus):</b>\n"
            "/tribunal <question> — Ask multiple judges, get unified verdict\n"
            "/tribunal bench — Show current judge configuration\n"
            "(Customise judges in ~/.baw/config.yaml tribunal section)\n\n"
            "<b>[MODEL] Memory:</b>\n"
            "/memory `<text>` — Save a memory\n"
            "/search `<query>` — Search memories\n"
            "/evolve — Self-evolution stats\n\n"
            "<b>🛠 Tools:</b>\n"
            "/board — Generate HTML dashboard\n"
            "/version — BAW version\n"
            "/cron — List/manage scheduled tasks\n\n"
            "<b>[FIX] System:</b>\n"
            "/update — Git pull + changelog + restart\n"
            "/tts on|off|status — Toggle text-to-speech"
        )
