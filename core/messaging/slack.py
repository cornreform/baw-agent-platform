"""
BAW — Slack Connector (Socket Mode)

Uses Slack Socket Mode via WebSocket — no public URL needed.
Requires: Slack App with Socket Mode enabled + Bot Token + App-Level Token.

Config:
  slack:
    bot_token: "xoxb-***"           # Bot User OAuth Token
    app_token: "xapp-***"           # App-Level Token (Socket Mode)
    allowed_channels: []            # Optional: channel IDs to listen (empty = all)
    allowed_users: []               # Optional: user IDs to respond (empty = all)
    prefix: ""                      # Optional: command prefix (empty = respond to all)
    max_message_length: 4000        # Slack message limit

Setup:
  1. Go to https://api.slack.com/apps → Create New App → From scratch
  2. Enable Socket Mode → Generate App-Level Token (scopes: connections:write)
  3. OAuth & Permissions → Add Bot Token Scopes:
     - chat:write
     - app_mentions:read
     - im:history
     - channels:history
  4. Install to workspace → copy Bot User OAuth Token (xoxb-***)
  5. Basic Info → copy App-Level Token (xapp-***)
  6. Event Subscriptions → Subscribe to bot events:
     - message.im
     - message.channels (optional)
     - app_mention (optional)
"""
from __future__ import annotations
import json
import logging
import time
import threading
from typing import Optional

import httpx
import websocket

from . import BaseConnector, Message, register

logger = logging.getLogger("baw.slack")

SLACK_API_BASE = "https://slack.com/api"
RECONNECT_DELAY = 5  # Seconds between reconnect attempts


@register("slack", "Slack — Socket Mode via WebSocket", "slack")
class SlackConnector(BaseConnector):
    """Slack connector using Socket Mode (WebSocket).

    No public URL required — ideal for local dev and Docker containers.
    """

    def __init__(self, config: dict, on_message):
        super().__init__(config.get("slack", {}), on_message)
        self._bot_token = self.config.get("bot_token", "")
        self._app_token = self.config.get("app_token", "")
        self._allowed_channels = self.config.get("allowed_channels", [])
        self._allowed_users = self.config.get("allowed_users", [])
        self._prefix = self.config.get("prefix", "").strip()
        self._max_len = self.config.get("max_message_length", 4000)
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._bot_user_id: str = ""
        self._client: httpx.Client | None = None
        self._headers: dict = {}

    def connect(self) -> bool:
        if not self._bot_token or not self._app_token:
            logger.error("[Slack] Both bot_token and app_token required")
            return False
        self._headers = {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json",
        }
        try:
            self._client = httpx.Client(headers=self._headers, timeout=15)
            # Verify bot token
            r = self._client.get(f"{SLACK_API_BASE}/auth.test")
            if r.status_code != 200:
                logger.error(f"[Slack] Auth test failed: HTTP {r.status_code}")
                return False
            data = r.json()
            if not data.get("ok"):
                logger.error(f"[Slack] Auth failed: {data.get('error')}")
                return False
            self._bot_user_id = data.get("user_id", "")
            bot_name = data.get("user", "BAW Bot")
            logger.info(f"[Slack] Connected as {bot_name} (ID: {self._bot_user_id})")
            # Start WebSocket
            self._start_socket_mode()
            return True
        except Exception as e:
            logger.error(f"[Slack] Connection error: {e}")
            return False

    def disconnect(self):
        self._running = False
        if self._ws:
            self._ws.close()
            self._ws = None
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        if self._client:
            self._client.close()
            self._client = None

    def send(self, chat_id: str, text: str) -> bool:
        """Send a message to a Slack channel/user."""
        if not self._client:
            return False
        try:
            # Split long messages
            chunks = self._split_text(text)
            for chunk in chunks:
                r = self._client.post(
                    f"{SLACK_API_BASE}/chat.postMessage",
                    json={"channel": chat_id, "text": chunk},
                    timeout=15,
                )
                if r.status_code != 200:
                    logger.warning(f"[Slack] send HTTP {r.status_code}: {r.text[:100]}")
                    return False
                resp = r.json()
                if not resp.get("ok"):
                    logger.warning(f"[Slack] send error: {resp.get('error')}")
                    return False
            return True
        except Exception as e:
            logger.error(f"[Slack] send error: {e}")
            return False

    def _split_text(self, text: str) -> list[str]:
        """Split text into Slack-safe chunks."""
        if len(text) <= self._max_len:
            return [text]
        chunks = []
        while text:
            chunk = text[:self._max_len]
            # Try to break at newline
            if len(text) > self._max_len:
                last_nl = chunk.rfind("\n")
                if last_nl > self._max_len * 0.8:
                    chunk = chunk[:last_nl]
            chunks.append(chunk)
            text = text[len(chunk):]
        return chunks

    def _start_socket_mode(self):
        """Open Socket Mode WebSocket connection."""
        try:
            # Get WebSocket URL
            r = self._client.post(
                f"{SLACK_API_BASE}/apps.connections.open",
                headers={"Authorization": f"Bearer {self._app_token}"},
                timeout=15,
            )
            if r.status_code != 200:
                logger.error(f"[Slack] connections.open failed: HTTP {r.status_code}")
                return
            data = r.json()
            if not data.get("ok"):
                logger.error(f"[Slack] connections.open error: {data.get('error')}")
                return
            ws_url = data.get("url")
            if not ws_url:
                logger.error("[Slack] No WebSocket URL received")
                return

            self._ws = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_ws_open,
                on_message=self._on_ws_message,
                on_error=self._on_ws_error,
                on_close=self._on_ws_close,
            )
            self._ws_thread = threading.Thread(target=self._ws.run_forever, daemon=True)
            self._ws_thread.start()
            logger.info("[Slack] Socket Mode WebSocket started")
        except Exception as e:
            logger.error(f"[Slack] Socket mode start error: {e}")

    def _on_ws_open(self, ws):
        logger.info("[Slack] WebSocket connected")

    def _on_ws_message(self, ws, message):
        try:
            envelope = json.loads(message)
            # Handle Slack envelope types
            msg_type = envelope.get("type", "")
            if msg_type == "disconnect":
                reason = envelope.get("reason", "unknown")
                logger.info(f"[Slack] Server requested disconnect: {reason}")
                ws.close()
                return
            if msg_type == "hello":
                logger.info("[Slack] Server hello received")
                return
            # Acknowledge receipt
            if "envelope_id" in envelope:
                ack = {"envelope_id": envelope["envelope_id"]}
                ws.send(json.dumps(ack))
            # Process payload
            payload = envelope.get("payload", {})
            event = payload.get("event", {})
            if not event:
                return
            self._handle_event(event)
        except json.JSONDecodeError:
            logger.warning(f"[Slack] Invalid JSON: {message[:200]}")
        except Exception as e:
            logger.error(f"[Slack] Message handling error: {e}")

    def _on_ws_error(self, ws, error):
        logger.error(f"[Slack] WebSocket error: {error}")

    def _on_ws_close(self, ws, close_status_code, close_msg):
        logger.info(f"[Slack] WebSocket closed ({close_status_code}: {close_msg})")
        if self._running:
            logger.info(f"[Slack] Reconnecting in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)
            self._start_socket_mode()

    def _handle_event(self, event: dict):
        """Process a Slack event."""
        event_type = event.get("type", "")
        # Only handle messages
        if event_type not in ("message", "app_mention"):
            return
        # Skip bot messages
        if event.get("bot_id") or event.get("user") == self._bot_user_id:
            return
        # Skip message subtypes (edits, deletes, etc.)
        if event.get("subtype"):
            return

        channel = event.get("channel", "")
        user = event.get("user", "")
        text = event.get("text", "").strip()

        if not text:
            return

        # Channel filter
        if self._allowed_channels and channel not in self._allowed_channels:
            return
        # User filter
        if self._allowed_users and user not in self._allowed_users:
            return

        # Prefix filter
        if self._prefix:
            if text.startswith(self._prefix):
                text = text[len(self._prefix):].strip()
            else:
                return
        else:
            # If no prefix, respond to DMs and @mentions only
            if not event.get("channel_type") == "im" and f"<@{self._bot_user_id}>" not in text:
                return
            # Remove @mention from text
            text = text.replace(f"<@{self._bot_user_id}>", "").strip()

        user_name = event.get("username", user)
        logger.info(f"[Slack] <{user_name}> {text[:80]}")

        msg_obj = Message(
            platform="slack",
            chat_id=channel,
            user_id=user,
            text=text,
            raw=event,
        )
        response = self.route(msg_obj)
        if response:
            self.send(channel, response)

    def _poll_loop(self):
        """Socket Mode uses WebSocket — no polling needed.

        This method keeps the connector alive for lifecycle management.
        """
        logger.info("[Slack] Socket Mode active (WebSocket)")
        while self._running:
            time.sleep(1)
            # WebSocket auto-reconnects via _on_ws_close
