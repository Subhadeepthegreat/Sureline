"""
Sureline — Structured Logger

Sets up structured JSON logging for the entire application.
Logs all pipeline events from PRD §9 (observability).
"""

import logging
import sys
from typing import Optional

import structlog


def setup_logging(level: str = "INFO") -> None:
    """
    Configure structured JSON logging for the Sureline application.

    Args:
        level: Log level string ("DEBUG", "INFO", "WARNING", "ERROR").
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure standard logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a named structured logger."""
    return structlog.get_logger(name)


# Pipeline event loggers (from PRD §9)
class PipelineEventLogger:
    """Log pipeline events as defined in PRD §9 Observability."""

    def __init__(self):
        self._log = get_logger("sureline.pipeline")

    def pipeline_start(self, session_id: str) -> None:
        self._log.info("pipeline_start", session_id=session_id)

    def pipeline_end(self, session_id: str, total_ms: float) -> None:
        self._log.info("pipeline_end", session_id=session_id, total_ms=round(total_ms, 1))

    def stt_request_sent(self, session_id: str) -> None:
        self._log.info("stt_request_sent", session_id=session_id)

    def stt_transcript_received(self, session_id: str, text: str, latency_ms: float) -> None:
        self._log.info("stt_transcript_received", session_id=session_id,
                      text=text[:100], latency_ms=round(latency_ms, 1))

    def query_generated(self, session_id: str, query_type: str, query: str) -> None:
        self._log.info("query_generated", session_id=session_id,
                      query_type=query_type, query=query[:200])

    def query_executed(self, session_id: str, success: bool, latency_ms: float) -> None:
        self._log.info("query_executed", session_id=session_id,
                      success=success, latency_ms=round(latency_ms, 1))

    def query_result_received(self, session_id: str, row_count: int) -> None:
        self._log.info("query_result_received", session_id=session_id, row_count=row_count)

    def llm_prompt_sent(self, session_id: str) -> None:
        self._log.info("llm_prompt_sent", session_id=session_id)

    def llm_response_received(self, session_id: str, response_length: int, latency_ms: float) -> None:
        self._log.info("llm_response_received", session_id=session_id,
                      response_length=response_length, latency_ms=round(latency_ms, 1))

    def tts_request_sent(self, session_id: str, text_length: int) -> None:
        self._log.info("tts_request_sent", session_id=session_id, text_length=text_length)

    def tts_audio_streamed(self, session_id: str, latency_ms: float) -> None:
        self._log.info("tts_audio_streamed", session_id=session_id, latency_ms=round(latency_ms, 1))

    def barge_in_detected(self, session_id: str) -> None:
        self._log.info("barge_in_detected", session_id=session_id)

    def error_occurred(self, session_id: str, component: str, error: str) -> None:
        self._log.error("error_occurred", session_id=session_id,
                       component=component, error=error)

    def retry_attempted(self, session_id: str, component: str, attempt: int) -> None:
        self._log.warning("retry_attempted", session_id=session_id,
                         component=component, attempt=attempt)
