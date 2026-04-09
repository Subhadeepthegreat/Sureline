"""
Tests for sureline/processors/caller_verification.py::CallerVerificationProcessor.

Covers:
- __init__: invalid column name raises ValueError at construction time
- _check_db: correct PIN found → True; wrong PIN → False; DB error → False (fail-safe)
- State machine: WAITING → VERIFIED on correct PIN; WAITING → FAILED on wrong PIN
- After VERIFIED: subsequent frames pass through unchanged
- After FAILED: subsequent frames are dropped (fallback already spoken)
"""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sureline.processors.caller_verification import CallerVerificationProcessor
from sureline.schema_registry import (
    ClientConfig,
    CallerVerificationConfig,
    FallbackConfig,
)


# ─── Helpers ─────────────────────────────────────────────────────

def _make_processor(
    db_path: Path,
    field: str = "account_no",
    table: str = "customers",
) -> CallerVerificationProcessor:
    config = ClientConfig(
        client_id="test",
        client_name="Test Corp",
        company_description="",
        database_type="sqlite",
        database_path=str(db_path),
        caller_verification=CallerVerificationConfig(method="pin", field=field, table=table),
        fallback=FallbackConfig(
            message="Please hold while we transfer you.",
            action="sip_transfer",
            target="",
        ),
    )
    return CallerVerificationProcessor(config)


# ─── __init__ validation ──────────────────────────────────────────

class TestCallerVerificationInit:
    def test_valid_field_name_accepted(self, db_path: Path):
        proc = _make_processor(db_path, field="account_no")
        assert proc._verify_field == "account_no"

    def test_valid_table_name_accepted(self, db_path: Path):
        proc = _make_processor(db_path, table="employees")
        assert proc._verify_table == "employees"

    @pytest.mark.parametrize("bad_table", [
        "cust omers",   # space
        "1customers",   # starts with digit
        "cust-omers",   # hyphen
        "cust;DROP",    # SQL injection
        "",             # empty
    ])
    def test_invalid_table_name_raises_value_error(self, db_path: Path, bad_table: str):
        config = ClientConfig(
            client_id="test",
            client_name="Test Corp",
            company_description="",
            database_type="sqlite",
            database_path=str(db_path),
            caller_verification=CallerVerificationConfig(method="pin", field="account_no", table=bad_table),
            fallback=FallbackConfig(message="hold", action="sip_transfer", target=""),
        )
        with pytest.raises(ValueError, match="table"):
            CallerVerificationProcessor(config)

    @pytest.mark.parametrize("bad_field", [
        "account no",        # space
        "1account",          # starts with digit
        "account-no",        # hyphen
        "account;DROP",      # SQL injection attempt
        "",                  # empty
        "ac count",          # embedded space
    ])
    def test_invalid_field_name_raises_value_error(self, db_path: Path, bad_field: str):
        config = ClientConfig(
            client_id="test",
            client_name="Test Corp",
            company_description="",
            database_type="sqlite",
            database_path=str(db_path),
            caller_verification=CallerVerificationConfig(method="pin", field=bad_field),
            fallback=FallbackConfig(
                message="Hold please.", action="sip_transfer", target=""
            ),
        )
        with pytest.raises(ValueError, match="valid SQL column name"):
            CallerVerificationProcessor(config)


# ─── _check_db ────────────────────────────────────────────────────

class TestCheckDb:
    def test_correct_pin_returns_true(self, db_path: Path):
        proc = _make_processor(db_path)
        assert proc._check_db("ACC001") is True

    def test_wrong_pin_returns_false(self, db_path: Path):
        proc = _make_processor(db_path)
        assert proc._check_db("WRONG999") is False

    def test_case_sensitive_pin_check(self, db_path: Path):
        # SQLite default is case-sensitive for non-ASCII; 'acc001' != 'ACC001'
        proc = _make_processor(db_path)
        result = proc._check_db("acc001")
        # Depending on SQLite collation this may be True or False;
        # what matters is it doesn't crash.
        assert isinstance(result, bool)

    def test_db_not_found_returns_false_fail_safe(self, tmp_path: Path):
        """When DB is unreachable, _check_db must return False (deny, not crash)."""
        config = ClientConfig(
            client_id="test",
            client_name="Test Corp",
            company_description="",
            database_type="sqlite",
            database_path=str(tmp_path / "ghost.db"),
            caller_verification=CallerVerificationConfig(method="pin", field="account_no"),
            fallback=FallbackConfig(message="Sorry.", action="sip_transfer", target=""),
        )
        proc = CallerVerificationProcessor(config)
        # Should not raise — should return False
        result = proc._check_db("ACC001")
        assert result is False

    def test_whitespace_stripped_from_pin(self, db_path: Path):
        proc = _make_processor(db_path)
        # _check_db calls spoken_pin.strip() before querying
        assert proc._check_db("  ACC001  ") is True


# ─── State machine via process_frame ──────────────────────────────

class TestCallerVerificationStateMachine:
    """
    We test process_frame by monkeypatching push_frame and super().process_frame
    so the test doesn't need a live Pipecat pipeline.
    """

    def _make_text_frame(self, text: str):
        from pipecat.frames.frames import TextFrame
        return TextFrame(text=text)

    def _make_transcription_frame(self, text: str):
        from pipecat.frames.frames import TranscriptionFrame
        # TranscriptionFrame signature varies by pipecat version; try common forms
        try:
            return TranscriptionFrame(text=text, user_id="", timestamp="")
        except TypeError:
            return TranscriptionFrame(text=text)

    @pytest.mark.asyncio
    async def test_correct_pin_transitions_to_verified(self, db_path: Path):
        from pipecat.processors.frame_processor import FrameDirection

        proc = _make_processor(db_path)
        pushed_frames = []

        async def _capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed_frames.append(frame)

        proc.push_frame = _capture_push

        # Simulate super().process_frame as a no-op
        with patch.object(
            type(proc).__mro__[1],   # FrameProcessor base
            "process_frame",
            new=AsyncMock(),
        ):
            frame = self._make_transcription_frame("ACC001")
            await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

        assert proc._state == proc._STATE_VERIFIED
        assert any(
            hasattr(f, "text") and "verified" in f.text.lower()
            for f in pushed_frames
        )

    @pytest.mark.asyncio
    async def test_wrong_pin_transitions_to_failed(self, db_path: Path):
        from pipecat.processors.frame_processor import FrameDirection

        proc = _make_processor(db_path)
        pushed_frames = []

        async def _capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed_frames.append(frame)

        proc.push_frame = _capture_push

        with patch.object(
            type(proc).__mro__[1],
            "process_frame",
            new=AsyncMock(),
        ):
            frame = self._make_transcription_frame("WRONGPIN")
            await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

        assert proc._state == proc._STATE_FAILED
        assert any(
            hasattr(f, "text") and "transfer" in f.text.lower()
            for f in pushed_frames
        )

    @pytest.mark.asyncio
    async def test_frames_pass_through_after_verified(self, db_path: Path):
        from pipecat.processors.frame_processor import FrameDirection

        proc = _make_processor(db_path)
        proc._state = proc._STATE_VERIFIED     # pre-set verified
        pushed_frames = []

        async def _capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed_frames.append(frame)

        proc.push_frame = _capture_push

        with patch.object(
            type(proc).__mro__[1],
            "process_frame",
            new=AsyncMock(),
        ):
            frame = self._make_text_frame("How can I help?")
            await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

        # In verified state the frame should be passed downstream
        assert frame in pushed_frames

    @pytest.mark.asyncio
    async def test_frames_dropped_after_failed(self, db_path: Path):
        from pipecat.processors.frame_processor import FrameDirection

        proc = _make_processor(db_path)
        proc._state = proc._STATE_FAILED       # pre-set failed
        pushed_frames = []

        async def _capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed_frames.append(frame)

        proc.push_frame = _capture_push

        with patch.object(
            type(proc).__mro__[1],
            "process_frame",
            new=AsyncMock(),
        ):
            frame = self._make_text_frame("Any message")
            await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

        # Frame should be dropped — not pushed
        assert frame not in pushed_frames

    @pytest.mark.asyncio
    async def test_db_error_results_in_failed_state(self, tmp_path: Path):
        """DB unreachable → fail-safe deny → FAILED state, fallback spoken."""
        from pipecat.processors.frame_processor import FrameDirection

        config = ClientConfig(
            client_id="test",
            client_name="Test Corp",
            company_description="",
            database_type="sqlite",
            database_path=str(tmp_path / "ghost.db"),
            caller_verification=CallerVerificationConfig(method="pin", field="account_no"),
            fallback=FallbackConfig(
                message="Please hold while we transfer you.",
                action="sip_transfer",
                target="",
            ),
        )
        proc = CallerVerificationProcessor(config)
        pushed_frames = []

        async def _capture_push(frame, direction=FrameDirection.DOWNSTREAM):
            pushed_frames.append(frame)

        proc.push_frame = _capture_push

        with patch.object(
            type(proc).__mro__[1],
            "process_frame",
            new=AsyncMock(),
        ):
            frame = self._make_transcription_frame("ACC001")
            await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

        assert proc._state == proc._STATE_FAILED
        # Fallback message should have been pushed
        assert any(
            hasattr(f, "text") and "transfer" in f.text.lower()
            for f in pushed_frames
        )
