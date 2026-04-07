"""
Sureline — Mock STT Service

A development-mode STT that allows testing the pipeline without
real speech-to-text API keys. It simply passes through text frames
or can accept typed input from the console.
"""

import logging

logger = logging.getLogger(__name__)


class MockSTTService:
    """
    Mock STT for development without API keys.

    In the Pipecat pipeline, this will be replaced by a real STT service.
    For standalone testing, you can call transcribe() directly.
    """

    def __init__(self):
        logger.info("MockSTTService initialized (no real transcription)")

    def transcribe(self, text: str) -> dict:
        """
        Pass-through transcription for development.

        Args:
            text: Pretend this is what the user said.

        Returns:
            Mock transcript dict.
        """
        return {
            "text": text,
            "is_final": True,
            "provider": "mock",
        }

    def __repr__(self):
        return "MockSTTService(development mode)"
