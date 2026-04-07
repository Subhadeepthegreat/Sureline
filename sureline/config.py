"""
Sureline — Central Configuration

Loads all settings from .env and provides typed access throughout the app.
"""

import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

_log = logging.getLogger("sureline.config")


# ─── STT — Sarvam (primary) ──────────────────────────────────────
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
SARVAM_STT_MODEL: str = os.getenv("SARVAM_STT_MODEL", "saaras:v3")

# ─── TTS — Sarvam (primary) + ElevenLabs (backup) ───────────────
# Toggle: set TTS_PROVIDER=elevenlabs in .env to force ElevenLabs
TTS_PROVIDER: str = os.getenv("TTS_PROVIDER", "sarvam")  # "sarvam" | "elevenlabs"
SARVAM_TTS_MODEL: str = os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")
SARVAM_TTS_VOICE: str = os.getenv("SARVAM_TTS_VOICE", "shubh")

ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "")

# ─── LLM Provider Toggle ────────────────────────────────────────
# "auto" → tries Azure → OpenAI → Gemini → Ollama (dev/trial only)
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "auto")

# ─── LLM Priority 1: Azure OpenAI ───────────────────────────────
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_MODEL: str = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o-mini")

# ─── LLM Priority 2: OpenAI ─────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ─── LLM Priority 3: Gemini (OpenAI-compatible endpoint) ────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

# ─── LLM Priority 4: Ollama (local, dev/trial only) ─────────────
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# qwen2.5:1.5b — 1 GB, ultra-fast on CPU, reliable tool calling (SQL/Pandas)
# Alternatives already in Ollama registry: qwen2.5:3b (bigger/slower)
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")

# ─── Data paths ──────────────────────────────────────────────────
DATA_DIR: Path = PROJECT_ROOT / "data"
DOCS_DIR: Path = PROJECT_ROOT / "docs"
DB_PATH: Path = DATA_DIR / "mahakash.db"
CHROMA_DIR: Path = PROJECT_ROOT / "chroma_db"

# ─── Logging ─────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ─── Pipeline constraints (from PRD) ────────────────────────────
LATENCY_TARGETS = {
    "total_pipeline": 2.0,    # seconds
    "stt": 0.5,
    "llm_inference": 0.8,
    "tts_ttfb": 0.3,
}

QUERY_TIMEOUT: int = 5  # seconds — sandboxed query execution


def has_sarvam_key() -> bool:
    """Check if Sarvam API key is configured (used for both STT and TTS)."""
    return bool(SARVAM_API_KEY)


def has_stt_key() -> bool:
    """Check if any STT key is available."""
    return has_sarvam_key()


def has_tts_key() -> bool:
    """Check if any TTS key is available (Sarvam or ElevenLabs)."""
    return has_sarvam_key() or bool(ELEVENLABS_API_KEY)


def has_elevenlabs_key() -> bool:
    """Check if ElevenLabs API key is configured."""
    return bool(ELEVENLABS_API_KEY)


def create_llm_client():
    """
    Return an openai.AsyncClient wired to the active LLM provider.

    Priority (auto mode): Azure OpenAI → OpenAI → Gemini → Ollama.
    Ollama is dev/trial only — logs a warning if reached.

    Used by QueryEngine and ConversationEngine for direct (non-streaming)
    tool-calling calls. Not the same as create_llm_service() in pipeline.py,
    which returns a Pipecat streaming FrameProcessor.
    """
    import openai

    if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT:
        _log.info("LLM provider: Azure OpenAI (%s)", AZURE_OPENAI_MODEL)
        return openai.AsyncOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            base_url=f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_OPENAI_MODEL}/",
            default_headers={"api-version": "2024-02-01"},
        ), AZURE_OPENAI_MODEL

    if OPENAI_API_KEY:
        _log.info("LLM provider: OpenAI (%s)", OPENAI_MODEL)
        return openai.AsyncOpenAI(api_key=OPENAI_API_KEY), OPENAI_MODEL

    if GEMINI_API_KEY:
        _log.info("LLM provider: Gemini (%s)", GEMINI_MODEL)
        return openai.AsyncOpenAI(
            api_key=GEMINI_API_KEY,
            base_url=GEMINI_BASE_URL,
        ), GEMINI_MODEL

    _log.warning(
        "No cloud LLM key found — falling back to Ollama (%s). "
        "Production deployments should never reach this fallback.",
        OLLAMA_MODEL,
    )
    return openai.AsyncOpenAI(
        api_key="ollama",
        base_url=OLLAMA_BASE_URL + "/v1",
    ), OLLAMA_MODEL
