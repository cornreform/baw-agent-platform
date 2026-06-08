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
import logging
import time
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("baw.messaging")


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

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the platform. Return True on success."""
        ...

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

            if cmd == "mode" and arg:
                return self._run_baw(f'--cfg set mode {arg}')

            if cmd == "tone" and arg:
                return self._run_baw(f'--cfg set tone.default {arg}')

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

            if cmd == "version":
                return self._run_baw("--version")

            if cmd == "mode":
                modes = ["quick", "hybrid", "tight"]
                return f"Current mode: {self.config.get('mode', 'tight')}\nAvailable: {', '.join(modes)}"

            if cmd in ("exit", "quit", "stop"):
                return "👋 Goodbye!"

        # Default: pass to BAW
        return self._run_baw(text)

    def _run_baw(self, prompt: str) -> str:
        """Run a BAW command and return the output."""
        import subprocess as sp
        import shlex
        import shutil
        try:
            # Resolve `baw` CLI — not always in PATH from systemd
            baw_cmd = shutil.which("baw")
            if not baw_cmd:
                # Fallback to known locations
                for p in [
                    Path.home() / ".local" / "bin" / "baw",
                    Path(__file__).parent.parent / "baw",
                ]:
                    if p.exists():
                        baw_cmd = str(p)
                        break
            if not baw_cmd:
                return "❌ BAW CLI not found. Install with: cd ~/baw && ./install.sh"

            result = sp.run(
                [baw_cmd, "--mode", self.config.get("mode", "quick"), prompt],
                capture_output=True, text=True, timeout=60,
                cwd=str(Path.home() / "baw"),
            )
            output = ""
            if result.stdout:
                output = result.stdout
            elif result.stderr and result.returncode != 0:
                output = f"❌ BAW error ({result.returncode}): {result.stderr[-300:]}"
            # Strip HTML tags for Telegram display
            import re
            output = re.sub(r'<[^>]+>', '', output)
            # Limit to 4000 chars
            if len(output) > 4000:
                output = output[:3997] + "..."
            return output.strip()
        except sp.TimeoutExpired:
            return "⏳ BAW task timed out (120s). Try a simpler request."
        except Exception as e:
            return f"❌ Error: {e}"

    @staticmethod
    def _help_text() -> str:
        return (
            "🤖 **BAW Bot** — Multi-platform Agent Interface\n\n"
            "Simply type anything and BAW will process it.\n\n"
            "**Commands:**\n"
            "/btw <text> — Quick answer (no court)\n"
            "/mode quick|hybrid|tight — Switch execution mode\n"
            "/tone casual|business|teaching|... — Switch tone\n"
            "/status — BAW system status\n"
            "/court — Show last Angel/Devil verdict\n"
            "/memory <text> — Save a memory\n"
            "/search <query> — Search memories\n"
            "/board — Generate HTML dashboard\n"
            "/version — BAW version\n"
            "/help — This message\n\n"
            "**Examples:**\n"
            "• list files in current directory\n"
            "• check disk space\n"
            "• /btw What time is it?\n"
            "• /tone teaching\n"
            "• /mode quick"
        )
