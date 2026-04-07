"""
Tests for LLM provider switching in QueryEngine and ConversationEngine.

Verifies that when the active provider changes (via env vars), the engines
pick up the right client/model without requiring code changes. Focuses on
observable behavior: which model name the engine was initialized with,
and that the tool-calling API is invoked on the right client.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

import sureline.config as cfg
from sureline.query.query_engine import QueryEngine


# ─── Helpers ─────────────────────────────────────────────────────

def _mock_tool_call_response(tool_name: str = "no_data_query_needed", reason: str = "test"):
    """Create a fake tool-call response from the LLM."""
    import json
    tool_call = MagicMock()
    tool_call.function.name = tool_name
    tool_call.function.arguments = json.dumps({"reason": reason})

    message = MagicMock()
    message.tool_calls = [tool_call]

    choice = MagicMock()
    choice.message = message

    response = MagicMock()
    response.choices = [choice]
    return response


# ─── QueryEngine provider switching ──────────────────────────────

class TestQueryEngineProviderSwitching:
    @pytest.mark.parametrize("provider,expected_model,env_attrs", [
        (
            "azure",
            "gpt-4o",
            {
                "AZURE_OPENAI_API_KEY": "azure-key",
                "AZURE_OPENAI_ENDPOINT": "https://resource.openai.azure.com",
                "AZURE_OPENAI_MODEL": "gpt-4o",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
            },
        ),
        (
            "openai",
            "gpt-4o-mini",
            {
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "OPENAI_API_KEY": "sk-real",
                "OPENAI_MODEL": "gpt-4o-mini",
                "GEMINI_API_KEY": "",
            },
        ),
        (
            "gemini",
            "gemini-2.0-flash",
            {
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "gemini-key",
                "GEMINI_MODEL": "gemini-2.0-flash",
                "GEMINI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
            },
        ),
        (
            "ollama",
            "qwen2.5:1.5b",
            {
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OLLAMA_MODEL": "qwen2.5:1.5b",
                "OLLAMA_BASE_URL": "http://localhost:11434",
            },
        ),
    ])
    def test_engine_uses_correct_model(
        self,
        provider: str,
        expected_model: str,
        env_attrs: dict,
        monkeypatch,
        tmp_path: Path,
    ):
        import sqlite3
        stub_db = tmp_path / "stub.db"
        sqlite3.connect(str(stub_db)).close()

        with monkeypatch.context() as m:
            for attr, val in env_attrs.items():
                m.setattr(cfg, attr, val, raising=True)
            engine = QueryEngine(db_path=stub_db)

        assert engine._model == expected_model, (
            f"Provider '{provider}': expected model '{expected_model}', got '{engine._model}'"
        )

    @pytest.mark.asyncio
    async def test_query_engine_calls_llm_with_correct_model(
        self, monkeypatch, tmp_path: Path
    ):
        """Verify the model name is passed through to the API call."""
        import sqlite3
        from sureline.query.query_engine import QueryEngine

        stub_db = tmp_path / "stub.db"
        sqlite3.connect(str(stub_db)).close()

        fake_response = _mock_tool_call_response()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

        with patch("sureline.query.query_engine.create_llm_client",
                   return_value=(mock_client, "test-model-xyz")):
            engine = QueryEngine(db_path=stub_db)
            await engine.query("Tell me about the company")

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("model") == "test-model-xyz"


# ─── ConversationEngine provider switching ────────────────────────

class TestConversationEngineProviderSwitching:
    def test_conversation_engine_inherits_provider(self, monkeypatch, tmp_path: Path):
        """ConversationEngine should propagate provider choice to QueryEngine."""
        import sqlite3
        stub_db = tmp_path / "stub.db"
        sqlite3.connect(str(stub_db)).close()

        with monkeypatch.context() as m:
            m.setattr(cfg, "AZURE_OPENAI_API_KEY", "", raising=True)
            m.setattr(cfg, "AZURE_OPENAI_ENDPOINT", "", raising=True)
            m.setattr(cfg, "OPENAI_API_KEY", "sk-test", raising=True)
            m.setattr(cfg, "OPENAI_MODEL", "gpt-4o-mini", raising=True)
            m.setattr(cfg, "GEMINI_API_KEY", "", raising=True)

            with patch("sureline.conversation.rag.RAGStore.index_documents"):
                from sureline.conversation.conversation_engine import ConversationEngine
                engine = ConversationEngine(db_path=stub_db)

        assert engine._model == "gpt-4o-mini"
        assert engine.query_engine._model == "gpt-4o-mini"
