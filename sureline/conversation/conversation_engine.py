"""
Sureline — Conversation Engine

Orchestrates the full question → answer flow using tool calling:
1. Receives user question (text from STT)
2. Uses RAG to fetch relevant company doc context
3. Uses QueryEngine (tool calling) to get data from DB
4. Uses Ollama to generate a speech-friendly response
5. Returns concise, spoken-friendly text for TTS

This engine uses TOOL CALLS throughout for speed:
- QueryEngine uses tools to decide SQL vs Pandas vs no-query
- ResponseGenerator uses tools to decide format
"""

import json
import logging
import time
from typing import Optional
from pathlib import Path

import ollama

from sureline.config import DB_PATH, OLLAMA_BASE_URL
from sureline.query.query_engine import QueryEngine
from sureline.conversation.rag import RAGStore
from sureline.conversation.memory import SessionMemory

logger = logging.getLogger(__name__)


class ConversationEngine:
    """
    Full conversation pipeline: question → data query → spoken answer.

    Combines:
    - RAG context from company documents
    - Data query results from SQLite/CSV
    - Session memory for multi-turn context
    - LLM response generation optimized for speech output
    """

    def __init__(
        self,
        model_name: str = "qwen2.5:3b",
        db_path: Optional[Path] = None,
    ):
        self.model_name = model_name

        # Sub-components
        self.query_engine = QueryEngine(model_name=model_name, db_path=db_path)
        self.rag = RAGStore()
        self.sessions: dict[str, SessionMemory] = {}

        # Index documents if not already done
        self.rag.index_documents()

        logger.info(f"ConversationEngine initialized (model={model_name})")

    def _get_session(self, session_id: str) -> SessionMemory:
        """Get or create a session memory."""
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionMemory(session_id=session_id)
        return self.sessions[session_id]

    def process_question(
        self,
        question: str,
        session_id: str = "default",
    ) -> dict:
        """
        Full pipeline: user question → spoken answer.

        Args:
            question: User's question (text from STT).
            session_id: Session ID for multi-turn memory.

        Returns:
            Dict with:
              - answer: str (speech-friendly text for TTS)
              - query_result: QueryResult object
              - context_used: str (RAG context)
              - timing: dict (breakdown of latencies)
        """
        timing = {}
        total_start = time.time()

        # 1. Get session memory
        session = self._get_session(session_id)
        session.add_user_message(question)

        # 2. RAG — fetch relevant company documents
        t0 = time.time()
        rag_context = self.rag.get_context_string(question, n_results=3)
        timing["rag_ms"] = (time.time() - t0) * 1000

        # 3. Query Engine — get data results via tool calling
        t0 = time.time()
        query_result = self.query_engine.query(question)
        timing["query_ms"] = (time.time() - t0) * 1000

        # 4. Generate speech-friendly response
        t0 = time.time()
        answer = self._generate_response(
            question=question,
            query_result=query_result,
            rag_context=rag_context,
            session=session,
        )
        timing["response_gen_ms"] = (time.time() - t0) * 1000

        # 5. Record in session
        session.add_assistant_message(answer)

        timing["total_ms"] = (time.time() - total_start) * 1000

        logger.info(
            f"Question processed in {timing['total_ms']:.0f}ms "
            f"(rag={timing['rag_ms']:.0f}ms, query={timing['query_ms']:.0f}ms, "
            f"response={timing['response_gen_ms']:.0f}ms)"
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
        Build the messages array containing the system prompt, history, and user context.
        This can be passed to Pipecat's OllamaLLMService for streaming.
        """
        # Build the data context
        if query_result.success and query_result.data:
            if isinstance(query_result.data, str):
                data_str = query_result.data
            elif isinstance(query_result.data, list):
                # Truncate large results for the LLM
                data_str = json.dumps(query_result.data[:10], indent=2, default=str)
            else:
                data_str = str(query_result.data)
        elif not query_result.success:
            data_str = f"Query failed: {query_result.error}"
        else:
            data_str = "No data results."

        # System prompt for speech-friendly response generation
        system_prompt = """You are a voice assistant for Mahakash Space Private Limited, an Indian space company.

Generate a SPOKEN response — this will be read aloud by a text-to-speech system.

RULES:
1. Be CONCISE — 1 to 3 sentences maximum.
2. Use natural, conversational language (as if speaking to someone).
3. Use Indian number formatting when applicable (lakhs, crores).
4. Do NOT use markdown, bullet points, tables, or any formatting.
5. Do NOT say "according to the data" or "based on the query" — just give the answer naturally.
6. If data shows no results, say it naturally: "I couldn't find any data for that."
7. Be slightly warm and professional — you represent Mahakash.
8. If the data is surprising or funny, you can be subtly amused."""

        # Build messages
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Add conversation history (last few turns for context)
        history = session.get_history()
        if len(history) > 2:  # Include previous context if multi-turn
            for h in history[-4:-1]:  # Last 2 exchanges before current
                messages.append(h)

        # User message with all context
        user_msg = f"""User asked: "{question}"

Company context:
{rag_context}

Data query result:
{data_str}

Generate a natural spoken response:"""

        messages.append({"role": "user", "content": user_msg})
        return messages

    def _generate_response(
        self,
        question: str,
        query_result,
        rag_context: str,
        session: SessionMemory,
    ) -> str:
        """
        Generate a speech-friendly response from query results and context.
        Uses Ollama directly for response formatting (non-streaming).
        """
        messages = self.build_messages(question, query_result, rag_context, session)

        try:
            response = ollama.chat(
                model=self.model_name,
                messages=messages,
                options={
                    "temperature": 0.4,   # Some warmth, but mostly accurate
                    "num_predict": 150,   # Short responses for voice
                },
            )
            answer = response["message"]["content"].strip()

            # Clean up any accidental formatting
            answer = answer.replace("**", "").replace("*", "")
            answer = answer.replace("- ", "").replace("• ", "")

            return answer

        except Exception as e:
            logger.error(f"Response generation failed: {e}", exc_info=True)
            return "I'm sorry, I encountered an error processing that question. Could you try asking again?"


if __name__ == "__main__":
    engine = ConversationEngine()

    test_questions = [
        "What were Mahakash's total sales?",
        "Who is the highest paid employee?",
        "What is our leave policy?",
        "What does the Astro-Catering department do?",
        "How many rockets have we sold?",
    ]

    for q in test_questions:
        print(f"\n{'='*60}")
        print(f"❓ {q}")
        result = engine.process_question(q)
        print(f"🗣️  {result['answer']}")
        print(f"⏱️  {result['timing']['total_ms']:.0f}ms total")
