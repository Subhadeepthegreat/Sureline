"""
Sureline — Pipecat Pipeline

Two modes:
  Voice mode (default)  :  python pipeline.py
  Text mode  (dev/test) :  python pipeline.py --text-mode

Voice pipeline (Pipecat 0.0.108):
  mic → VADProcessor → SarvamSTT → SurelineContextProcessor
      → OLLamaLLM → SarvamTTS → speakers

SurelineContextProcessor
  - Fires a background task per transcription (non-blocking — doesn't
    queue new speech behind a slow Ollama call)
  - Cancels the in-flight task if the user barges in
  - Prints a visible status line at every stage so you can see exactly
    what the pipeline is doing
"""

import argparse
import asyncio
import logging
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    BotSpeakingFrame,           # periodic heartbeat while TTS plays — the one actually seen in traces
    EndFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMMessagesFrame,           # triggers OpenAI generation — LLMMessagesUpdateFrame does NOT
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,              # standalone TTS utterance — manages its own context lifecycle
    VADUserStartedSpeakingFrame,  # VAD emits these; STT passes them through unchanged
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.openai.llm import OpenAILLMService

from sureline.conversation.conversation_engine import ConversationEngine
from sureline.config import (
    DB_PATH, PROJECT_ROOT, OLLAMA_MODEL,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL,
)
from sureline.query.sandbox import QueryResult
from sureline.schema_registry import SchemaRegistry
from sureline.stt.stt_module import create_stt_service
from sureline.tts.tts_module import create_tts_service

import re
import web_server  # local module — WebSocket state broadcaster


# ─── Fast non-data classifier ─────────────────────────────────────
# Before spending 3-5s on a query-engine API call, check if the turn
# is obviously conversational (greetings, chitchat, meta-questions).
# Any match here skips the SQL/RAG layer entirely → saves one round-trip.

_NON_DATA_RE = re.compile(
    # Short acknowledgements / reactions — must be the ENTIRE utterance (≤ 6 words).
    # Covers greetings, fillers, affirmations, and brief reactions that require
    # no data lookup — just a warm, brief conversational reply.
    r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening|night)|"
    r"thanks?|thank\s+you|okay|ok|sure|alright|great|got\s+it|"
    r"it\s+is|it\s+is[,\s]+it\s+is|interesting|fascinating|amazing|"
    r"wow|really|nice|cool|wonderful|excellent|perfect|awesome|"
    r"hm+|hmm+|uh+|ah+|oh+|i\s+see|i\s+understand|"
    r"that'?s?\s+(great|nice|cool|interesting|amazing|wonderful|good)|"
    r"right|indeed|absolutely|definitely|of\s+course)\s*[.!?,]*\s*$"
    r"|"
    # Meta-questions about the agent itself — anchored end-to-end.
    r"^\s*(tell\s+me\s+what\s+(you\s+)?(can\s+do|do)|"
    r"what\s+(can\s+you(\s+do)?|are\s+you\s+doing)|who\s+are\s+you|"
    r"what\s+do\s+you\s+do|introduce\s+yourself|about\s+yourself)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

def _is_social_turn(text: str) -> bool:
    """Return True for obvious greetings/reactions/chitchat — no SQL needed.

    Only matches explicitly-listed social patterns via regex. The previous
    word-count fallback (≤6 words, no ?) was removed because it incorrectly
    classified short factual queries like "Total revenue" or "Show orders"
    as social turns and silently skipped the SQL layer.
    """
    return bool(_NON_DATA_RE.match(text.strip()))


# ─── Duration-proportional echo cooldown ─────────────────────────
#
# Word-overlap echo detection (previously used as gate 3) creates false positives
# when the user legitimately repeats words from the bot's response ("We have a
# question about government projects" after bot mentions "government projects").
#
# Production voice agent systems (Vapi, Retell, LiveKit) avoid this by gating at the
# audio layer (WebRTC AEC). For local audio without AEC, the best software alternative
# is a duration-proportional cooldown: extend the post-TTS mute window based on how
# long the bot was actually speaking. This is proportional to TTS output length and
# has zero false positives on legitimate follow-up questions.
#
# Tuning:
#   _ECHO_COOLDOWN_BASE = minimum cooldown after TTS stops (handles STT pipeline lag)
#   _ECHO_COOLDOWN_RATIO = fraction of speaking duration added to the base cooldown
#   _ECHO_COOLDOWN_MAX = upper cap (long monologues don't lock the mic forever)

_ECHO_COOLDOWN_BASE  = 0.8   # seconds — minimum post-TTS mute (STT pipeline latency)
_ECHO_COOLDOWN_RATIO = 0.4   # 40% of speaking duration added as extra cooldown
_ECHO_COOLDOWN_MAX   = 3.0   # seconds — cap so long answers don't lock the mic


# ─── WS broadcast helper ──────────────────────────────────────────

def _ws_emit(msg: dict) -> None:
    """Fire-and-forget broadcast to all connected browser tabs.
    Safe to call from async context — creates a task, never blocks."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(web_server.broadcast(msg))
    except RuntimeError:
        pass  # no running loop (e.g. test context) — skip silently


# ─── LLM factory ─────────────────────────────────────────────────

def create_llm_service():
    """
    Return the appropriate Pipecat streaming LLM service.

    Priority (auto mode): Azure OpenAI → OpenAI → Gemini → Ollama.
    Mirrors the hierarchy in config.create_llm_client() but returns
    a Pipecat FrameProcessor (streaming) instead of openai.AsyncOpenAI.
    """
    if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT:
        status(f"[LLM        ] Using Azure OpenAI ({AZURE_OPENAI_MODEL})")
        return OpenAILLMService(
            api_key=AZURE_OPENAI_API_KEY,
            base_url=f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/{AZURE_OPENAI_MODEL}/",
            model=AZURE_OPENAI_MODEL,
        )
    if OPENAI_API_KEY:
        status(f"[LLM        ] Using OpenAI ({OPENAI_MODEL})")
        return OpenAILLMService(api_key=OPENAI_API_KEY, model=OPENAI_MODEL)
    if GEMINI_API_KEY:
        status(f"[LLM        ] Using Gemini ({GEMINI_MODEL}) via OpenAI-compat API")
        return OpenAILLMService(
            api_key=GEMINI_API_KEY,
            base_url=GEMINI_BASE_URL,
            model=GEMINI_MODEL,
        )
    status(f"[LLM        ] Using Ollama ({OLLAMA_MODEL}) locally (dev only)")
    return OLLamaLLMService(settings=OLLamaLLMService.Settings(model=OLLAMA_MODEL))

# Silence the noisy debug logs — only show warnings+ from pipecat internals
logging.basicConfig(level=logging.WARNING)
logging.getLogger("sureline").setLevel(logging.INFO)
logger = logging.getLogger(__name__)


# ─── Status printer ──────────────────────────────────────────────

def status(msg: str) -> None:
    """Single-line status visible above all other log noise."""
    print(f"\n>>> {msg}", flush=True)


# ─── Client config loader ─────────────────────────────────────────

def load_conversation_engine() -> ConversationEngine:
    """
    Load ConversationEngine from SchemaRegistry using CLIENT_ID env var.
    Falls back to default Mahakash config if CLIENT_ID is not set.
    """
    import os
    from pathlib import Path

    client_id = os.getenv("CLIENT_ID", "mahakash")
    clients_dir = PROJECT_ROOT / "clients"

    try:
        registry = SchemaRegistry(clients_dir=clients_dir)
        config = registry.load(client_id)
        status(f"[Config     ] Loaded client config: {config.client_name} ({client_id})")
        db_path = Path(config.database_path) if config.database_path else DB_PATH
        csv_path = db_path if config.database_type == "csv" else None
        return ConversationEngine(
            db_path=db_path if config.database_type == "sqlite" else None,
            csv_path=csv_path,
            client_name=config.client_name,
            company_description=config.company_description,
            client_id=client_id,
            filler_phrase=config.filler_phrase,
        )
    except FileNotFoundError:
        status(f"[Config     ] No YAML for '{client_id}' — using defaults (mahakash hardcodes)")
        return ConversationEngine(db_path=DB_PATH)


# ─── Core processor: RAG + SQL context injection ─────────────────

class SurelineContextProcessor(FrameProcessor):
    """
    Sits between Sarvam STT and the LLM.

    On each TranscriptionFrame (voice) or TextFrame (text-mode):
      1. Cancels any still-running previous task (barge-in at the LLM layer)
      2. Fires a background asyncio task so process_frame returns immediately
         — this unblocks the pipeline for the next speech turn
      3. Background task: RAG + SQL enrichment → LLMMessagesFrame → LLM

    On InterruptionFrame: cancels the background task immediately.
    """

    def __init__(
        self,
        engine: ConversationEngine,
        timing: dict | None = None,
        emitter: "ResponseEmitterProcessor | None" = None,
    ):
        super().__init__()
        self.engine = engine
        self._filler_phrase = engine.filler_phrase
        self._task: asyncio.Task | None = None
        self._timing = timing  # shared dict for TTFT tracking
        self._emitter = emitter  # kept for WebSocket broadcast (no longer used for echo detection)
        self._bot_speaking = False        # True while TTS is playing — hard-mute mic
        self._bot_speaking_start: float = 0.0  # monotonic time when bot started speaking
        self._echo_cooldown_until: float = 0.0  # monotonic time until post-TTS echo window closes

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)

        # Track when TTS starts/stops so we can mute our own voice from the mic.
        #
        # BotStartedSpeakingFrame: one-shot transition event (may not fire in all
        # Pipecat versions/configurations).
        # BotSpeakingFrame: periodic heartbeat ← while TTS is playing audio — this
        # is what actually appears in the trace for Pipecat 0.0.108. We gate on both
        # so the hard-mute works regardless of which one Pipecat sends.
        if isinstance(frame, (BotStartedSpeakingFrame, BotSpeakingFrame)):
            if not self._bot_speaking:
                self._bot_speaking_start = time.monotonic()  # record when TTS started
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            speaking_duration = time.monotonic() - self._bot_speaking_start
            self._bot_speaking = False
            # Duration-proportional cooldown: base window + 40% of speaking time, capped.
            # This catches STT pipeline lag (base) and scales with how much audio
            # the mic may have captured from the speakers (duration term).
            # Example: 2s filler → 0.8+0.8 = 1.6s cooldown
            #          8s answer → 0.8+3.2 = 4.0s → capped at 3.0s
            proportional = min(
                _ECHO_COOLDOWN_BASE + speaking_duration * _ECHO_COOLDOWN_RATIO,
                _ECHO_COOLDOWN_MAX,
            )
            self._echo_cooldown_until = time.monotonic() + proportional

        # VAD fires VADUser* frames — STT passes them through unchanged
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            if not self._bot_speaking:
                _ws_emit({"type": "state", "state": "listening"})

        # Barge-in / interruption: cancel whatever is in flight.
        # Must clear _bot_speaking here — BotStoppedSpeakingFrame is NOT sent on barge-in,
        # so without this reset _bot_speaking stays True and all future transcriptions
        # are silently dropped as echoes (the agent goes permanently deaf).
        elif isinstance(frame, InterruptionFrame):
            self._bot_speaking = False
            self._bot_speaking_start = 0.0  # reset so next BotStartedSpeaking starts fresh
            self._echo_cooldown_until = 0.0
            self._cancel_pending()
            _ws_emit({"type": "state", "state": "idle"})
            _ws_emit({"type": "interruption"})

        elif isinstance(frame, TextFrame):
            # TextFrame is the base — TranscriptionFrame is a subclass.
            # Both carry the user's words; either path triggers enrichment.
            question = frame.text.strip()
            if question:
                # ── Echo gate (two layers) ────────────────────────────────
                # 1. Hard gate: bot is actively speaking right now
                in_speaking = self._bot_speaking
                # 2. Duration-proportional cooldown: post-TTS mute window scales
                #    with how long the bot spoke (see _ECHO_COOLDOWN_* constants).
                #    Replaces the previous word-overlap gate which caused false
                #    positives on legitimate follow-up questions.
                in_cooldown = time.monotonic() < self._echo_cooldown_until

                if in_speaking or in_cooldown:
                    reason = "speaking" if in_speaking else "cooldown"
                    status(f"[Echo drop  ] ({reason}) {question!r}")
                else:
                    _ws_emit({"type": "state",      "state": "processing"})
                    _ws_emit({"type": "transcript", "text":  question})
                    self._cancel_pending()
                    self._task = asyncio.create_task(
                        self._enrich_and_push(question)
                    )
                    def _log_task_exc(t: asyncio.Task):
                        if not t.cancelled() and t.exception():
                            logger.error("[task exc] %s", t.exception(), exc_info=t.exception())
                    self._task.add_done_callback(_log_task_exc)
            return  # never forward raw transcript downstream

        # Every frame that isn't consumed above must be forwarded downstream.
        # super().process_frame() handles lifecycle only — it does NOT push frames.
        await self.push_frame(frame, direction)

    def _cancel_pending(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _enrich_and_push(self, question: str) -> None:
        try:
            status(f"[STT  heard ] {question}")

            # TTSSpeakFrame is the correct way to push a standalone utterance —
            # it manages its own turn context internally and flushes immediately.
            await self.push_frame(TTSSpeakFrame(text=self._filler_phrase))

            # Resolve caller-specific session ID; fall back for local/text mode
            session_id = getattr(self, "_caller_id", None) or "local_session"
            session = self.engine._get_session(session_id)
            session.add_user_message(question)

            if _is_social_turn(question):
                # Fast path: obviously conversational — skip SQL query engine.
                # Still fetch RAG/full-context so the LLM has company knowledge
                # for brief replies (e.g. "It is, it is" → warm one-liner response).
                status("[Context    ] Social turn — skipping SQL, fetching docs only")
                rag_context  = await asyncio.to_thread(
                    self.engine.rag.get_context_string, question, 3
                )
                query_result = QueryResult(success=True, data=None, error=None)
            else:
                status("[Context    ] RAG + SQL running in parallel...")
                try:
                    rag_result, query_result = await asyncio.wait_for(
                        asyncio.gather(
                            asyncio.to_thread(self.engine.rag.get_context_string, question, 3),
                            self.engine.query_engine.query(question),
                            return_exceptions=True,
                        ),
                        timeout=8.0,
                    )
                except asyncio.TimeoutError:
                    status("[Timeout    ] RAG+SQL exceeded 8s — sending fallback.")
                    await self.push_frame(TTSSpeakFrame(
                        text="I'm taking longer than expected. Please try again in a moment."
                    ))
                    _ws_emit({"type": "state", "state": "idle"})
                    return

                if isinstance(rag_result, Exception):
                    logger.warning("[RAG        ] RAG context unavailable: %s", rag_result)
                    rag_context = "No document context available."
                else:
                    rag_context = rag_result
                if isinstance(query_result, Exception):
                    logger.warning("[SQL        ] SQL query failed: %s", query_result)
                    query_result = QueryResult(success=False, error=str(query_result))

                # ── DB query visibility ───────────────────────────────────
                # Log what the query engine actually did so we can debug failures.
                qtype = getattr(query_result, "query_type", "?")
                qms   = getattr(query_result, "execution_time_ms", 0)
                if qtype == "none":
                    status(f"[QueryEngine] no_data_query_needed ({qms:.0f}ms)")
                elif query_result.success:
                    rows = len(query_result.data) if isinstance(query_result.data, list) else "—"
                    status(f"[QueryEngine] {qtype} → {rows} rows ({qms:.0f}ms)")
                else:
                    status(f"[QueryEngine] FAILED ({qtype}) — {query_result.error} ({qms:.0f}ms)")

            messages = self.engine.build_messages(question, query_result, rag_context, session)

            status("[LLM        ] Generating response...")
            if self._timing is not None:
                self._timing["llm_send_ts"] = time.perf_counter()
                self._timing["first_token_ts"] = None

            # LLMMessagesFrame is the only frame OpenAILLMService actually acts on —
            # it calls OpenAILLMContext.from_messages() and fires the API request.
            # LLMMessagesUpdateFrame goes to context aggregators (not in this pipeline).
            _ws_emit({"type": "state", "state": "speaking"})
            await self.push_frame(LLMMessagesFrame(messages=messages))

        except asyncio.CancelledError:
            status("[Cancelled  ] Interrupted — ready for new question.")
            _ws_emit({"type": "state", "state": "idle"})
        except Exception as exc:
            logger.error("[_enrich_and_push CRASHED] %s", exc, exc_info=True)
            status(f"[ERROR      ] _enrich_and_push exception: {exc}")
            _ws_emit({"type": "state", "state": "idle"})


# ─── Pipeline trace probe ────────────────────────────────────────

class TraceProcessor(FrameProcessor):
    """Logs every significant frame that passes through a pipeline stage.
    Insert between stages to diagnose where frames go missing."""

    # Frame types too noisy to log (audio chunks, system heartbeats)
    _SILENT = frozenset([
        "AudioRawFrame", "InputAudioRawFrame", "OutputAudioRawFrame",
        "SystemFrame", "HeartbeatFrame", "StartFrame",
    ])

    def __init__(self, label: str):
        super().__init__()
        self._label = label

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        name = type(frame).__name__
        if name not in self._SILENT:
            arrow = "→" if direction == FrameDirection.DOWNSTREAM else "←"
            txt = getattr(frame, "text", None) or getattr(frame, "messages", None)
            extra = f" {str(txt)[:80]!r}" if txt else ""
            status(f"[{self._label}] {arrow} {name}{extra}")
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


# ─── Response emitter: LLM text → WebSocket + echo tracking ─────

class ResponseEmitterProcessor(FrameProcessor):
    """Broadcasts each LLM text token to connected browser tabs.

    Sits between the LLM and TTS in the voice pipeline. Emits:
      {type: 'response',     text: str}  — one per text chunk/token
      {type: 'response_end'}             — when LLM response finishes
    All frames are forwarded downstream unchanged.

    Also accumulates the full bot response text so SurelineContextProcessor
    can do word-overlap echo detection on the next mic transcription.
    """

    def __init__(self):
        super().__init__()
        self._tokens: list[str] = []
        self.last_response_words: frozenset[str] = frozenset()  # read by SurelineContextProcessor

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame) and not isinstance(frame, TranscriptionFrame):
            _ws_emit({"type": "response", "text": frame.text})
            self._tokens.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            _ws_emit({"type": "response_end"})
            full_text = "".join(self._tokens)
            # Store as a word set for fast O(1) lookup in echo detection
            self.last_response_words = frozenset(
                w.strip(".,!?;:\"'()[]") for w in full_text.lower().split()
                if w.strip(".,!?;:\"'()[]")
            )
            self._tokens.clear()

        await self.push_frame(frame, direction)


# ─── Text-mode output ────────────────────────────────────────────

class TerminalOutputProcessor(FrameProcessor):
    """Streams LLM text to console in text mode."""

    def __init__(self, timing: dict | None = None):
        super().__init__()
        self._timing = timing

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame) and not isinstance(frame, TranscriptionFrame):
            if (
                self._timing is not None
                and self._timing.get("first_token_ts") is None
                and self._timing.get("llm_send_ts") is not None
            ):
                now = time.perf_counter()
                ttft = now - self._timing["llm_send_ts"]
                self._timing["first_token_ts"] = now
                print(f"\n[TTFT: {ttft:.2f}s] ", end="", flush=True)
            print(frame.text, end="", flush=True)
        elif isinstance(frame, EndFrame):
            print()

        # Always forward every frame — super() handles lifecycle, push_frame handles routing
        await self.push_frame(frame, direction)


# ─── Text-mode input ─────────────────────────────────────────────

async def terminal_input_loop(task: PipelineTask) -> None:
    print("━" * 50)
    print("  Text mode — type questions, Enter to submit")
    print("  'quit' to exit")
    print("━" * 50 + "\n")
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            if line.lower() in ("quit", "exit"):
                await task.queue_frame(EndFrame())
                break
            await task.queue_frame(TextFrame(text=line))
        except Exception:
            break


# ─── Text mode ───────────────────────────────────────────────────

async def run_text_mode() -> None:
    engine = load_conversation_engine()

    timing: dict = {}  # shared state for TTFT measurements
    context_processor = SurelineContextProcessor(engine, timing=timing)
    llm = create_llm_service()
    output = TerminalOutputProcessor(timing=timing)

    pipeline = Pipeline([context_processor, llm, output])
    runner = PipelineRunner()
    # idle_timeout_secs=None: disable the idle guard entirely in text mode.
    # Default (300s) fires based on BotSpeakingFrame/UserSpeakingFrame which
    # never flow in text mode, killing the pipeline during slow cold-start.
    task = PipelineTask(pipeline, idle_timeout_secs=None, enable_rtvi=False)

    input_task = asyncio.create_task(terminal_input_loop(task))
    await runner.run(task)
    await input_task


# ─── Voice mode ──────────────────────────────────────────────────

async def run_voice_mode() -> None:
    try:
        from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.processors.audio.vad_processor import VADProcessor
    except ImportError as e:
        print(f"Audio deps missing: {e}")
        print("Run: pip install \"pipecat-ai[local,silero]\" sounddevice")
        await run_text_mode()
        return

    engine = load_conversation_engine()

    stt = create_stt_service()
    tts = create_tts_service()

    # Pipecat 0.0.108: VAD is a standalone processor, not a transport param
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ))
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(
        confidence=0.7,
        start_secs=0.2,   # respond quickly to speech start
        stop_secs=0.8,    # wait 800ms of silence — long enough for natural pauses
        min_volume=0.6,
    )))

    # Emitter tracks bot response text for echo detection (shared with context_processor)
    emitter = ResponseEmitterProcessor()
    context_processor = SurelineContextProcessor(engine, emitter=emitter)
    llm = create_llm_service()

    pipeline = Pipeline([
        transport.input(),
        vad,
        stt,
        TraceProcessor("STT→CTX"),   # shows TranscriptionFrame leaving STT
        context_processor,
        TraceProcessor("CTX→LLM"),   # shows LLMMessagesFrame entering LLM
        llm,
        TraceProcessor("LLM→TTS"),   # shows TextFrame tokens leaving LLM
        emitter,                     # streams LLM tokens to browser + tracks text for echo detection
        tts,
        transport.output(),
    ])

    status("Voice pipeline ready — speak into your mic")
    status("STT: Sarvam saaras:v3  |  TTS: Sarvam bulbul:v3")
    status("Barge-in enabled — just speak to interrupt the agent")
    print()

    # Tell the browser which client is active
    _ws_emit({"type": "session_start", "client": engine.client_name})

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    runner = PipelineRunner()

    # Keep-alive: Sarvam closes idle WebSocket connections after ~30s.
    # Send a protocol-level ping every 20s so the connection stays open.
    # Without this, answers are silently dropped when the TTS frame arrives
    # exactly during the reconnect window.
    async def _sarvam_tts_keepalive(tts_service, interval: float = 20.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                ws = getattr(tts_service, "_websocket", None)
                if ws is not None:
                    await asyncio.wait_for(ws.ping(), timeout=5.0)
                    logger.debug("Sarvam TTS keep-alive ping sent")
            except Exception as exc:
                logger.debug("Sarvam TTS keep-alive ping skipped: %s", exc)

    keepalive_task = asyncio.create_task(_sarvam_tts_keepalive(tts))

    try:
        await runner.run(task)
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass


# ─── Entry point ─────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sureline Voice Agent Pipeline")
    parser.add_argument(
        "--text-mode", "--test-mode",
        action="store_true",
        dest="text_mode",
        help="Keyboard input instead of mic (no audio hardware needed)",
    )
    args = parser.parse_args()

    if args.text_mode:
        asyncio.run(run_text_mode())
    else:
        asyncio.run(run_voice_mode())


if __name__ == "__main__":
    main()
