"""
Sureline Web Server
===================
HTTP :  http://127.0.0.1:8080  →  serves frontend/index.html
WS   :  ws://127.0.0.1:8765    →  pushes pipeline state events to the browser

Message schema (server → browser):
  {type: 'connected'}
  {type: 'session_start', client: str}
  {type: 'state',      state:  'idle'|'listening'|'processing'|'speaking'|'error'}
  {type: 'transcript', text:   str}   ← user speech (what was heard)
  {type: 'response',   text:   str}   ← first chunk of agent reply
  {type: 'status',     text:   str}   ← override status label mid-state
  {type: 'latency',    value:  float} ← seconds, shown in chrome bottom
  {type: 'session_end'}
"""
import asyncio
import json
import logging
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import websockets
import websockets.exceptions

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent / "frontend"
HTTP_HOST    = "127.0.0.1"
HTTP_PORT    = 8080
WS_HOST      = "127.0.0.1"
WS_PORT      = 8765

# ── Client registry ───────────────────────────────────────────────────────────

_clients: set = set()

async def broadcast(msg: dict) -> None:
    """Push a JSON event to every connected browser tab. Never raises."""
    if not _clients:
        return
    data = json.dumps(msg)
    await asyncio.gather(
        *(c.send(data) for c in list(_clients)),
        return_exceptions=True,
    )

# ── WebSocket handler ─────────────────────────────────────────────────────────

def _config_msg() -> dict:
    """Build the config message from the active .env settings."""
    from sureline import config as cfg
    llm_map = {
        "azure":  f"Azure OpenAI ({cfg.AZURE_OPENAI_MODEL})",
        "openai": f"OpenAI ({cfg.OPENAI_MODEL})",
        "gemini": f"Gemini ({cfg.GEMINI_MODEL})",
        "ollama": f"Ollama ({cfg.OLLAMA_MODEL})",
        "auto":   (
            f"Azure OpenAI ({cfg.AZURE_OPENAI_MODEL})" if cfg.AZURE_OPENAI_API_KEY else
            f"OpenAI ({cfg.OPENAI_MODEL})"             if cfg.OPENAI_API_KEY        else
            f"Gemini ({cfg.GEMINI_MODEL})"             if cfg.GEMINI_API_KEY        else
            f"Ollama ({cfg.OLLAMA_MODEL})"
        ),
    }
    tts_map = {
        "sarvam":     f"Sarvam ({cfg.SARVAM_TTS_MODEL})",
        "elevenlabs": "ElevenLabs",
    }
    return {
        "type": "config",
        "llm": llm_map.get(cfg.LLM_PROVIDER, cfg.LLM_PROVIDER),
        "stt": f"Sarvam ({cfg.SARVAM_STT_MODEL})" if cfg.SARVAM_API_KEY else "Mock STT",
        "tts": tts_map.get(cfg.TTS_PROVIDER, cfg.TTS_PROVIDER),
    }


async def _ws_handler(websocket) -> None:
    _clients.add(websocket)
    logger.info("[WS ] client connected  (%d total)", len(_clients))
    try:
        await websocket.send(json.dumps({"type": "connected"}))
        await websocket.send(json.dumps(_config_msg()))
        async for _ in websocket:
            pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _clients.discard(websocket)
        logger.info("[WS ] client disconnected (%d total)", len(_clients))

# ── HTTP server (static frontend/) ───────────────────────────────────────────

class _SilentHandler(SimpleHTTPRequestHandler):
    """Serve files from frontend/ without per-request stdout noise."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, *_):
        pass  # silence

    def translate_path(self, path: str) -> str:
        """Resolve URL path and reject anything outside FRONTEND_DIR."""
        resolved = super().translate_path(path)
        # Normalize both paths to prevent traversal via .. or symlinks
        resolved_abs = Path(resolved).resolve()
        frontend_abs = FRONTEND_DIR.resolve()
        if not str(resolved_abs).startswith(str(frontend_abs)):
            # Return a path that doesn't exist — triggers 404
            return str(frontend_abs / "__blocked__")
        return resolved

    def end_headers(self):
        # Allow the browser to fetch modules from esm.sh (Pretext CDN)
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


def start_http_server() -> None:
    """Start the HTTP server in a background daemon thread."""
    if not FRONTEND_DIR.exists():
        logger.warning("[HTTP] frontend/ directory not found — HTTP server skipped")
        return

    def _run():
        httpd = HTTPServer((HTTP_HOST, HTTP_PORT), _SilentHandler)
        logger.info("[HTTP] serving frontend/ at http://%s:%d", HTTP_HOST, HTTP_PORT)
        httpd.serve_forever()

    t = threading.Thread(target=_run, daemon=True, name="sureline-http")
    t.start()

# ── WebSocket server ──────────────────────────────────────────────────────────

async def start_ws_server():
    """
    Start the WebSocket broadcaster.
    Returns the server object so the caller can close it on shutdown.
    """
    server = await websockets.serve(_ws_handler, WS_HOST, WS_PORT)
    logger.info("[WS ] listening at ws://%s:%d", WS_HOST, WS_PORT)
    return server
