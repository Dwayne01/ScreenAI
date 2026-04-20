"""FastAPI server: WebSocket hub, REST endpoints, audio transcription."""
import asyncio
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Optional

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from config import OPENAI_API_KEY

try:
    import openai as _openai_mod
except ImportError:
    _openai_mod = None  # type: ignore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="ScreenAI", docs_url=None, redoc_url=None)

# Injected by main.py after initialization
manual_capture_callback: Optional[Callable[[Optional[str]], Coroutine]] = None
analyze_interview_callback: Optional[Callable[[str], Coroutine]] = None
handle_chat_callback: Optional[Callable[[str, bool], Coroutine]] = None
clear_chat_callback: Optional[Callable[[], Coroutine]] = None

# Module-level OpenAI client — created once, reused for all requests
_openai_client = None

# Lock protecting listen_session.chunks to prevent the stop/chunk race
_listen_lock = asyncio.Lock()


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not configured")
        if _openai_mod is None:
            raise RuntimeError("openai package not installed — run: pip install openai")
        _openai_client = _openai_mod.OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


@dataclass
class ListenSession:
    active: bool = False
    chunks: list[str] = field(default_factory=list)
    last_chunk_tail: str = ""  # tail of previous chunk passed as Whisper context

    @property
    def full_transcript(self) -> str:
        return "\n\n".join(self.chunks)

    def reset(self) -> None:
        self.active = False
        self.chunks.clear()
        self.last_chunk_tail = ""


listen_session = ListenSession()


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("Client connected — total: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("Client disconnected — total: %d", len(self.active_connections))

    async def broadcast(self, data: dict) -> None:
        if not self.active_connections:
            return
        message = json.dumps(data)
        connections = list(self.active_connections)
        results = await asyncio.gather(
            *[ws.send_text(message) for ws in connections],
            return_exceptions=True,
        )
        dead = [ws for ws, result in zip(connections, results) if isinstance(result, Exception)]
        for ws in dead:
            logger.warning("Send failed, dropping client")
            self.disconnect(ws)

    async def send_to(self, websocket: WebSocket, data: dict) -> None:
        try:
            await websocket.send_text(json.dumps(data))
        except Exception as exc:
            logger.warning("send_to failed: %s", exc)
            self.disconnect(websocket)


manager = ConnectionManager()


@app.get("/")
async def serve_dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    await manager.send_to(websocket, {"type": "connected", "message": "ScreenAI connected"})
    try:
        while True:
            raw = await websocket.receive_text()
            if raw == "ping":
                await websocket.send_text("pong")
            else:
                try:
                    msg = json.loads(raw)
                    msg_type = msg.get("type")
                    if msg_type == "chat" and handle_chat_callback:
                        asyncio.create_task(
                            handle_chat_callback(msg.get("text", ""), msg.get("include_screenshot", False))
                        )
                    elif msg_type == "clear" and clear_chat_callback:
                        asyncio.create_task(clear_chat_callback())
                except (json.JSONDecodeError, KeyError):
                    pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        manager.disconnect(websocket)


@app.post("/capture")
async def trigger_capture() -> JSONResponse:
    if manual_capture_callback is None:
        return JSONResponse({"error": "Not initialized"}, status_code=503)
    asyncio.create_task(manual_capture_callback())
    return JSONResponse({"status": "triggered"})


@app.post("/clear")
async def clear_chat() -> JSONResponse:
    if clear_chat_callback is None:
        return JSONResponse({"error": "Not initialized"}, status_code=503)
    asyncio.create_task(clear_chat_callback())
    return JSONResponse({"status": "cleared"})


@app.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)) -> JSONResponse:
    """Transcribe uploaded audio using OpenAI Whisper."""
    if not OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY not set — Whisper unavailable"}, status_code=503)
    try:
        client = _get_openai_client()
        audio_bytes = await audio.read()
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = audio.filename or "recording.webm"
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(model="whisper-1", file=audio_file),
        )
        return JSONResponse({"text": result.text})
    except Exception as exc:
        logger.error("Transcription failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/listen/start")
async def listen_start() -> JSONResponse:
    # 409 if a session is already active — prevents silent reset
    if listen_session.active:
        return JSONResponse({"error": "A listen session is already active"}, status_code=409)
    listen_session.reset()
    listen_session.active = True
    await manager.broadcast({"type": "listen_started", "timestamp": datetime.now().isoformat()})
    return JSONResponse({"status": "started"})


@app.post("/listen/chunk")
async def listen_chunk(audio: UploadFile = File(...)) -> JSONResponse:
    async with _listen_lock:
        if not listen_session.active:
            return JSONResponse({"error": "No active listen session"}, status_code=400)

    if not OPENAI_API_KEY:
        return JSONResponse({"error": "OPENAI_API_KEY required for transcription"}, status_code=503)
    try:
        client = _get_openai_client()
        audio_bytes = await audio.read()
        if len(audio_bytes) < 2000:
            logger.debug("Chunk too small (%d bytes) — likely silence, skipping", len(audio_bytes))
            return JSONResponse({"text": "", "chunk_index": len(listen_session.chunks)})

        audio_file = io.BytesIO(audio_bytes)
        # Filename determines format Whisper uses; prefer webm, fall back to m4a for Safari
        ct = audio.content_type or ""
        audio_file.name = "chunk.m4a" if "mp4" in ct or "m4a" in ct else "chunk.webm"

        # Snapshot context tail before the async executor call
        prompt_context = listen_session.last_chunk_tail or None

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                prompt=prompt_context,
            ),
        )
        text = result.text.strip()

        async with _listen_lock:
            if text and listen_session.active:
                listen_session.chunks.append(text)
                listen_session.last_chunk_tail = text[-200:]
                chunk_index = len(listen_session.chunks)
                await manager.broadcast({
                    "type": "transcript_chunk",
                    "timestamp": datetime.now().isoformat(),
                    "text": text,
                    "chunk_index": chunk_index,
                })
                return JSONResponse({"text": text, "chunk_index": chunk_index})

        return JSONResponse({"text": text, "chunk_index": len(listen_session.chunks)})
    except Exception as exc:
        logger.error("Listen chunk failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/listen/stop")
async def listen_stop() -> JSONResponse:
    async with _listen_lock:
        if not listen_session.active:
            return JSONResponse({"error": "No active listen session"}, status_code=400)
        # Snapshot transcript inside lock to avoid missing an in-flight chunk
        transcript = listen_session.full_transcript
        chunk_count = len(listen_session.chunks)
        listen_session.active = False

    await manager.broadcast({
        "type": "listen_stopped",
        "timestamp": datetime.now().isoformat(),
        "chunk_count": chunk_count,
    })
    if not transcript.strip():
        await manager.broadcast({
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "message": "No transcript was recorded — nothing to analyze.",
        })
        return JSONResponse({"status": "stopped", "chunk_count": 0})
    if analyze_interview_callback:
        asyncio.create_task(analyze_interview_callback(transcript))
    return JSONResponse({"status": "analyzing", "chunk_count": chunk_count})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "clients": len(manager.active_connections)}
