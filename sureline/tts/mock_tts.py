"""
Sureline — Mock TTS Service

A development-mode TTS that prints the response text to console
instead of generating actual audio. For testing the pipeline
without ElevenLabs API keys.
"""

import logging

logger = logging.getLogger(__name__)


class MockTTSService:
    """
    Mock TTS for development without API keys.

    Prints the text that would be spoken, so you can verify the
    conversation engine output during development.
    """

    def __init__(self):
        logger.info("MockTTSService initialized (text output only, no audio)")

    def synthesize(self, text: str) -> dict:
        """
        Mock synthesis — just prints the text.

        Args:
            text: Text that would be spoken.

        Returns:
            Mock result dict.
        """
        print(f"\n🔊 [MOCK TTS]: {text}\n")
        return {
            "audio": None,
            "text": text,
            "provider": "mock",
        }

    def __repr__(self):
        return "MockTTSService(development mode)"
