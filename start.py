"""
Sureline — One-command launcher.

    python start.py               → voice mode (mic + speakers)
    python start.py --text-mode   → keyboard mode (no audio)

Does automatically:
  1. Starts Ollama if it's not running
  2. Pulls qwen2.5:3b if not already downloaded
  3. Generates the Mahakash sample database if it doesn't exist
  4. Launches the voice pipeline
"""

import argparse
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Enable debug logging for libraries that use Loguru (e.g. pipecat)
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="DEBUG")

OLLAMA_URL = "http://localhost:11434"

# Pull model from env so it stays in sync with config.py
import os
from dotenv import load_dotenv as _ld
_ld(ROOT / ".env")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")


# ─── helpers ─────────────────────────────────────────────────────

def _say(msg, end="\n"):
    print(msg, end=end, flush=True)


def _ollama_running() -> bool:
    try:
        urllib.request.urlopen(OLLAMA_URL, timeout=2)
        return True
    except Exception:
        return False


def _start_ollama() -> bool:
    """Start ollama serve in a background window. Returns True when ready."""
    _say("   Ollama not running — starting it...", end="")
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["ollama", "serve"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except FileNotFoundError:
        _say("\nERROR: ollama not found. Download from https://ollama.com/download")
        return False

    for _ in range(30):
        time.sleep(1)
        _say(".", end="")
        if _ollama_running():
            _say(" ready.")
            return True

    _say("\nERROR: Ollama started but didn't respond within 30s.")
    return False


def _ensure_model(model: str) -> bool:
    """Pull the model if not already present. Returns True on success."""
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and model in result.stdout:
            _say(f"   {model} already downloaded.")
            return True
    except Exception:
        pass

    _say(f"   Pulling {model} — first time only, ~2 GB...")
    result = subprocess.run(["ollama", "pull", model], timeout=600)
    if result.returncode != 0:
        _say(f"ERROR: Failed to pull {model}.")
        return False
    _say(f"   {model} ready.")
    return True


def _ensure_database() -> None:
    """Seed the sample Mahakash database if it doesn't exist yet."""
    db_path = ROOT / "data" / "mahakash.db"
    if db_path.exists():
        _say("   Database already exists.")
        return
    _say("   Generating Mahakash sample database...")
    from data.seed_database import create_database
    create_database()


# ─── entry point ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sureline Voice Agent — one-command launcher")
    parser.add_argument(
        "--text-mode",
        action="store_true",
        help="Keyboard input instead of mic (useful for testing without audio)",
    )
    args = parser.parse_args()

    _say("\n" + "━" * 50)
    _say("  SURELINE  —  Enterprise Voice Agent")
    _say("━" * 50)

    # ── 1 & 2. Ollama — only needed if no cloud LLM is configured ──
    from sureline.config import has_cloud_llm_key
    _using_cloud = has_cloud_llm_key()
    if _using_cloud:
        _say("\n[1/2] Cloud LLM detected — skipping Ollama setup.")
    else:
        _say("\n[1/3] Checking Ollama...")
        if _ollama_running():
            _say("   Ollama already running.")
        elif not _start_ollama():
            sys.exit(1)

        _say(f"\n[2/3] Checking model ({MODEL})...")
        if not _ensure_model(MODEL):
            sys.exit(1)

    # ── Database ──────────────────────────────────────────────────
    _total = "2" if _using_cloud else "3"
    _say(f"\n[{_total}/{_total}] Checking database...")
    _ensure_database()

    # ── 4. Launch ─────────────────────────────────────────────────
    import asyncio
    import web_server
    from pipeline import run_voice_mode, run_text_mode

    _say("\n" + "━" * 50)
    if args.text_mode:
        _say("  MODE: Text  (type questions, press Enter)")
    else:
        _say("  MODE: Voice  (speak into your mic)")
        _say("  TTS:  Sarvam  (set TTS_PROVIDER=elevenlabs to switch)")
        _say("  Barge-in enabled — interrupt the agent any time")
    _say("━" * 50 + "\n")

    # ── 4a. Serve the frontend over HTTP ─────────────────────────
    web_server.start_http_server()
    _say(f"  UI:   http://127.0.0.1:{web_server.HTTP_PORT}")

    # ── 4b. Open the browser after HTTP server has had a moment to bind ─
    threading.Thread(
        target=lambda: (time.sleep(1.5), webbrowser.open(f"http://127.0.0.1:{web_server.HTTP_PORT}")),
        daemon=True,
    ).start()

    # ── 4c. Run WS broadcaster + pipeline in the same event loop ─
    async def _run_all():
        ws_server = await web_server.start_ws_server()
        try:
            if args.text_mode:
                await run_text_mode()
            else:
                await run_voice_mode()
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
