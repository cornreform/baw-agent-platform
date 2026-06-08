"""
BAW — Signal Messenger Connector

Uses Signal Messenger REST API.
Requires: signal-cli (https://github.com/AsamK/signal-cli) running as daemon.

Config:
  signal:
    phone: "+15551234567"    # Signal phone number
    signal_cli_path: "signal-cli"  # Path to signal-cli binary
"""
from __future__ import annotations
import json
import logging
import subprocess as sp
from . import BaseConnector, Message, register

logger = logging.getLogger("baw.signal")


@register("signal", "Signal Messenger — via signal-cli REST API", "signal")
class SignalConnector(BaseConnector):
    """Signal connector wrapping the signal-cli daemon.

    Setup:
      1. Install signal-cli: https://github.com/AsamK/signal-cli
      2. Register: signal-cli -u +15551234567 register
      3. Verify: signal-cli -u +15551234567 verify <code>
      4. Start daemon: signal-cli -u +15551234567 daemon
    """

    def __init__(self, config: dict, on_message):
        super().__init__(config.get("signal", {}), on_message)
        self._phone = self.config.get("phone", "")
        self._cli_path = self.config.get("signal_cli_path", "signal-cli")

    def connect(self) -> bool:
        if not self._phone:
            logger.warning("[Signal] No phone number configured — connector disabled")
            return False
        try:
            sp.run([self._cli_path, "--version"], capture_output=True, timeout=5)
            logger.info(f"[Signal] CLI found, phone: {self._phone}")
            return True
        except FileNotFoundError:
            logger.warning("[Signal] signal-cli not installed. Install from https://github.com/AsamK/signal-cli")
            return False
        except Exception as e:
            logger.error(f"[Signal] Connection error: {e}")
            return False

    def disconnect(self):
        pass

    def send(self, chat_id: str, text: str) -> bool:
        """Send a Signal message via signal-cli."""
        try:
            sp.run(
                [self._cli_path, "-u", self._phone, "send", "-m", text, chat_id],
                capture_output=True, timeout=30,
            )
            return True
        except Exception as e:
            logger.error(f"[Signal] send error: {e}")
            return False

    def _poll_loop(self):
        """Signal uses signal-cli daemon mode for receiving.

        See: https://github.com/AsamK/signal-cli/wiki/DBus-service
        Or use the JSON-RPC mode for message reception.

        This connector requires manual daemon setup for now.
        """
        logger.info("[Signal] Poll loop: signal-cli daemon handles reception")
        # signal-cli daemon handles message reception automatically
        # Integration requires the JSON-RPC API or D-Bus
        while self._running:
            import time
            time.sleep(60)  # Minimal polling — real impl uses daemon callbacks
