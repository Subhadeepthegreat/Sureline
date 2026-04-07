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
    EndFrame,
    InterruptionFrame,
    LLMMessagesUpdateFrame,   # replaces deprecated LLMMessagesFrame in pipecat 0.0.105+
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.openai.llm import OpenAILLMService

from sureline.conversation.conversation_engine import ConversationEngine
from sureline.config import (
    DB_PATH, OLLAMA_MODEL,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_BASE_URL,
)
from sureline.stt.stt_module import create_stt_service
from sureline.tts.tts_module import create_tts_service


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


# ─── Status printer ──────────────────────────────────────────────

def status(msg: str) -> None:
    """Single-line status visible above all other log noise."""
    print(f"\n>>> {msg}", flush=True)


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

    def __init__(self, engine: ConversationEngine, timing: dict | None = None):
        super().__init__()
        self.engine = engine
        self._task: asyncio.Task | None = None
        self._timing = timing  # shared dict for TTFT tracking

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)

        # Barge-in / interruption: cancel whatever is in flight
        if isinstance(frame, InterruptionFrame):
            self._cancel_pending()
            return

        question = None
        if isinstance(frame, TranscriptionFrame):
            question = frame.text
        elif isinstance(frame, TextFrame) and not isinstance(frame, TranscriptionFrame):
            question = frame.text

        if question and question.strip():
            self._cancel_pending()
            self._task = asyncio.create_task(
                self._enrich_and_push(question.strip())
            )

    def _cancel_pending(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _enrich_and_push(self, question: str) -> None:
        try:
            status(f"[STT  heard ] {question}")

            # Push filler immediately so TTS has something to say while
            # both LLM calls (RAG + SQL) are running in parallel.
            await self.push_frame(TextFrame(text="Let me check that for you..."))

            status("[Context    ] RAG + SQL running in parallel...")

            # Resolve caller-specific session ID; fall back for local/text mode
            session_id = getattr(self, "_caller_id", None) or "local_session"
            session = self.engine._get_session(session_id)
            session.add_user_message(question)

            rag_result, query_result = await asyncio.gather(
                asyncio.to_thread(self.engine.rag.get_context_string, question, 3),
                self.engine.query_engine.query(question),
                return_exceptions=True,
            )

            rag_context = (
                rag_result if not isinstance(rag_result, Exception)
                else "No document context available."
            )
            if isinstance(query_result, Exception):
                from sureline.query.sandbox import QueryResult as _QR
                query_result = _QR(success=False, error=str(query_result))

            messages = self.engine.build_messages(question, query_result, rag_context, session)

            status("[LLM        ] Generating response...")
            if self._timing is not None:
                self._timing["llm_send_ts"] = time.perf_counter()
                self._timing["first_token_ts"] = None

            # LLMMessagesUpdateFrame(run_llm=True) updates context AND triggers
            # LLM generation in one frame (replaces deprecated LLMMessagesFrame).
            await self.push_frame(LLMMessagesUpdateFrame(messages=messages, run_llm=True))

        except asyncio.CancelledError:
            status("[Cancelled  ] Interrupted — ready for new question.")


# ─── Text-mode output ────────────────────────────────────────────

class TerminalOutputProcessor(FrameProcessor):
    """Streams LLM text to console in text mode."""

    def __init__(self, timing: dict | None = None):
        super().__init__()
        self._timing = timing

    async def process_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
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
        await super().process_frame(frame, direction)


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
    engine = ConversationEngine(db_path=DB_PATH)

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
        from pipecat.processors.audio.vad_processor import VADProcessor
    except ImportError as e:
        print(f"Audio deps missing: {e}")
        print("Run: pip install \"pipecat-ai[local,silero]\" sounddevice")
        await run_text_mode()
        return

    engine = ConversationEngine(db_path=DB_PATH)

    stt = create_stt_service()
    tts = create_tts_service()

    # Pipecat 0.0.108: VAD is a standalone processor, not a transport param
    transport = LocalAudioTransport(LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ))
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer())

    context_processor = SurelineContextProcessor(engine)
    llm = create_llm_service()

    pipeline = Pipeline([
        transport.input(),
        vad,               # emits VADUserStartedSpeakingFrame / VADUserStoppedSpeakingFrame
        stt,               # flushes on VADUserStoppedSpeakingFrame → TranscriptionFrame
        context_processor, # TranscriptionFrame → RAG+SQL → LLMMessagesFrame
        llm,               # LLMMessagesFrame → TextFrame stream
        tts,               # TextFrame → audio
        transport.output(),
    ])

    status("Voice pipeline ready — speak into your mic")
    status("STT: Sarvam saaras:v3  |  TTS: Sarvam bulbul:v3")
    status("Barge-in enabled — just speak to interrupt the agent")
    print()

    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    runner = PipelineRunner()
    await runner.run(task)


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
