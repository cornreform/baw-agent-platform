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
        self._sessions_dir = Path.home() / ".baw" / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._load_session_index()

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
        lines = ["[PLAN] **Saved Tasks:**"]
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
                return "[>] Restarting BAW engine..."

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
            if cmd in ("validate", "val", "v"):
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
                        lines = [f"[PKG] **備份列表** ({len(bks)} 個)"]
                        for b in bks[:7]:
                            lines.append(f"  • `{b['name']}` — {b['size_mb']}MB ({b['created'][:16]})")
                        return "\n".join(lines)
                    elif subcmd == "restore":
                        from core.backup import restore_backup
                        r = restore_backup("latest")
                        return f"[>] 還原: {r['status']}\n{r.get('detail', '')}\n檔案數: {r.get('files_restored', 0)}"
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
                            f"[STATS] **過去 24 小時**\n"
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
                lines = [f"**Message Queue**"]
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
                if msg.chat_id in self._sessions:
                    old = self._sessions[msg.chat_id]
                    # Delete saved session file too
                    self._delete_session(old["id"])
                new_sid = f"ses-{uuid.uuid4().hex[:12]}"
                self._sessions[msg.chat_id] = {
                    "id": new_sid, "name": "fresh",
                    "messages": [], "created": time.time(), "updated": time.time(),
                }
                return "[>] Session reset — starting fresh."
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
        if len(_task_headers) >= 2:
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
                _direct_result = f"⚡ 當前 model: `{_chat_m}` (chat) / `{_m}` (default)\n\n查更多: `/status`"
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
        """Detect numbered tasks, execute each via _dispatch_task (recursive)."""
        if _depth >= 5:
            return f"[FAIL] Nesting too deep ({_depth}).\n{self._run_baw(text, chat_id=chat_id)}"
        import re as _re
        _sections = _re.split(
            _MULTITASK_SPLIT,
            text, flags=_re.MULTILINE
        )
        _tasks = [s.strip() for s in _sections if s.strip() and _re.match(_MULTITASK_PATTERN, s.strip())]
        _results = []
        _total = len(_tasks)
        _prev = self._silent_mode
        self._silent_mode = True
        try:
            for _i, _task in enumerate(_tasks, 1):
                logger.info(f"[MultiTask] [{_i}/{_total}] depth={_depth}")
                try:
                    _resp = self._dispatch_task(_task, chat_id, _depth + 1)
                    _results.append(f"## Task {_i}/{_total}: {_resp[:500]}")
                except Exception as _e:
                    _results.append(f"## Task {_i}/{_total}: [FAIL] Error — {_e}")
                # Small delay between tasks to prevent API rate-limit and give UI breathing room
                if _i < _total:
                    import time as _t2
                    _t2.sleep(0.5)
        finally:
            self._silent_mode = _prev
        _pass = sum(1 for r in _results if "[FAIL] Error" not in r)
        _fail = _total - _pass
        # ── Truth-check: don't call it "Pass" if the reply contains failure
        #    indicators beyond just "[FAIL] Error". The LLM often reports
        #    "Done — 1/1 (100%)" after a real failure (syntax error, hallucination).
        _real_pass = 0
        _real_fail = 0
        _suspicious = []
        for _r in _results:
            _body = _r.split(":", 1)[1] if ":" in _r else _r
            _body_lower = _body.lower()
            # Genuine success: tool output shows it worked
            _is_fail = (
                "[FAIL]" in _r
                or "error" in _body_lower
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
                or "was not executed" in _body_lower
                or "was not created" in _body_lower
            )
            if _is_fail:
                _real_fail += 1
                _suspicious.append(_r.split("\n")[0][:60])
            else:
                _real_pass += 1
        _summary = (
            f"\n\n---\n## Summary\n"
            f"- Total: {_total}  |  Pass: {_real_pass}  |  Fail: {_real_fail}"
        )
        if _suspicious:
            _summary += f"\n- [WARN] Possibly fabricated tasks: {len(_suspicious)}"
            for _s in _suspicious:
                _summary += f"\n  - `{_s}`"
        _summary += f"\n- Nesting depth: {_depth}\n"
        return "\n\n".join(_results) + _summary

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
            return f"[OK] New task started: **{name}** (`{new_sid[:12]}`)"

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
            return f"[SAVE] Task saved: **{ses['name']}** (`{ses['id'][:12]}`)"

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
                f"[TODO] **Current Task**\n"
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
            return f"[PLAN] **Session Summary** (`{ses['id'][:12]}`)\n\n{response}"
        except Exception as e:
            return f"[FAIL] Summarization failed: {e}"

    def _compress_session(self, config: dict, data_dir: Path, session: dict) -> str:
        """Compress session: summarize early part, keep last 4 messages + summary header.
        Returns summary text. Can be called on-demand (/compact) or auto-triggered."""
        conv_history = session.get("messages", [])
        if not conv_history:
            return "📭 No messages to compress."

        _summary = "[Conversation auto-compressed]"
        try:
            from ..llm import call_llm_with_fallback
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
                f"[Session Summary] {_summary}",
                tags=["session-summary", "manual" if _summary != "[Conversation auto-compressed]" else "auto"],
                source="agent",
            )
        except Exception as e:
            logger.warning(f"[Context] Memory save failed: {e}")

        # Compress session: keep last 4 msgs + summary header
        _keep = 4
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
            f"📂 **Picked Up:** `{data['id'][:12]}` — {data.get('name', 'untitled')}\n"
            f"   ({len(msgs)} msgs, last activity: {updated_dt})\n\n"
            f"**Last context:**\n{context}\n\n"
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

        # Read config first to check tool enablement
        data_dir = Path.home() / ".baw"
        try:
            config = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))
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

        # Re-read config
        data_dir = Path.home() / ".baw"
        try:
            config = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))
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
            self.send(chat_id, f"[>] **BAW Update** — Step {step}/{total_steps}\n{label}")

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
            output = ""
            info = {}
            all_plan_recaps = []
            all_failure_reasons = []
            all_checkpoint_results = []
            all_uncertain_claims = []
            _recalc_total = 0

            # Send typing indicator
            if chat_id:
                self.send_typing(chat_id)

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
                        f"Execute the full plan silently. Report only the final result."
                    )

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
                                   return "[QUEUED] Task stuck (no progress for >10min)."
                           else:
                               _stuck_seconds = 0
                           if self._cancel_event.is_set():
                               fut.cancel()
                               return "[STOP] Cancelled."
                    else:
                        fut.cancel()
                        return "[QUEUED] Task took too long (>30min)."

                output = response or ""

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
                    # Skip court verdict if the only output is the empty-fallback message
                    _has_real_content = bool(output.strip()) and \
                        "No additional output" not in output and \
                        "Completed. (No" not in output and \
                        "Task failed to reach goal" not in output
                    if _has_real_content:
                        output += f"\n⚖️ {agreement} (gap {gap})"
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
            # ── Compress excessive blank lines: 3+ consecutive newlines → 2 (keep 1 blank line max)
            output = re.sub(r'\n{3,}', '\n\n', output)
            output = output.strip()
            # Limit to 4000 chars
            if len(output) > 4000:
                output = output[:3997] + "..."
            # ── Guarantee non-empty output — user must always see a result ──
            if not output.strip():
                if all_failure_reasons:
                    lines = ["[FAIL] Task failed:"]
                    for r in all_failure_reasons:
                        lines.append(f"  • {r[:200]}")
                    output = "\n".join(lines)
                elif info and info.get("goal_achieved") is False:
                    output = "❗ 任務未能完成目標，需要跟進。"
                else:
                    output = "✅ 任務已完成。 (無額外輸出 — 以上進度訊息已包含步驟結果)"
            # ── Hallucination guard: LLM sometimes claims it "cannot access local files"
            #    even though it has read_file/write_file/terminal tools. Override it.
            _hallucination_phrases = [
                "無法直接讀取", "無法直接讀取你電腦上的檔案",
                "cannot access local files", "i cannot access", "我無法直接",
                "我無法讀取", "無法讀取你電腦",
                "唔支援直接 attach", "唔支援直接傳送", "唔支援直接",
                "唔支援上傳", "不能直接 attach", "cannot attach files",
                "cannot directly attach", "does not support attaching",
                "don't support sending files", "can't send files directly",
                "cannot send files", "can't attach files",
                "呢個 chat interface 唔支援", "chat interface 唔支援",
            ]
            if any(_hp in output.lower() for _hp in _hallucination_phrases):
                # Try to extract file path from original prompt
                _file_match = re.search(
                    r'((?:/tmp/|/home/|/app/|~/.baw/|/etc/|/var/)[^\s"\'\`\u3002\uff0c？]+)',
                    prompt
                )
                if _file_match:
                    _fpath = _file_match.group(1)
                    try:
                        from pathlib import Path as _Path
                        _pp = _Path(_fpath).expanduser().resolve()
                        if not _pp.exists():
                            _fcontent = f"Error: file not found: {_fpath}"
                        elif _pp.is_dir():
                            _items = [p.name + ('/' if p.is_dir() else '') for p in _pp.iterdir()]
                            _fcontent = f"Directory listing ({len(_items)} items):\n" + "\n".join(sorted(_items))
                        elif not _pp.is_file():
                            _fcontent = f"Error: not a file: {_fpath}"
                        else:
                            _fcontent = _pp.read_text(encoding="utf-8")
                        if _fcontent.startswith("Error:"):
                            output = f"[FAIL] 無法讀取 `{_fpath}`：{_fcontent}"
                        else:
                            output = (
                                f"[FILE] **檔案內容** (`{_fpath}`):\n"
                                f"```\n{_fcontent[:2000]}\n```"
                            )
                    except Exception as _e2:
                        output = f"[FAIL] 讀檔錯誤: {_e2}"
                else:
                    output = (
                        "[FAIL] 讀檔失敗: 請求包含讀取檔案，"
                        "但 LLM 誤報無法讀取。未能自動提取檔案路徑。"
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
            if chat_id and _progress_msg_id:
                try:
                    _first_line = output.strip().split('\n')[0][:100] if output.strip() else "✅ 完成"
                    self.send(chat_id, f"✅ {_first_line}", edit_msg_id=_progress_msg_id)
                except Exception:
                    pass

            return output.strip()

        except Exception as e:
            return f"[FAIL] BAW error: {e}"

    @staticmethod
    def _help_text() -> str:
        return (
            "[BOT] **BAW Bot** — Multi-platform Agent Interface\n\n"
            "Simply type anything and BAW will process it.\n\n"
            "**💬 Core:**\n"
            "/help — This message\n"
            "/status — BAW system status + sessions\n"
            "/btw `<text>` — Quick answer (no court, no plan)\n"
            "/fresh `<prompt>` — Raw model — no soul, no memories\n"
            "/court — 最近 5 單案件 (id+verdict+score+elapsed)\n"
            "/court `<id>` — 查全卷 (起訴/答辯/證物/判決)\n"
            "/court stats — 本週 metrics (核准率/平均 latency/tier 分流)\n"
            "/court live — 訂閱逐步推送 (M3 wire-in)\n"
            "/stop — Cancel running request\n"
            "/restart — Restart BAW engine\n\n"
            "**[PLAN] Sessions:**\n"
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
            "**[TEST] Validate (REAL tests):**\n"
            "/validate — Run all real-world validations\n"
            "/validate api — DeepSeek + MiniMax live API calls\n"
            "/validate evolve — Evolve logging (real write + read)\n"
            "/validate memory — Memory read/write\n"
            "/validate telegram — Bot connectivity\n"
            "/validate disk — Disk space check\n"
            "/validate git — Git status\n\n"
            "**🏥 Health & Ops:**\n"
            "/doctor, /dr — 10-point system health check\n"
            "/watchdog, /wd — Same as /doctor\n"
            "/backup, /bk — Create backup (or /backup list, /backup restore)\n"
            "/monitor, /mon — 24h error rate (or /monitor weekly)\n\n"
            "**🏛️ Tribunal (multi-model consensus):**\n"
            "/tribunal <question> — Ask multiple judges, get unified verdict\n"
            "/tribunal bench — Show current judge configuration\n"
            "(Customise judges in ~/.baw/config.yaml tribunal section)\n\n"
            "**[MODEL] Memory:**\n"
            "/memory `<text>` — Save a memory\n"
            "/search `<query>` — Search memories\n"
            "/evolve — Self-evolution stats\n\n"
            "**🛠 Tools:**\n"
            "/board — Generate HTML dashboard\n"
            "/version — BAW version\n"
            "/cron — List/manage scheduled tasks\n\n"
            "**[FIX] System:**\n"
            "/update — Git pull + changelog + restart\n"
            "/tts on|off|status — Toggle text-to-speech"
        )
