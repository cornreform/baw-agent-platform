"""
BAW — Telegram Bot Connector

Long-polling Telegram Bot via httpx (no extra dependencies).
Fully featured: commands, replies, error handling, reconnection.
"""
from __future__ import annotations
import json
import logging
import threading
import time
import httpx
from typing import Optional

from . import BaseConnector, Message, register

logger = logging.getLogger("baw.telegram")

POLL_TIMEOUT = 30  # Long-poll timeout (seconds)
POLL_RETRY_DELAY = 5
MAX_MESSAGE_LENGTH = 4000
API_BASE = "https://api.telegram.org/bot{token}"


@register("telegram", "Telegram Bot — long-polling via httpx", "telegram")
class TelegramConnector(BaseConnector):
    """Telegram Bot connector using long-polling (getUpdates).

    Config:
      telegram:
        token: "***"          # Bot token from @BotFather
        allowed_users: []     # Optional: list of user IDs to allow
    """

    def __init__(self, config: dict, on_message):
        super().__init__(config.get("telegram", {}), on_message)
        self._token = self.config.get("token", "")
        self._allowed = self.config.get("allowed_users", [])
        self._offset = 0
        self._client: httpx.Client | None = None
        self._api_base = API_BASE.format(token=self._token) if self._token else ""
        self._debounce_until = 0.0

    def connect(self) -> bool:
        """Test connection by fetching bot info."""
        if not self._token:
            logger.error("[Telegram] No token configured")
            return False
        try:
            self._client = httpx.Client(timeout=10)
            r = self._client.get(f"{self._api_base}/getMe")
            if r.status_code == 200:
                info = r.json()
                if info.get("ok"):
                    bot_name = info["result"].get("first_name", "BAW Bot")
                    logger.info(f"[Telegram] Connected as @{info['result'].get('username', '?')}")

                    # Register slash command menu
                    self._register_commands()

                    return True
            logger.error(f"[Telegram] getMe failed: {r.status_code} {r.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Telegram] Connection error: {e}")
            return False

    def _register_commands(self):
        """Register bot command menu via setMyCommands."""
        commands = [
            {"command": "start",   "description": "Welcome message"},
            {"command": "help",    "description": "Show available commands"},
            {"command": "status",  "description": "BAW system status"},
            {"command": "btw",     "description": "Quick answer (no court)"},
            {"command": "model",   "description": "Switch model: deepseek / kimi / minimax"},
            {"command": "mode",    "description": "Switch mode: quick / hybrid / tight"},
            {"command": "tone",    "description": "Switch tone: casual / business / teaching"},
            {"command": "court",   "description": "Show last Angel/Devil verdict"},
            {"command": "memory",  "description": "Save a memory entry"},
            {"command": "search",  "description": "Search stored memories"},
            {"command": "board",   "description": "Generate HTML dashboard"},
            {"command": "stop",    "description": "Stop current processing and cancel"},
            {"command": "restart", "description": "Restart BAW engine"},
        ]
        try:
            self._client.post(
                f"{self._api_base}/setMyCommands",
                json={"commands": commands},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"[Telegram] Failed to register commands: {e}")

    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    def send(self, chat_id: str, text: str) -> bool:
        """Send a message to a Telegram chat."""
        if not self._client or not self._token:
            return False
        try:
            # Split long messages
            if len(text) > MAX_MESSAGE_LENGTH:
                parts = []
                while text:
                    parts.append(text[:MAX_MESSAGE_LENGTH])
                    text = text[MAX_MESSAGE_LENGTH:]
                for part in parts:
                    self._client.post(
                        f"{self._api_base}/sendMessage",
                        json={"chat_id": chat_id, "text": part, "parse_mode": "Markdown"},
                        timeout=10,
                    )
                return True
            r = self._client.post(
                f"{self._api_base}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            if r.status_code != 200:
                # Retry without Markdown if parse failed
                if "can't parse entities" in r.text:
                    r = self._client.post(
                        f"{self._api_base}/sendMessage",
                        json={"chat_id": chat_id, "text": text},
                        timeout=10,
                    )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"[Telegram] send error: {e}")
            return False

    def _poll_loop(self):
        """Long-polling loop."""
        if not self._client:
            logger.error("[Telegram] No client — can't poll")
            return

        while self._running:
            try:
                r = self._client.post(
                    f"{self._api_base}/getUpdates",
                    json={
                        "offset": self._offset,
                        "timeout": POLL_TIMEOUT,
                        "allowed_updates": ["message"],
                    },
                    timeout=POLL_TIMEOUT + 5,
                )
                if r.status_code != 200:
                    logger.warning(f"[Telegram] getUpdates HTTP {r.status_code}")
                    time.sleep(POLL_RETRY_DELAY)
                    continue

                data = r.json()
                if not data.get("ok"):
                    logger.warning(f"[Telegram] API error: {data}")
                    time.sleep(POLL_RETRY_DELAY)
                    continue

                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)

            except httpx.TimeoutException:
                # Timeout is normal for long-poll — just retry
                continue
            except Exception as e:
                logger.error(f"[Telegram] Poll error: {e}")
                time.sleep(POLL_RETRY_DELAY)

    def _handle_update(self, update: dict):
        """Process a single Telegram update (non-blocking)."""
        msg = update.get("message")
        if not msg:
            return

        chat_id = str(msg["chat"]["id"])
        user_id = str(msg["from"]["id"])
        user_name = msg["from"].get("first_name", "User")
        text = msg.get("text", "").strip()

        if not text:
            return

        # Access control
        if self._allowed and user_id not in self._allowed:
            self.send(chat_id, "⛔ You are not authorized to use this bot.")
            return

        logger.info(f"[Telegram] <{user_name}> {text[:80]}")

        # /stop — cancel running BAW immediately + enforce debounce window
        if text.strip().lower().startswith("/stop"):
            self._cancel_event.set()
            self._busy = False
            self._debounce_until = time.time() + 1.0
            self.send(chat_id, "⏹ Stopped.")
            return

        # Debounce window: suppress new threads right after /stop
        if self._debounce_until and time.time() < self._debounce_until:
            self.send(chat_id, "⏳ Please wait a moment before sending a new request.")
            return

        # If busy, reject and suggest /stop
        if self._busy:
            self.send(chat_id, "⏳ Still processing previous request. Use /stop to cancel.")
            return

        # Start async processing in background thread
        self._busy = True
        self._cancel_event.clear()
        threading.Thread(
            target=self._process_message,
            args=(chat_id, user_id, user_name, text, msg),
            daemon=True,
        ).start()

    def _process_message(self, chat_id, user_id, user_name, text, msg):
        """Process a message in background thread (runs route() + send())."""
        logger.info(f"[Telegram] Processing: {text[:60]}...")

        # Typing indicator heartbeat (respects cancel event)
        _typing_stop = threading.Event()
        def _typing_heartbeat():
            while not _typing_stop.is_set() and not self._cancel_event.is_set():
                try:
                    self._client.post(
                        f"{self._api_base}/sendChatAction",
                        json={"chat_id": chat_id, "action": "typing"},
                        timeout=5,
                    )
                except Exception:
                    pass
                _typing_stop.wait(3.0)
        _hb = threading.Thread(target=_typing_heartbeat, daemon=True)
        _hb.start()

        try:
            # Check cancelled before starting BAW
            if self._cancel_event.is_set():
                return

            msg_obj = Message(
                platform="telegram",
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                raw=msg,
            )
            response = self.route(msg_obj)
            _typing_stop.set()

            # If cancelled during processing, discard result
            if self._cancel_event.is_set():
                return
            if response:
                self.send(chat_id, response)

            # If restart was requested, exit cleanly so systemd restarts
            if self._restart_requested:
                import os
                logger.info("[Telegram] Restart requested — exiting")
                os._exit(0)
        except Exception as e:
            logger.error(f"[Telegram] Process error: {e}")
            if not self._cancel_event.is_set():
                try:
                    self.send(chat_id, f"❌ Error: {e}")
                except Exception:
                    pass
        finally:
            self._busy = False
