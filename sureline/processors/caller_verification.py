"""
Sureline — Caller Verification Processor

Pipecat FrameProcessor that verifies the caller's identity before
allowing questions to reach the LLM.

Verification methods (configured per client in YAML):
  pin  — caller speaks their PIN; matched against a DB column
  ani  — caller ID auto-matched from inbound call metadata
  otp  — OTP sent via SMS; caller reads it back (Phase 2, requires SMS provider)

On success: passes the frame downstream.
On failure: pushes a TextFrame with the fallback message and stops.
On DB error: invokes the fallback handler rather than crashing.
"""

import asyncio
import logging
import re
import sqlite3
from typing import Optional

_SAFE_COLUMN_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

from pipecat.frames.frames import TextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from sureline.schema_registry import ClientConfig

logger = logging.getLogger(__name__)


class CallerVerificationProcessor(FrameProcessor):
    """
    Sits at the front of the pipeline, before SurelineContextProcessor.

    State machine:
      WAITING_PIN  → emits prompt, waits for caller to speak PIN
      VERIFIED     → passes all frames downstream
      FAILED       → emits fallback message, drops all subsequent frames
    """

    _STATE_WAITING = "waiting_pin"
    _STATE_VERIFIED = "verified"
    _STATE_FAILED = "failed"

    def __init__(self, client_config: ClientConfig):
        super().__init__()
        self._config = client_config
        self._state = self._STATE_WAITING
        self._db_path = client_config.database_path
        self._fallback_msg = client_config.fallback.message

        field = client_config.caller_verification.field
        if not _SAFE_COLUMN_RE.match(field):
            raise ValueError(
                f"caller_verification.field '{field}' is not a valid SQL column name. "
                "Must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
            )
        self._verify_field = field

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)

        if self._state == self._STATE_VERIFIED:
            await self.push_frame(frame, direction)
            return

        if self._state == self._STATE_FAILED:
            # Drop everything — fallback already spoken
            return

        # Waiting for PIN — prompt on first non-transcription frame
        if not isinstance(frame, (TranscriptionFrame, TextFrame)):
            await self.push_frame(frame, direction)
            return

        spoken = frame.text.strip() if hasattr(frame, "text") else ""

        if not spoken:
            await self.push_frame(frame, direction)
            return

        # First speech from caller — treat as PIN
        verified = await self._verify_pin(spoken)

        if verified:
            self._state = self._STATE_VERIFIED
            await self.push_frame(TextFrame(text="Thank you, you're verified. How can I help you?"))
        else:
            self._state = self._STATE_FAILED
            await self.push_frame(TextFrame(text=self._fallback_msg))
            logger.warning("Caller verification failed (input length: %d)", len(spoken))

    async def _verify_pin(self, spoken_pin: str) -> bool:
        """Check spoken PIN against the verification field in the DB."""
        return await asyncio.to_thread(self._check_db, spoken_pin)

    def _check_db(self, spoken_pin: str) -> bool:
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT 1 FROM customers WHERE {self._verify_field} = ? LIMIT 1",
                (spoken_pin.strip(),),
            )
            result = cursor.fetchone()
            conn.close()
            return result is not None
        except sqlite3.OperationalError as exc:
            logger.error(
                "DB error during caller verification (field=%s): %s",
                self._verify_field, exc,
            )
            # DB unreachable — fail safe: deny rather than bypass verification
            return False
