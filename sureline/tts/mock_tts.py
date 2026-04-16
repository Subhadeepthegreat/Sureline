"""
Sureline — Mock TTS Service

A development-mode TTS that prints the response text to console
instead of generating actual audio. Used when no TTS API key is set.

Must be a Pipecat FrameProcessor so it can be inserted in the pipeline.
"""

import logging

from pipecat.frames.frames import TextFrame, TTSSpeakFrame, LLMFullResponseEndFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class MockTTSService(FrameProcessor):
    """
    Mock TTS for development without API keys.

    Sits in the pipeline like a real TTS service. Prints any text it
    would speak, passes all frames downstream. No audio is generated.
    """

    def __init__(self):
        super().__init__()
        logger.info("MockTTSService initialized (text output only — no audio hardware)")

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSSpeakFrame):
            print(f"\n[MOCK TTS] {frame.text}\n", flush=True)
        elif isinstance(frame, TextFrame) and not isinstance(frame, TranscriptionFrame):
            print(frame.text, end="", flush=True)
        elif isinstance(frame, LLMFullResponseEndFrame):
            print(flush=True)

        await self.push_frame(frame, direction)

    def __repr__(self):
        return "MockTTSService(dev mode — no audio)"
