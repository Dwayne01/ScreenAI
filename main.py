"""
ScreenAI — entry point.

Starts:
  1. FastAPI/uvicorn server(s) in a background thread, sharing ONE asyncio event loop.
     Both HTTP and HTTPS run via asyncio.gather so all WebSocket connections share
     the same loop — fixing the cross-loop send bug (BUG-01).
  2. pynput global hotkey listener in the main thread.

Thread boundary: pynput callbacks are synchronous. They use
asyncio.run_coroutine_threadsafe() to schedule async work on the server loop.
"""

import asyncio
import logging
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn

import server as srv
from capture import ScreenCapture, capture_screenshot
from ai_service import AIService, get_ai_service
from config import AI_PROVIDER, HOST, HOTKEY, HTTPS_PORT, PORT
from hotkey import start_hotkey_listener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# State shared between threads (written once at startup, then read-only except _last_screenshot)
_loop: Optional[asyncio.AbstractEventLoop] = None
_ai_service: Optional[AIService] = None
_last_screenshot: Optional[ScreenCapture] = None


# ---------------------------------------------------------------------------
# Core async handlers (run inside the uvicorn event loop)
# ---------------------------------------------------------------------------

async def _handle_capture(extra_prompt: Optional[str] = None) -> None:
    """Capture screen, call AI, broadcast result to all WebSocket clients."""
    global _last_screenshot
    ts = datetime.now().isoformat()

    await srv.manager.broadcast({"type": "processing", "timestamp": ts})
    try:
        cap = capture_screenshot()
        _last_screenshot = cap

        loop = asyncio.get_running_loop()
        response_text = await loop.run_in_executor(
            None, _ai_service.analyze_screenshot, cap.raw_png, extra_prompt
        )

        await srv.manager.broadcast({
            "type": "capture",
            "timestamp": ts,
            "screenshot": cap.display_b64,
            "response": response_text,
            "provider": AI_PROVIDER,
            "dimensions": {"width": cap.width, "height": cap.height},
        })
        logger.info("Broadcast complete — %d client(s)", len(srv.manager.active_connections))

    except Exception as exc:
        logger.error("Capture failed: %s", exc, exc_info=True)
        await srv.manager.broadcast({
            "type": "error",
            "timestamp": ts,
            "message": str(exc),
        })


async def handle_chat(text: str, include_screenshot: bool = False) -> None:
    """Handle a text chat message from the browser, optionally with last screenshot."""
    ts = datetime.now().isoformat()
    await srv.manager.broadcast({"type": "thinking", "timestamp": ts})
    try:
        image_png: Optional[bytes] = None
        if include_screenshot and _last_screenshot:
            image_png = _last_screenshot.raw_png

        loop = asyncio.get_running_loop()

        # Stream tokens to the client as they arrive
        full_text_parts: list[str] = []

        def on_token(chunk: str) -> None:
            full_text_parts.append(chunk)
            asyncio.run_coroutine_threadsafe(
                srv.manager.broadcast({
                    "type": "chat_stream",
                    "chunk": chunk,
                    "timestamp": ts,
                }),
                loop,
            )

        await loop.run_in_executor(
            None, lambda: _ai_service.stream_chat(text, image_png, on_token)
        )
        response_text = "".join(full_text_parts)

        await srv.manager.broadcast({
            "type": "chat_response",
            "timestamp": ts,
            "response": response_text,
            "provider": AI_PROVIDER,
        })
    except Exception as exc:
        logger.error("Chat failed: %s", exc, exc_info=True)
        await srv.manager.broadcast({
            "type": "error",
            "timestamp": ts,
            "message": str(exc),
        })


async def _analyze_interview(transcript: str) -> None:
    ts = datetime.now().isoformat()
    await srv.manager.broadcast({"type": "analyzing_interview", "timestamp": ts})
    try:
        loop = asyncio.get_running_loop()
        report = await loop.run_in_executor(None, _ai_service.analyze_interview, transcript)
        await srv.manager.broadcast({
            "type": "interview_analysis",
            "timestamp": ts,
            "report": report,
        })
        logger.info("Interview analysis broadcast complete")
    except Exception as exc:
        logger.error("Interview analysis failed: %s", exc, exc_info=True)
        await srv.manager.broadcast({
            "type": "error",
            "timestamp": ts,
            "message": f"Analysis failed: {exc}",
        })


async def _clear_chat() -> None:
    """Reset AI conversation history and notify all clients."""
    _ai_service.clear_history()
    await srv.manager.broadcast({
        "type": "cleared",
        "timestamp": datetime.now().isoformat(),
    })
    logger.info("Conversation history cleared")


# ---------------------------------------------------------------------------
# Hotkey callback (runs in pynput thread — must be synchronous)
# ---------------------------------------------------------------------------

def on_hotkey() -> None:
    if _loop is None or not _loop.is_running():
        logger.warning("Event loop not ready, ignoring hotkey press")
        return
    asyncio.run_coroutine_threadsafe(_handle_capture(), _loop)
    logger.info("Capture scheduled via hotkey")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _loop, _ai_service

    logger.info("Initializing AI service (%s)...", AI_PROVIDER)
    _ai_service = get_ai_service()

    # Wire up callbacks (eliminates circular import in server.py)
    srv.manual_capture_callback = _handle_capture
    srv.analyze_interview_callback = _analyze_interview
    srv.handle_chat_callback = handle_chat
    srv.clear_chat_callback = _clear_chat

    _loop = asyncio.new_event_loop()

    _ssl_dir = Path(__file__).parent
    _ssl_cert = _ssl_dir / "ssl_cert.pem"
    _ssl_key  = _ssl_dir / "ssl_key.pem"
    _has_ssl  = _ssl_cert.exists() and _ssl_key.exists()

    http_server = uvicorn.Server(
        uvicorn.Config(srv.app, host=HOST, port=PORT, log_level="warning", access_log=False)
    )
    https_server = uvicorn.Server(
        uvicorn.Config(
            srv.app, host=HOST, port=HTTPS_PORT,
            ssl_certfile=str(_ssl_cert) if _has_ssl else None,
            ssl_keyfile=str(_ssl_key) if _has_ssl else None,
            log_level="warning", access_log=False,
        )
    ) if _has_ssl else None

    async def _serve_all() -> None:
        """Run HTTP and HTTPS servers on the same event loop (fixes BUG-01)."""
        tasks = [http_server.serve()]
        if https_server:
            tasks.append(https_server.serve())
        await asyncio.gather(*tasks)

    def _run_servers() -> None:
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_serve_all())

    server_thread = threading.Thread(target=_run_servers, daemon=True, name="uvicorn")
    server_thread.start()

    # Wait for HTTP server to finish binding
    for _ in range(100):
        if http_server.started:
            break
        time.sleep(0.05)
    else:
        logger.critical("Server failed to start within 5 s — exiting")
        return

    try:
        _local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        _local_ip = "localhost"

    logger.info("=" * 52)
    logger.info("  ScreenAI ready")
    logger.info("  HTTP  : http://localhost:%d", PORT)
    if _has_ssl:
        logger.info("  HTTPS : https://%s:%d  (accept cert warning)", _local_ip, HTTPS_PORT)
    logger.info("  Hotkey    : %s", HOTKEY)
    logger.info("  AI        : %s", AI_PROVIDER)
    logger.info("=" * 52)

    try:
        start_hotkey_listener(on_hotkey, HOTKEY)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down...")
        http_server.should_exit = True
        if https_server:
            https_server.should_exit = True
        server_thread.join(timeout=3)


if __name__ == "__main__":
    main()
