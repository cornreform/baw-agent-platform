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
        self._cancel_event = threading.Event()
        self._busy = False
        self._restart_requested = False

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

            if cmd in ("version", "v"):
                return self._run_baw("--version")

            if cmd in ("exit", "quit"):
                return "👋 Goodbye!"

            if cmd in ("stop",):
                self._cancel_event.set()
                self._busy = False
                return "⏹ Stopped."

            if cmd in ("restart",):
                self._restart_requested = True
                return "🔄 Restarting BAW engine..."

            if cmd == "mode" and arg:
                return self._baw_cfg_set("mode", arg)

            if cmd == "mode":
                return (
                    f"Current mode: {self.config.get('mode', 'tight')}\n"
                    f"Available: quick, hybrid, tight"
                )

            if cmd == "tone" and arg:
                return self._baw_cfg_set("tone.default", arg)

            if cmd == "tone":
                tones = ["casual", "business", "teaching", "client-doc", "ot-rt", "stepwise"]
                return f"Current tone: {self.config.get('tone', {}).get('default', 'casual')}\nAvailable: {', '.join(tones)}"

            if cmd in ("model", "m") and arg:
                if arg not in self._MODELS:
                    return f"Model '{arg}' not found. Available: {', '.join(self._MODELS)}"
                return self._baw_cfg_set("model.default", arg)

            if cmd in ("model", "models"):
                baw = self._baw_ensure()
                current = baw["config"].get("model", {}).get("default", "deepseek-v4-flash")
                return (
                    f"Current model: {current}\n"
                    f"Available:\n"
                    + "\n".join(f"  /model {m}" for m in self._MODELS)
                )

        # Default: pass to BAW
        return self._run_baw(text)

    # ── In-process BAW engine (lazy-loaded) ──
    _BAW = None  # {'run_agent': fn, 'config': dict, 'data_dir': Path}
    _MODELS = ["deepseek-v4-flash", "kimi-k2.6", "MiniMax-M3"]

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
        _reg(**_ld('bash'))
        _reg(**_ld('read_file'))
        _reg(**_ld('write_file'))
        _reg(**_ld('web_search'))

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

        # Write back
        cfg_path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")

        return f"✅ Config updated: {key} → {value}"

    def _run_baw(self, prompt: str) -> str:
        """Run BAW in-process (no subprocess)."""
        import re
        import asyncio
        from concurrent.futures import ThreadPoolExecutor, TimeoutError

        # If cancel was requested, consume it and return immediately
        if self._cancel_event.is_set():
            self._cancel_event.clear()
            self._busy = False
            return "⏹ Previous request was cancelled."

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
                # Poll for result with cancel checking every 1s
                import time as _time
                _elapsed = 0
                _max_wait = 60
                while _elapsed < _max_wait:
                    try:
                        response, info = fut.result(timeout=1)
                        break
                    except TimeoutError:
                        _elapsed += 1
                        if self._cancel_event.is_set():
                            fut.cancel()
                            return "⏹ Cancelled."
                else:
                    # Timeout after 60s without result
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
            "/stop — Stop current processing and cancel\n"
            "/restart — Restart BAW engine\n"
            "/btw `<text>` — Quick answer (no court)\n"
            "/model `<name>` — Switch model (deepseek / kimi / minimax)\n"
            "/models — List available models\n"
            "/mode `quick|hybrid|tight` — Switch execution mode\n"
            "/tone `<profile>` — Switch tone\n"
            "/status — BAW system status\n"
            "/court — Show last Angel/Devil verdict\n"
            "/memory `<text>` — Save a memory\n"
            "/search `<query>` — Search memories\n"
            "/board — Generate HTML dashboard\n"
            "/version — BAW version\n"
            "/help — This message\n\n"
            "**Examples:**\n"
            "• list files in current directory\n"
            "• check disk space\n"
            "• /btw What time is it?\n"
            "• /tone teaching\n"
            "• /model kimi-k2.6"
        )
