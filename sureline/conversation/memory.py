"""
Sureline — Session Memory

Maintains per-session conversation history for multi-turn interactions.
Uses a sliding window to keep context manageable for small models.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

logger = logging.getLogger(__name__)

# Max turns to keep in memory (user + assistant pairs)
MAX_HISTORY_TURNS = 10


@dataclass
class Turn:
    """A single conversational turn."""
    role: str       # "user" or "assistant"
    content: str


class SessionMemory:
    """
    Per-session conversation memory with sliding window.

    Keeps the last MAX_HISTORY_TURNS turns to avoid overflowing
    the context window of small models.
    """

    def __init__(self, session_id: str = "default", max_turns: int = MAX_HISTORY_TURNS):
        self.session_id = session_id
        self.max_turns = max_turns
        self._history: deque[Turn] = deque(maxlen=max_turns * 2)  # *2 for user+assistant pairs
        logger.info(f"Session memory created: {session_id} (max {max_turns} turns)")

    def add_user_message(self, content: str) -> None:
        """Record a user message."""
        self._history.append(Turn(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        """Record an assistant response."""
        self._history.append(Turn(role="assistant", content=content))

    def get_history(self) -> list[dict]:
        """
        Get conversation history formatted for LLM consumption.

        Returns:
            List of {"role": ..., "content": ...} dicts.
        """
        return [{"role": t.role, "content": t.content} for t in self._history]

    def get_summary_context(self) -> str:
        """
        Get a compact string summary of the conversation for context injection.
        """
        if not self._history:
            return "No previous conversation."

        lines = []
        for t in self._history:
            prefix = "User" if t.role == "user" else "Agent"
            # Truncate long messages
            msg = t.content[:150] + "..." if len(t.content) > 150 else t.content
            lines.append(f"{prefix}: {msg}")

        return "Previous conversation:\n" + "\n".join(lines)

    def clear(self) -> None:
        """Clear all history."""
        self._history.clear()
        logger.info(f"Session {self.session_id} memory cleared.")

    @property
    def turn_count(self) -> int:
        """Number of messages in history."""
        return len(self._history)
