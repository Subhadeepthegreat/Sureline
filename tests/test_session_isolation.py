"""
Tests for per-session memory isolation in SessionMemory and ConversationEngine.

Covers:
- Two sessions with different session_ids have independent histories
- One session's messages do not appear in another's context
- Session memory sliding window evicts old turns correctly
- Clearing a session does not affect other sessions
"""

import pytest

from sureline.conversation.memory import SessionMemory, MAX_HISTORY_TURNS


# ─── SessionMemory unit tests ─────────────────────────────────────

class TestSessionMemoryIsolation:
    def test_two_sessions_have_independent_history(self):
        s1 = SessionMemory(session_id="alice")
        s2 = SessionMemory(session_id="bob")

        s1.add_user_message("What is my balance?")
        s1.add_assistant_message("Your balance is $1,500.")

        s2.add_user_message("How many points do I have?")
        s2.add_assistant_message("You have 200 reward points.")

        history1 = s1.get_history()
        history2 = s2.get_history()

        assert len(history1) == 2
        assert len(history2) == 2
        # No cross-contamination
        assert all("balance" in t["content"] for t in history1)
        assert all("points" in t["content"] for t in history2)

    def test_clearing_one_session_leaves_other_intact(self):
        s1 = SessionMemory("alice")
        s2 = SessionMemory("bob")

        s1.add_user_message("Hello from Alice")
        s2.add_user_message("Hello from Bob")

        s1.clear()

        assert s1.turn_count == 0
        assert s2.turn_count == 1

    def test_session_id_stored(self):
        s = SessionMemory("unique-caller-id")
        assert s.session_id == "unique-caller-id"

    def test_empty_session_returns_empty_history(self):
        s = SessionMemory("fresh")
        assert s.get_history() == []
        assert s.get_summary_context() == "No previous conversation."


# ─── Sliding window eviction ──────────────────────────────────────

class TestSessionMemorySlidingWindow:
    def test_old_turns_evicted_at_max_capacity(self):
        s = SessionMemory(session_id="test", max_turns=3)
        # max_turns=3 → deque maxlen=6 (user+assistant pairs)
        for i in range(10):
            s.add_user_message(f"Question {i}")
            s.add_assistant_message(f"Answer {i}")

        history = s.get_history()
        # Should only keep last 6 messages (3 turn pairs)
        assert len(history) <= 6
        # Latest messages should be present
        assert any("Question 9" in t["content"] for t in history)
        # Earliest messages should be evicted
        assert all("Question 0" not in t["content"] for t in history)

    def test_turn_count_reflects_current_size(self):
        s = SessionMemory(session_id="test", max_turns=2)
        s.add_user_message("msg1")
        s.add_user_message("msg2")
        s.add_user_message("msg3")
        s.add_user_message("msg4")
        s.add_user_message("msg5")
        assert s.turn_count <= s.max_turns * 2


# ─── ConversationEngine session registry ─────────────────────────

class TestConversationEngineSessionRegistry:
    def test_get_session_creates_new_for_unknown_id(self, tmp_path, monkeypatch):
        """_get_session should auto-create a new SessionMemory for a new session_id."""
        import sqlite3
        stub_db = tmp_path / "stub.db"
        sqlite3.connect(str(stub_db)).close()

        with (
            monkeypatch.context() as m,
        ):
            import sureline.config as cfg
            m.setattr(cfg, "AZURE_OPENAI_API_KEY", "", raising=True)
            m.setattr(cfg, "AZURE_OPENAI_ENDPOINT", "", raising=True)
            m.setattr(cfg, "OPENAI_API_KEY", "sk-test", raising=True)
            m.setattr(cfg, "OPENAI_MODEL", "gpt-4o-mini", raising=True)
            m.setattr(cfg, "GEMINI_API_KEY", "", raising=True)

            from unittest.mock import patch
            with patch("sureline.conversation.rag.RAGStore.index_documents"):
                from sureline.conversation.conversation_engine import ConversationEngine
                engine = ConversationEngine(db_path=stub_db)

        session_a = engine._get_session("caller-A")
        session_b = engine._get_session("caller-B")

        assert session_a is not session_b
        assert session_a.session_id == "caller-A"
        assert session_b.session_id == "caller-B"

    def test_same_session_id_returns_same_object(self, tmp_path, monkeypatch):
        import sqlite3
        stub_db = tmp_path / "stub.db"
        sqlite3.connect(str(stub_db)).close()

        import sureline.config as cfg
        with monkeypatch.context() as m:
            m.setattr(cfg, "AZURE_OPENAI_API_KEY", "", raising=True)
            m.setattr(cfg, "AZURE_OPENAI_ENDPOINT", "", raising=True)
            m.setattr(cfg, "OPENAI_API_KEY", "sk-test", raising=True)
            m.setattr(cfg, "OPENAI_MODEL", "gpt-4o-mini", raising=True)
            m.setattr(cfg, "GEMINI_API_KEY", "", raising=True)

            from unittest.mock import patch
            with patch("sureline.conversation.rag.RAGStore.index_documents"):
                from sureline.conversation.conversation_engine import ConversationEngine
                engine = ConversationEngine(db_path=stub_db)

        s1 = engine._get_session("same-caller")
        s2 = engine._get_session("same-caller")
        assert s1 is s2

    def test_sessions_dict_accumulates_independently(self, tmp_path, monkeypatch):
        import sqlite3
        stub_db = tmp_path / "stub.db"
        sqlite3.connect(str(stub_db)).close()

        import sureline.config as cfg
        with monkeypatch.context() as m:
            m.setattr(cfg, "AZURE_OPENAI_API_KEY", "", raising=True)
            m.setattr(cfg, "AZURE_OPENAI_ENDPOINT", "", raising=True)
            m.setattr(cfg, "OPENAI_API_KEY", "sk-test", raising=True)
            m.setattr(cfg, "OPENAI_MODEL", "gpt-4o-mini", raising=True)
            m.setattr(cfg, "GEMINI_API_KEY", "", raising=True)

            from unittest.mock import patch
            with patch("sureline.conversation.rag.RAGStore.index_documents"):
                from sureline.conversation.conversation_engine import ConversationEngine
                engine = ConversationEngine(db_path=stub_db)

        sa = engine._get_session("A")
        sb = engine._get_session("B")
        sa.add_user_message("message from A")
        sb.add_user_message("message from B")

        # Fetching again by id returns the same populated session
        assert engine._get_session("A").get_history()[0]["content"] == "message from A"
        assert engine._get_session("B").get_history()[0]["content"] == "message from B"
