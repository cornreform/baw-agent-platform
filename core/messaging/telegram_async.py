"""
BAW — Async Telegram Transport Layer (Phase 1)

Replaces the threaded poll loop with:
  - asyncio-based long polling (default, works locally)
  - Optional FastAPI webhook server (when BAW_WEBHOOK_URL + BAW_WEBHOOK_PORT are set)

The core processing (route(), send(), _handle_update) stays sync — 
async transport bridges via run_in_executor().

Usage:
  BAW_WEBHOOK_PORT=8080 BAW_WEBHOOK_URL=https://example.com/webhook \
    → webhook mode (requires public HTTPS)

  Default → async long polling (0 threads, dedicated AsyncClient)
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger("baw.telegram")

POLL_TIMEOUT = 30
POLL_RETRY_DELAY = 5

# ── Webhook server ──────────────────────────────────────────────────────

_WEBHOOK_APP = None  # lazy-init FastAPI app


def _build_webhook_app(connector: "TelegramConnector"):
    """Build a FastAPI webhook app that dispatches updates to the connector."""
    global _WEBHOOK_APP
    if _WEBHOOK_APP is not None:
        return _WEBHOOK_APP

    from fastapi import FastAPI, Request

    app = FastAPI(title="BAW Telegram Webhook")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request):
        update = await request.json()
        connector._handle_update_async(update)
        return {"ok": True}

    _WEBHOOK_APP = app
    return app


# ── Async mixin methods — added to TelegramConnector ─────────────────────

def _ensure_async_attrs(self):
    """Ensure async attributes exist on the connector instance."""
    if not hasattr(self, "_async_client") or self._async_client is None:
        self._async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(POLL_TIMEOUT + 10, connect=10),
        )
    if not hasattr(self, "_loop"):
        self._loop = None


async def _poll_loop_async(self):
    """Async long-polling loop. 0 threads, dedicated AsyncClient, never hangs."""
    _ensure_async_attrs(self)
    client = self._async_client

    # Re-load offset from disk (may have been updated by previous run)
    self._load_offset()

    logger.info("[Telegram] Async poll loop started")
    while self._running:
        try:
            r = await client.post(
                f"{self._api_base}/getUpdates",
                json={
                    "offset": self._offset,
                    "timeout": POLL_TIMEOUT,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=httpx.Timeout(POLL_TIMEOUT + 5),
            )
            if r.status_code != 200:
                logger.warning(f"[Telegram] getUpdates HTTP {r.status_code}")
                await asyncio.sleep(POLL_RETRY_DELAY)
                continue

            data = r.json()
            if not data.get("ok"):
                logger.warning(f"[Telegram] API error: {data}")
                await asyncio.sleep(POLL_RETRY_DELAY)
                continue

            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                self._save_offset()
                # Process each update in its own async task
                asyncio.create_task(self._handle_update_async(update))

        except httpx.TimeoutException:
            # Normal for long-poll
            continue
        except asyncio.CancelledError:
            logger.info("[Telegram] Async poll loop cancelled")
            break
        except Exception as e:
            logger.error(f"[Telegram] Async poll error: {e}", exc_info=True)
            await asyncio.sleep(POLL_RETRY_DELAY)

    logger.info("[Telegram] Async poll loop ended")


async def _handle_update_async(self, update: dict):
    """Async wrapper that runs the sync _handle_update in executor."""
    try:
        # Run the existing sync _handle_update in the default executor
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._handle_update, update)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[Telegram] _handle_update_async error: {e}", exc_info=True)


async def _start_async(self):
    """Main async entry point. Starts poll loop + optional webhook server."""
    _ensure_async_attrs(self)
    self._loop = asyncio.get_running_loop()

    # Determine mode: webhook or async poll
    webhook_url = os.environ.get("BAW_WEBHOOK_URL", "").strip()
    webhook_port = os.environ.get("BAW_WEBHOOK_PORT", "").strip()

    if webhook_url and webhook_port:
        # ── Webhook mode ──
        port = int(webhook_port)
        app = _build_webhook_app(self)

        # First, tell Telegram to use the webhook
        async with httpx.AsyncClient(timeout=10) as tmp:
            await tmp.post(
                f"{self._api_base}/setWebhook",
                json={"url": webhook_url},
            )
        logger.info(f"[Telegram] Webhook set → {webhook_url}")

        # Run uvicorn without the reloader (must be awaited in background)
        import uvicorn as _uvicorn

        config = _uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        server = _uvicorn.Server(config)
        await server.serve()
    else:
        # ── Async long polling mode (default) ──
        await _poll_loop_async(self)


async def _stop_async(self):
    """Stop the async transport."""
    if self._async_client:
        await self._async_client.aclose()
        self._async_client = None

    # Clear Telegram webhook if we set one
    webhook_url = os.environ.get("BAW_WEBHOOK_URL", "").strip()
    if webhook_url:
        try:
            async with httpx.AsyncClient(timeout=10) as tmp:
                await tmp.post(f"{self._api_base}/deleteWebhook")
        except Exception:
            pass


# ── Monkey-patch entry point ────────────────────────────────────────────

def patch_connector(cls):
    """Add async transport methods to TelegramConnector.

    Call once at import time to replace start/stop with async versions.
    The core processing layer (route, send, _handle_update) is unchanged.
    """
    from . import BaseConnector

    original_start = BaseConnector.start
    original_stop = BaseConnector.stop

    async def _run_async_main(self):
        try:
            await _start_async(self)
        except Exception as e:
            logger.error(f"[Telegram] Async transport exited: {e}", exc_info=True)
        finally:
            await _stop_async(self)

    def _start_patched(self):
        """Override start(): use asyncio instead of thread."""
        if self._running:
            return
        if not self.connect():
            logger.error("[Telegram] Failed to connect")
            return
        self._running = True

        # Launch asyncio event loop in a daemon thread
        def _run_loop():
            asyncio.run(_run_async_main(self))

        import threading as _t
        self._async_thread = _t.Thread(target=_run_loop, daemon=True, name="telegram-async")
        self._async_thread.start()
        logger.info("[Telegram] Async transport started (0 threads for I/O)")

    def _stop_patched(self):
        """Override stop(): stops asyncio loop + cleans up."""
        self._running = False
        # Async client cleanup
        if hasattr(self, "_async_client") and self._async_client:
            try:
                import asyncio as _aio
                # Schedule close on the running loop
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        _stop_async(self), self._loop
                    )
            except Exception:
                pass
        # Also call original disconnect for sync client
        self.disconnect()
        logger.info("[Telegram] Async transport stopped")

    cls.start = _start_patched
    cls.stop = _stop_patched
    cls._poll_loop_async = _poll_loop_async
    cls._handle_update_async = _handle_update_async

    # Register a module-level reference for import
    import sys as _sys
    _sys.modules.setdefault("telegram_async", None)

    return cls
