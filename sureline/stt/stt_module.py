"""
Sureline — STT Module

Primary:  Sarvam AI  (saaras:v3 via pipecat.services.sarvam.stt.SarvamSTTService)
Fallback: Mock STT   (for dev without API key)

Pipecat note: SarvamSTTService lives in the .stt submodule, not the top-level
pipecat.services.sarvam package. Requires sarvamai>=0.1.25 installed separately
(the pipecat[sarvam] extra may pin an older version).
"""

import asyncio
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
            # _ResilientSarvamSTTService is None when sarvamai isn't installed;
            # in that case fall through to the except block via an explicit raise.
            if _ResilientSarvamSTTService is None:
                raise ImportError("sarvamai SDK not available")
            return _ResilientSarvamSTTService(
                api_key=SARVAM_API_KEY,
                # model and vad_signals go via Settings
                settings=SarvamSTTService.Settings(
                    model=SARVAM_STT_MODEL,  # "saaras:v3"
                    vad_signals=False,       # Pipecat's Silero VAD drives turn detection
                ),
                # mode is a direct constructor param (not in Settings)
                mode="transcribe",
            )
        except Exception as e:
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


def _make_resilient_class():
    """
    Build _ResilientSarvamSTTService only if SarvamSTTService can be imported.
    Deferred so the module loads fine even without sarvamai installed.
    """
    try:
        from pipecat.frames.frames import ErrorFrame
        from pipecat.services.sarvam.stt import SarvamSTTService

        class ResilientSarvamSTTService(SarvamSTTService):
            """
            SarvamSTTService with dead-socket circuit breaker.

            Root cause of the error-loop:
              When the Sarvam WS drops mid-session, _socket_client stays
              non-None (it's only nulled in _disconnect()). Every subsequent
              100ms audio frame calls run_stt(), hits the live-socket path,
              tries to send on the dead socket, and logs an error — tight loop.

            Fix:
              Override run_stt(). If we get an "Error sending audio to Sarvam"
              ErrorFrame from the parent, we immediately null _socket_client so
              the guard fires on the next frame, then schedule a reconnect after
              a short back-off delay. Subsequent audio frames are silently dropped
              until the reconnect completes.
            """

            _RECONNECT_DELAY = 2.0  # seconds before attempting reconnect

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._reconnecting = False  # guards against multiple concurrent _connect()

            async def run_stt(self, audio: bytes):
                async for frame in super().run_stt(audio):
                    if (
                        isinstance(frame, ErrorFrame)
                        and frame.error
                        and "sending audio to Sarvam" in frame.error
                    ):
                        if self._reconnecting:
                            # Reconnect already scheduled — drop frame silently.
                            continue
                        # Dead socket detected — null it immediately so the
                        # guard at the top of run_stt() fires on subsequent frames.
                        self._socket_client = None
                        self._websocket_context = None
                        self._reconnecting = True
                        logger.warning(
                            "[STT] Sarvam WS connection lost — reconnecting in "
                            f"{self._RECONNECT_DELAY:.0f}s…"
                        )
                        try:
                            _loop = asyncio.get_running_loop()

                            async def _reconnect_and_clear():
                                try:
                                    await self._connect()
                                finally:
                                    self._reconnecting = False

                            _loop.call_later(
                                self._RECONNECT_DELAY,
                                lambda l=_loop: l.create_task(_reconnect_and_clear()),
                            )
                        except RuntimeError:
                            self._reconnecting = False  # no running loop in test context
                        # Suppress the error frame — no need to cascade noise
                        # to the pipeline; the logger warning is enough.
                    else:
                        yield frame

        return ResilientSarvamSTTService

    except Exception as e:
        # Catch both ImportError and the plain Exception that pipecat's sarvam
        # module raises when sarvamai is missing ("Missing module: ...").
        # Only suppress expected import-time failures — log anything unexpected.
        if not any(kw in str(e).lower() for kw in ("module", "import", "sarvamai", "missing")):
            logger.warning("_make_resilient_class: unexpected error: %s", e)
        return None


# Attempt to build the resilient class at import time.
# If sarvamai isn't installed this returns None and create_stt_service()
# will fall back to mock STT before ever reaching this class.
_ResilientSarvamSTTService = _make_resilient_class()
