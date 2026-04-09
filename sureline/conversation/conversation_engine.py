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
from sureline.conversation.rag import RAGStore
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
        self.rag = RAGStore(client_id=client_id)
        self.sessions: dict[str, tuple[SessionMemory, datetime]] = {}
        self._session_access_count: int = 0  # triggers periodic TTL sweep

        self.rag.index_documents()

        logger.info("ConversationEngine initialized (model=%s, client=%s)", self._model, client_name)

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
        Called by SurelineContextProcessor to push LLMMessagesUpdateFrame.
        """
        if query_result.success and query_result.data:
            if isinstance(query_result.data, str):
                data_str = query_result.data
            elif isinstance(query_result.data, list):
                data_str = json.dumps(query_result.data[:10], indent=2, default=str)
            else:
                data_str = str(query_result.data)
        elif not query_result.success:
            data_str = f"Query failed: {query_result.error}"
        else:
            data_str = "No data results."

        system_prompt = (
            f"You are a voice assistant for {self._client_name}.\n\n"
            "Generate a SPOKEN response — this will be read aloud by a text-to-speech system.\n\n"
            "RULES:\n"
            "1. Be CONCISE — 1 to 3 sentences maximum.\n"
            "2. Use natural, conversational language (as if speaking to someone).\n"
            "3. Use Indian number formatting when applicable (lakhs, crores).\n"
            "4. Do NOT use markdown, bullet points, tables, or any formatting.\n"
            "5. Do NOT say 'according to the data' or 'based on the query' — just give the answer naturally.\n"
            "6. If data shows no results, say it naturally: 'I couldn't find any data for that.'\n"
            "7. Be slightly warm and professional.\n"
            "8. If the data is surprising, you can be subtly amused."
        )

        messages = [{"role": "system", "content": system_prompt}]

        history = session.get_history()
        if len(history) > 2:
            for h in history[-4:-1]:
                messages.append(h)

        user_msg = (
            f'User asked: "{question}"\n\n'
            f"Company context:\n{rag_context}\n\n"
            f"Data query result:\n{data_str}\n\n"
            "Generate a natural spoken response:"
        )
        messages.append({"role": "user", "content": user_msg})
        return messages

    async def _generate_response(self, question, query_result, rag_context, session) -> str:
        messages = self.build_messages(question, query_result, rag_context, session)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.4,
                max_tokens=150,
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
