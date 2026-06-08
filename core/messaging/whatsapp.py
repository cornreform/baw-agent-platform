"""
BAW — WhatsApp Business Platform Connector

Uses WhatsApp Cloud API (Meta) or WhatsApp Business API.

Config (Cloud API):
  whatsapp:
    token: "***"              # Permanent access token
    phone_number_id: "***"    # Your WhatsApp Business phone number ID
    webhook_secret: "***"     # Webhook verification token

Config (Business API):
  whatsapp:
    api_base: "http://localhost:8080"  # Your WhatsApp Business API server
    token: "***"
"""
from __future__ import annotations
import logging
from . import BaseConnector, Message, register

logger = logging.getLogger("baw.whatsapp")


@register("whatsapp", "WhatsApp — Cloud API / Business API", "whatsapp")
class WhatsAppConnector(BaseConnector):
    """WhatsApp connector.

    Two modes:
    1. Cloud API (Meta) — requires Facebook Developer account + app
    2. Business API — self-hosted WhatsApp Business API server

    Setup (Cloud API):
      1. Go to https://developers.facebook.com
      2. Create a Meta App → WhatsApp → Setup
      3. Get your Phone Number ID and Permanent Access Token
      4. Configure webhook to point to your BAW webhook handler

    Setup (Business API):
      1. Deploy WhatsApp Business API server
      2. Configure phone number
      3. Point to your BAW webhook handler

    Note: WhatsApp requires a webhook (HTTP server) for incoming messages.
    This is not a polling-based connector. Use a reverse proxy
    (e.g., nginx, Caddy) to forward webhooks to BAW.
    """

    def __init__(self, config: dict, on_message):
        super().__init__(config.get("whatsapp", {}), on_message)
        self._token = self.config.get("token", "")
        self._phone_id = self.config.get("phone_number_id", "")
        self._webhook_secret = self.config.get("webhook_secret", "")
        self._api_base = self.config.get("api_base", "")
        self._webhook_port = self.config.get("webhook_port", 8081)

    def connect(self) -> bool:
        if not self._token:
            logger.warning("[WhatsApp] No token configured — connector disabled")
            return False
        if self._phone_id:
            logger.info(f"[WhatsApp] Cloud API configured (Phone ID: {self._phone_id[:8]}...)")
        elif self._api_base:
            logger.info(f"[WhatsApp] Business API configured ({self._api_base})")
        else:
            logger.warning("[WhatsApp] Neither Cloud API nor Business API configured")
            return False
        return True

    def disconnect(self):
        pass

    def send(self, chat_id: str, text: str) -> bool:
        """Send via WhatsApp Cloud API."""
        import httpx
        try:
            url = f"https://graph.facebook.com/v18.0/{self._phone_id}/messages"
            r = httpx.post(url, json={
                "messaging_product": "whatsapp",
                "to": chat_id,
                "type": "text",
                "text": {"body": text},
            }, headers={
                "Authorization": f"Bearer {self._token}",
            }, timeout=15)
            return r.status_code == 200
        except Exception as e:
            logger.error(f"[WhatsApp] send error: {e}")
            return False

    def _poll_loop(self):
        """WhatsApp uses webhooks, not polling.

        Set up a webhook server that listens for incoming messages
        and routes them through BAW. See docs/webhooks.md for setup.
        """
        logger.info("[WhatsApp] Webhook-based — no polling")
        while self._running:
            import time
            time.sleep(30)
