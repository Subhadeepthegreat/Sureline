"""
Sureline — STT Module

Primary:  Sarvam AI  (saaras:v3 via pipecat.services.sarvam.stt.SarvamSTTService)
Fallback: Mock STT   (for dev without API key)

Pipecat note: SarvamSTTService lives in the .stt submodule, not the top-level
pipecat.services.sarvam package. Requires sarvamai>=0.1.25 installed separately
(the pipecat[sarvam] extra may pin an older version).
"""

import logging
from typing import Any

from sureline.config import SARVAM_API_KEY, SARVAM_STT_MODEL, has_sarvam_key

logger = logging.getLogger(__name__)


def create_stt_service() -> Any:
    """
    Create the Sarvam STT service, or mock if no key is available.
    Returns a Pipecat-compatible STT service instance.
    """
    if has_sarvam_key():
        logger.info(f"Creating Sarvam STT service (model={SARVAM_STT_MODEL})")
        try:
            # SarvamSTTService is in the .stt submodule — not re-exported by __init__
            from pipecat.services.sarvam.stt import SarvamSTTService
            return SarvamSTTService(
                api_key=SARVAM_API_KEY,
                # model and vad_signals go via Settings
                settings=SarvamSTTService.Settings(
                    model=SARVAM_STT_MODEL,  # "saaras:v3"
                    vad_signals=False,       # Pipecat's Silero VAD drives turn detection
                ),
                # mode is a direct constructor param (not in Settings)
                mode="transcribe",
            )
        except ImportError as e:
            logger.warning(
                f"Could not import SarvamSTTService: {e}. "
                "Run: pip install \"pipecat-ai[sarvam]\" \"sarvamai>=0.1.25\""
            )
            return _create_mock_stt()
    else:
        logger.info("No SARVAM_API_KEY found. Using mock STT.")
        return _create_mock_stt()


def _create_mock_stt():
    from sureline.stt.mock_stt import MockSTTService
    return MockSTTService()
