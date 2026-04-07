"""
Sureline — TTS Module

Provider abstraction for Text-to-Speech.

Primary:  Sarvam AI  (bulbul:v3 via Pipecat SarvamTTSService)
Backup:   ElevenLabs (toggle via TTS_PROVIDER=elevenlabs in .env)
Fallback: Mock TTS   (for dev without any API key)

Toggle:
    .env  →  TTS_PROVIDER=sarvam      → uses Sarvam  (default)
    .env  →  TTS_PROVIDER=elevenlabs  → uses ElevenLabs

See pipecat-sarvam-handshake.md for Sarvam integration notes.
"""

import logging
from typing import Any

from sureline.config import (
    TTS_PROVIDER,
    SARVAM_API_KEY, SARVAM_TTS_MODEL, SARVAM_TTS_VOICE,
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
    has_sarvam_key, has_elevenlabs_key,
)

logger = logging.getLogger(__name__)


def create_tts_service() -> Any:
    """
    Create the appropriate TTS service based on TTS_PROVIDER toggle
    and available API keys.

    Resolution order:
      1. If TTS_PROVIDER=elevenlabs AND ElevenLabs key exists → ElevenLabs
      2. If Sarvam key exists → Sarvam (default)
      3. If ElevenLabs key exists (as silent fallback) → ElevenLabs
      4. Mock TTS
    """
    provider = TTS_PROVIDER.strip().lower()

    if provider == "elevenlabs":
        if has_elevenlabs_key():
            logger.info("TTS_PROVIDER=elevenlabs: using ElevenLabs TTS")
            return _create_elevenlabs_tts()
        else:
            logger.warning(
                "TTS_PROVIDER=elevenlabs but ELEVENLABS_API_KEY is not set. "
                "Falling back to Sarvam."
            )

    if has_sarvam_key():
        logger.info(f"Creating Sarvam TTS service (model={SARVAM_TTS_MODEL}, voice={SARVAM_TTS_VOICE})")
        return _create_sarvam_tts()

    if has_elevenlabs_key():
        logger.info("No Sarvam key found. Using ElevenLabs as TTS fallback.")
        return _create_elevenlabs_tts()

    logger.info("No TTS API keys found. Using mock TTS.")
    return _create_mock_tts()


def _create_sarvam_tts() -> Any:
    try:
        from pipecat.services.sarvam import SarvamTTSService
        return SarvamTTSService(
            api_key=SARVAM_API_KEY,
            settings=SarvamTTSService.Settings(
                model=SARVAM_TTS_MODEL,   # bulbul:v3
                voice=SARVAM_TTS_VOICE,   # shubh (default for v3)
                # bulbul:v3 adds temperature but drops pitch/loudness (v2 only)
                # enable_preprocessing=True helps with numbers, abbreviations
                enable_preprocessing=True,
            ),
        )
    except ImportError:
        logger.warning(
            "pipecat-ai[sarvam] not installed. "
            "Run: pip install \"pipecat-ai[sarvam]\" sarvamai>=0.1.25"
        )
        return _create_mock_tts()


def _create_elevenlabs_tts() -> Any:
    try:
        # pipecat 0.0.105+: use the .tts submodule (top-level is deprecated)
        try:
            from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        except ImportError:
            from pipecat.services.elevenlabs import ElevenLabsTTSService
        return ElevenLabsTTSService(
            api_key=ELEVENLABS_API_KEY,
            voice_id=ELEVENLABS_VOICE_ID or "JBFqnCBsd6RMkjVDRZzb",
        )
    except ImportError:
        logger.warning(
            "pipecat-ai[elevenlabs] not installed. "
            "Run: pip install \"pipecat-ai[elevenlabs]\""
        )
        return _create_mock_tts()


def _create_mock_tts() -> Any:
    from sureline.tts.mock_tts import MockTTSService
    return MockTTSService()
