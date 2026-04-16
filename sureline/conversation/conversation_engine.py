"""
Sureline — Conversation Engine

Orchestrates the full question → answer flow:
1. Receives user question (text from STT)
2. RAG + QueryEngine run in PARALLEL (asyncio.gather)
3. Uses active LLM provider to generate a speech-friendly response
4. Returns concise, spoken-friendly text for TTS
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

from sureline.config import DB_PATH, create_llm_client
from sureline.query.query_engine import QueryEngine
from sureline.conversation.rag import create_context_store
from sureline.conversation.memory import SessionMemory

logger = logging.getLogger(__name__)

# Sessions inactive longer than this are eligible for eviction
_SESSION_TTL_MINUTES = 30


class ConversationEngine:
    """
    Full conversation pipeline: question → data query → spoken answer.

    Combines:
    - RAG context from company documents
    - Data query results from SQLite/CSV (parallel with RAG)
    - Session memory for multi-turn context
    - LLM response generation optimised for speech output
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        csv_path: Optional[Path] = None,
        client_name: str = "the company",
        company_description: str = "",
        client_id: Optional[str] = None,
        filler_phrase: str = "Let me check that for you...",
    ):
        self._client, self._model = create_llm_client()
        self._client_name = client_name
        self._filler_phrase = filler_phrase

        self.query_engine = QueryEngine(
            db_path=db_path or DB_PATH,
            csv_path=csv_path,
            client_name=client_name,
            company_description=company_description,
        )
        # Auto-selects WikiStore → FullContextStore → RAGStore (in that priority order)
        self.rag = create_context_store(client_id=client_id)
        store_type = type(self.rag).__name__
        self.sessions: dict[str, tuple[SessionMemory, datetime]] = {}
        self._session_access_count: int = 0  # triggers periodic TTL sweep

        self.rag.index_documents()

        logger.info(
            "ConversationEngine initialized (model=%s, client=%s, store=%s)",
            self._model, client_name, store_type,
        )

    @property
    def filler_phrase(self) -> str:
        return self._filler_phrase

    @property
    def client_name(self) -> str:
        return self._client_name

    def _get_session(self, session_id: str) -> SessionMemory:
        now = datetime.now(timezone.utc)
        if session_id not in self.sessions:
            mem = SessionMemory(session_id=session_id)
            self.sessions[session_id] = (mem, now)
        else:
            mem, _ = self.sessions[session_id]
            self.sessions[session_id] = (mem, now)  # update last_active

        # Periodically evict stale sessions (every 100 accesses) to bound memory growth
        self._session_access_count += 1
        if self._session_access_count % 100 == 0:
            self.cleanup_stale_sessions()

        return mem

    def cleanup_stale_sessions(self, ttl_minutes: int = _SESSION_TTL_MINUTES) -> int:
        """Evict sessions inactive longer than ttl_minutes. Returns number evicted."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)
        stale = [sid for sid, (_, last) in self.sessions.items() if last < cutoff]
        for sid in stale:
            del self.sessions[sid]
        if stale:
            logger.info("Evicted %d stale sessions (ttl=%dm)", len(stale), ttl_minutes)
        return len(stale)

    async def process_question(
        self,
        question: str,
        session_id: str = "default",
    ) -> dict:
        """
        Full pipeline: user question → spoken answer.

        RAG and QueryEngine run in parallel via asyncio.gather.
        Partial failures (one source fails) are handled gracefully.

        Returns:
            Dict with answer, query_result, context_used, timing.
        """
        timing = {}
        total_start = time.time()

        session = self._get_session(session_id)
        session.add_user_message(question)

        # RAG + SQL in parallel — ~800ms saved vs sequential
        t0 = time.time()
        rag_result, query_result = await asyncio.gather(
            asyncio.to_thread(self.rag.get_context_string, question, 3),
            self.query_engine.query(question),
            return_exceptions=True,
        )
        timing["parallel_ms"] = (time.time() - t0) * 1000

        if isinstance(rag_result, Exception):
            logger.warning("RAG failed: %s", rag_result)
            rag_context = "No document context available."
        else:
            rag_context = rag_result

        if isinstance(query_result, Exception):
            logger.warning("Query engine failed: %s", query_result)
            from sureline.query.sandbox import QueryResult
            query_result = QueryResult(success=False, error=str(query_result))

        # Generate spoken response
        t0 = time.time()
        answer = await self._generate_response(question, query_result, rag_context, session)
        timing["response_gen_ms"] = (time.time() - t0) * 1000

        session.add_assistant_message(answer)
        timing["total_ms"] = (time.time() - total_start) * 1000

        logger.info(
            "Question processed in %.0fms (parallel=%.0fms, response=%.0fms)",
            timing["total_ms"], timing["parallel_ms"], timing["response_gen_ms"],
        )

        return {
            "answer": answer,
            "query_result": query_result,
            "context_used": rag_context,
            "timing": timing,
        }

    def build_messages(
        self,
        question: str,
        query_result,
        rag_context: str,
        session: SessionMemory,
    ) -> list[dict]:
        """
        Build the messages array for the streaming LLM in pipeline.py.
        Called by SurelineContextProcessor to push LLMMessagesFrame.

        Architecture:
        - System prompt = voice agent rules + full company knowledge (background)
        - History messages = clean prior Q&A turns
        - Final user message = current question + live DB data only
          (docs are NOT repeated here — they're already in the system prompt)
        """
        # ── Format live data result ───────────────────────────────────────
        if query_result.success and query_result.data:
            if isinstance(query_result.data, str):
                data_str = query_result.data
            elif isinstance(query_result.data, list):
                data_str = json.dumps(query_result.data[:10], indent=2, default=str)
            else:
                data_str = str(query_result.data)
        elif not query_result.success:
            data_str = f"Data query failed: {query_result.error}"
        else:
            data_str = "No data results found."

        # ── System prompt: rules + docs as background knowledge ──────────
        # Docs go here (not in the user message) so the LLM treats them as
        # standing knowledge, separate from the live conversation.
        system_prompt = (
            f"You are Sureline, a voice assistant who speaks AS {self._client_name} — "
            "as if you are the company's knowledgeable, warm, and slightly witty spokesperson.\n\n"
            "You are mid-conversation. The user already knows who you are. "
            "Your responses will be read aloud by text-to-speech.\n\n"

            "━━━ CONVERSATION RULES ━━━\n"
            "1. HISTORY AWARENESS — Read the conversation history carefully. "
            "NEVER repeat information you already provided in a previous turn. "
            "If you covered something before, build on it or move forward — do not restart.\n"
            "2. RESPONSE LENGTH — Match length strictly to what was asked:\n"
            "   • Short reaction / acknowledgment (e.g. 'it is', 'okay', 'wow') → 1 sentence, warm and brief.\n"
            "   • Simple factual question → 1-2 sentences.\n"
            "   • Story / achievement / detail request → full, rich, narrative answer.\n"
            "   • Follow-up ('tell me more', 'what else') → expand the SAME topic, don't switch.\n"
            "3. ENDINGS — Never end with generic questions like 'Would you like to know more?' or "
            "'Isn't that exciting?' — these feel robotic. Either end naturally, or ask something "
            "SPECIFIC that follows logically from what was just discussed.\n"
            "4. GROUNDEDNESS — Only speak from the Company Knowledge and Live Data below. "
            "If neither has the answer, say warmly: 'I don't have that detail right now.' "
            "NEVER invent facts, numbers, or incidents.\n"
            "5. VOICE — Conversational, spoken language only. No bullet points, lists, or markdown. "
            "Use Indian number formatting (lakhs, crores). Speak as the company's voice, never say "
            "'according to the documents' or 'based on the data'.\n"
            "6. HUMOUR & WARMTH — The company has a distinct personality. Let it come through "
            "naturally — don't force it, but don't suppress it either.\n\n"

            f"━━━ COMPANY KNOWLEDGE ━━━\n{rag_context}\n"
        )

        messages = [{"role": "system", "content": system_prompt}]

        # ── Inject conversation history (up to last 6 messages) ──────────
        # Use clean Q&A pairs so the LLM can see what it already covered.
        history = session.get_history()
        # history[-1] is the current question (just added in _enrich_and_push)
        # history[-2] is the last assistant reply, etc.
        # We include up to 6 messages before the current question.
        prior = history[:-1]  # exclude current question — it goes in user_msg below
        for h in prior[-6:]:
            messages.append(h)

        # ── Current turn: question + live DB data ─────────────────────────
        # Docs are already in system prompt — don't repeat them here.
        if data_str and data_str not in ("No data results found.", "Data query failed: no_data_query_needed"):
            user_msg = f"{question}\n\n[Live data: {data_str}]"
        else:
            user_msg = question

        messages.append({"role": "user", "content": user_msg})
        return messages

    async def _generate_response(self, question, query_result, rag_context, session) -> str:
        messages = self.build_messages(question, query_result, rag_context, session)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.6,
                max_tokens=400,
            )
            answer = response.choices[0].message.content.strip()
            answer = answer.replace("**", "").replace("*", "")
            answer = answer.replace("- ", "").replace("• ", "")
            return answer
        except Exception as e:
            logger.error("Response generation failed: %s", e, exc_info=True)
            return "I'm sorry, I encountered an error processing that question. Could you try asking again?"


if __name__ == "__main__":
    async def _main():
        engine = ConversationEngine()
        questions = [
            "What were total sales?",
            "Who is the highest paid employee?",
            "What is the leave policy?",
        ]
        for q in questions:
            print(f"\n{'='*60}\n? {q}")
            result = await engine.process_question(q)
            print(f"  {result['answer']}")
            print(f"  {result['timing']['total_ms']:.0f}ms total")

    asyncio.run(_main())
