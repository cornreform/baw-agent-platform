"""
BAW — Matrix Protocol Connector

Uses Matrix Client-Server API (REST) via httpx.
Requires: a Matrix account on any homeserver.

Config:
  matrix:
    homeserver: "https://matrix.org"
    username: "@baw:matrix.org"
    password: "***"           # Or access_token
    access_token: "***"       # Preferred over password
"""
from __future__ import annotations
import json
import logging
import time
import httpx
from . import BaseConnector, Message, register

logger = logging.getLogger("baw.matrix")

POLL_INTERVAL = 3  # Seconds


@register("matrix", "Matrix Protocol — REST API via httpx", "matrix")
class MatrixConnector(BaseConnector):
    """Matrix connector using the Client-Server REST API.

    Supports: login, sync (long-poll), send message, room discovery.
    """

    def __init__(self, config: dict, on_message):
        super().__init__(config.get("matrix", {}), on_message)
        self._homeserver = self.config.get("homeserver", "https://matrix.org")
        self._username = self.config.get("username", "")
        self._password = self.config.get("password", "")
        self._token = self.config.get("access_token", "")
        self._client: httpx.Client | None = None
        self._user_id = ""
        self._device_id = "BAW"
        self._next_batch = ""

    def connect(self) -> bool:
        if not self._username:
            logger.warning("[Matrix] No username configured — connector disabled")
            return False
        try:
            self._client = httpx.Client(timeout=15)
            # Authenticate
            if self._token:
                self._client.headers.update({"Authorization": f"Bearer {self._token}"})
                whoami = self._client.get(f"{self._homeserver}/_matrix/client/v3/account/whoami")
                if whoami.status_code == 200:
                    self._user_id = whoami.json()["user_id"]
                    logger.info(f"[Matrix] Connected as {self._user_id} (token)")
                    return True
                logger.warning("[Matrix] Token invalid, trying password...")

            if self._password:
                r = self._client.post(
                    f"{self._homeserver}/_matrix/client/v3/login",
                    json={
                        "type": "m.login.password",
                        "user": self._username,
                        "password": self._password,
                        "device_id": self._device_id,
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    self._token = data["access_token"]
                    self._user_id = data["user_id"]
                    self._client.headers.update({"Authorization": f"Bearer {self._token}"})
                    logger.info(f"[Matrix] Connected as {self._user_id}")
                    return True
                logger.error(f"[Matrix] Login failed: {r.status_code} {r.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Matrix] Connection error: {e}")
            return False

    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    def send(self, chat_id: str, text: str) -> bool:
        """Send a message to a Matrix room."""
        if not self._client or not self._token:
            return False
        try:
            # Split long messages
            if len(text) > 40000:
                text = text[:39997] + "..."
            r = self._client.put(
                f"{self._homeserver}/_matrix/client/v3/rooms/{chat_id}/send/m.room.message/"
                f"{int(time.time() * 1000)}",
                json={
                    "msgtype": "m.text",
                    "body": text,
                    "format": "org.matrix.custom.html",
                    "formatted_body": text.replace("\n", "<br>"),
                },
                timeout=15,
            )
            return r.status_code == 200
        except Exception as e:
            logger.error(f"[Matrix] send error: {e}")
            return False

    def _poll_loop(self):
        """Long-poll Matrix sync for new messages."""
        if not self._client:
            return
        while self._running:
            try:
                params = {"timeout": 30000}  # 30s long-poll
                if self._next_batch:
                    params["since"] = self._next_batch

                r = self._client.get(
                    f"{self._homeserver}/_matrix/client/v3/sync",
                    params=params,
                    timeout=35,
                )
                if r.status_code != 200:
                    time.sleep(POLL_INTERVAL)
                    continue

                data = r.json()
                self._next_batch = data.get("next_batch", "")

                # Process rooms
                rooms = data.get("rooms", {}).get("join", {})
                for room_id, room_data in rooms.items():
                    for event in room_data.get("timeline", {}).get("events", []):
                        self._handle_event(room_id, event)

            except httpx.TimeoutException:
                continue
            except Exception as e:
                logger.error(f"[Matrix] Sync error: {e}")
                time.sleep(POLL_INTERVAL)

    def _handle_event(self, room_id: str, event: dict):
        """Process a Matrix room event."""
        if event.get("type") != "m.room.message":
            return
        if event.get("sender") == self._user_id:
            return
        content = event.get("content", {})
        if content.get("msgtype") != "m.text":
            return

        text = content.get("body", "").strip()
        if not text:
            return

        user_name = event.get("sender", "User")
        logger.info(f"[Matrix] <{user_name}> {text[:80]}")

        msg_obj = Message(
            platform="matrix",
            chat_id=room_id,
            user_id=event.get("sender", ""),
            text=text,
            raw=event,
        )
        response = self.route(msg_obj)
        if response:
            self.send(room_id, response)
