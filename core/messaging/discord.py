"""
BAW — Discord Bot Connector

Uses Discord REST API via httpx (no discord.py dependency).
Gateway intent polling for messages.
"""
from __future__ import annotations
import json
import logging
import time
import httpx
from typing import Optional

from . import BaseConnector, Message, register

logger = logging.getLogger("baw.discord")

POLL_INTERVAL = 2  # Seconds between polls
API_BASE = "https://discord.com/api/v10"


@register("discord", "Discord Bot — REST API via httpx", "discord")
class DiscordConnector(BaseConnector):
    """Discord Bot connector using REST API.

    Config:
      discord:
        token: "***"          # Bot token from Discord Developer Portal
        allowed_channels: []  # Optional: list of channel IDs
        prefix: "baw "        # Command prefix (e.g. "baw list files")
    """

    def __init__(self, config: dict, on_message):
        super().__init__(config.get("discord", {}), on_message)
        self._token = self.config.get("token", "")
        self._allowed_channels = self.config.get("allowed_channels", [])
        self._prefix = self.config.get("prefix", "baw ")
        self._client: httpx.Client | None = None
        self._headers: dict = {}
        self._last_message_id: dict[str, str] = {}  # channel_id → last_message_id
        self._bot_user_id: str = ""

    def connect(self) -> bool:
        if not self._token:
            logger.error("[Discord] No token configured")
            return False
        self._headers = {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "BAW (https://github.com/cornreform/baw-agent-platform)",
        }
        try:
            self._client = httpx.Client(headers=self._headers, timeout=10)
            r = self._client.get(f"{API_BASE}/users/@me")
            if r.status_code == 200:
                info = r.json()
                self._bot_user_id = str(info["id"])
                bot_name = info.get("username", "BAW Bot")
                logger.info(f"[Discord] Connected as {bot_name} (ID: {self._bot_user_id})")
                return True
            logger.error(f"[Discord] Auth failed: {r.status_code} {r.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Discord] Connection error: {e}")
            return False

    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    def send(self, chat_id: str, text: str) -> bool:
        """Send a message to a Discord channel."""
        if not self._client:
            return False
        try:
            # Split long messages
            if len(text) > 1900:
                parts = []
                while text:
                    parts.append(text[:1900])
                    text = text[1900:]
                for i, part in enumerate(parts):
                    r = self._client.post(
                        f"{API_BASE}/channels/{chat_id}/messages",
                        json={"content": part},
                        timeout=10,
                    )
                    if r.status_code != 200 and i == 0:
                        logger.warning(f"[Discord] send HTTP {r.status_code}: {r.text[:100]}")
                return True
            r = self._client.post(
                f"{API_BASE}/channels/{chat_id}/messages",
                json={"content": text},
                timeout=10,
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"[Discord] send error: {e}")
            return False

    def _poll_loop(self):
        """Poll for new messages in allowed channels.

        Uses a simple polling approach: periodically fetch recent messages
        from each configured channel and process new ones.
        """
        if not self._client:
            return

        while self._running:
            try:
                # Get bot's guilds/channels
                if not self._allowed_channels:
                    self._discover_channels()

                for channel_id in self._allowed_channels:
                    self._poll_channel(channel_id)

                time.sleep(POLL_INTERVAL)

            except Exception as e:
                logger.error(f"[Discord] Poll error: {e}")
                time.sleep(POLL_INTERVAL * 5)

    def _discover_channels(self):
        """Auto-discover accessible channels."""
        try:
            r = self._client.get(f"{API_BASE}/users/@me/guilds", timeout=10)
            if r.status_code != 200:
                return
            for guild in r.json()[:3]:  # Limit to 3 guilds
                guild_id = guild["id"]
                r2 = self._client.get(
                    f"{API_BASE}/guilds/{guild_id}/channels",
                    timeout=10,
                )
                if r2.status_code != 200:
                    continue
                for channel in r2.json():
                    if channel["type"] == 0:  # GUILD_TEXT
                        cid = channel["id"]
                        if cid not in self._allowed_channels:
                            self._allowed_channels.append(cid)
                            logger.info(f"[Discord] Discovered channel: #{channel.get('name', '?')} ({cid})")
        except Exception as e:
            logger.debug(f"[Discord] Channel discovery: {e}")

    def _poll_channel(self, channel_id: str):
        """Fetch recent messages from a channel and process new ones."""
        try:
            params = {"limit": 5}
            last_id = self._last_message_id.get(channel_id)
            if last_id:
                params["after"] = last_id

            r = self._client.get(
                f"{API_BASE}/channels/{channel_id}/messages",
                params=params,
                timeout=10,
            )
            if r.status_code != 200:
                return

            messages = r.json()
            if not messages:
                return

            # Update last message ID
            self._last_message_id[channel_id] = messages[0]["id"]

            # Process messages in reverse (oldest first)
            for msg in reversed(messages):
                self._handle_message(channel_id, msg)

        except Exception as e:
            logger.debug(f"[Discord] Poll channel {channel_id}: {e}")

    def _handle_message(self, channel_id: str, msg: dict):
        """Process a single Discord message."""
        # Skip own messages
        if str(msg["author"]["id"]) == self._bot_user_id:
            return
        # Skip bot messages
        if msg["author"].get("bot"):
            return

        text = msg.get("content", "").strip()
        if not text:
            return

        user_name = msg["author"].get("username", "User")
        logger.info(f"[Discord] <{user_name}> {text[:80]}")

        # Check for BAW prefix
        if text.startswith(self._prefix):
            text = text[len(self._prefix):].strip()
        else:
            return  # Ignore messages without prefix

        # Route through BAW
        msg_obj = Message(
            platform="discord",
            chat_id=channel_id,
            user_id=str(msg["author"]["id"]),
            text=text,
            raw=msg,
        )
        # Send typing indicator
        try:
            self._client.post(
                f"{API_BASE}/channels/{channel_id}/typing",
                timeout=5,
            )
        except Exception:
            pass

        response = self.route(msg_obj)
        if response:
            self.send(channel_id, response)
