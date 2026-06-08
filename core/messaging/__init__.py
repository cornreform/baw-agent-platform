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

    # ── In-process BAW engine (lazy-loaded) ──
    _BAW = None  # {'run_agent': fn, 'config': dict, 'data_dir': Path}

    def _baw_ensure(self):
        """Lazy-import BAW modules in-process (no subprocess)."""
        if self._BAW is not None:
            return self._BAW
        import os, sys, yaml
        from pathlib import Path

        # Add BAW to sys.path (same as baw script does)
        baw_root = Path(__file__).parent.parent.resolve()  # ~/baw/
        sys.path.insert(0, str(baw_root))
        sys.path.insert(0, str(baw_root.parent))

        # Import once
        from baw.core.loop import run_agent
        from baw.tools import register_all as reg_tools
        reg_tools()

        # Load config
        data_dir = Path.home() / ".baw"
        config = yaml.safe_load((data_dir / "config.yaml").read_text(encoding="utf-8"))

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

    def _run_baw(self, prompt: str) -> str:
        """Run BAW in-process (no subprocess)."""
        import re
        import asyncio
        from concurrent.futures import ThreadPoolExecutor, TimeoutError

        try:
            baw = self._baw_ensure()
            run_agent = baw["run_agent"]
            config = baw["config"]
            data_dir = baw["data_dir"]

            # mode from telegram config or default to quick
            mode = self.config.get("mode", "quick")

            # Run BAW with a timeout via thread pool
            with ThreadPoolExecutor(1) as pool:
                fut = pool.submit(
                    run_agent,
                    prompt=prompt,
                    config=config,
                    data_dir=data_dir,
                    mode=mode,
                    verbose=False,
                )
                try:
                    response, info = fut.result(timeout=60)
                except TimeoutError:
                    fut.cancel()
                    return "⏳ BAW took too long (>60s). Try a simpler request."

            output = response or ""

            # Strip HTML tags for Telegram display
            output = re.sub(r'<[^>]+>', '', output)
            # Limit to 4000 chars
            if len(output) > 4000:
                output = output[:3997] + "..."
            return output.strip()

        except Exception as e:
            return f"❌ BAW error: {e}"

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
