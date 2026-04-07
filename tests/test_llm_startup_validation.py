"""
Tests for LLM provider selection in sureline/config.py::create_llm_client().

Verifies the priority chain: Azure → OpenAI → Gemini → Ollama.
Uses monkeypatch to set env vars without touching the real environment.
Each test patches config module attributes directly (post-load) since
dotenv has already run by import time.
"""

import logging
from unittest.mock import patch

import pytest

import sureline.config as cfg


def _call_create(monkeypatch, env: dict) -> tuple:
    """Helper: patch config module attributes and call create_llm_client()."""
    with monkeypatch.context() as m:
        for attr, val in env.items():
            m.setattr(cfg, attr, val, raising=True)
        return cfg.create_llm_client()


class TestLLMProviderSelection:
    def test_azure_wins_when_both_key_and_endpoint_set(self, monkeypatch):
        client, model = _call_create(monkeypatch, {
            "AZURE_OPENAI_API_KEY": "azure-key",
            "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
            "AZURE_OPENAI_MODEL": "gpt-4o",
            "OPENAI_API_KEY": "openai-key",
            "GEMINI_API_KEY": "",
        })
        assert model == "gpt-4o"
        # base_url should point at Azure endpoint pattern
        assert "openai.azure.com" in str(client.base_url)

    def test_azure_skipped_when_only_key_no_endpoint(self, monkeypatch):
        # Both key AND endpoint required — key alone should fall through to OpenAI
        client, model = _call_create(monkeypatch, {
            "AZURE_OPENAI_API_KEY": "azure-key",
            "AZURE_OPENAI_ENDPOINT": "",        # missing endpoint
            "OPENAI_API_KEY": "openai-key",
            "OPENAI_MODEL": "gpt-4o-mini",
            "GEMINI_API_KEY": "",
        })
        assert model == "gpt-4o-mini"

    def test_openai_selected_without_azure(self, monkeypatch):
        client, model = _call_create(monkeypatch, {
            "AZURE_OPENAI_API_KEY": "",
            "AZURE_OPENAI_ENDPOINT": "",
            "OPENAI_API_KEY": "sk-real-key",
            "OPENAI_MODEL": "gpt-4o-mini",
            "GEMINI_API_KEY": "",
        })
        assert model == "gpt-4o-mini"
        assert "openai.com" in str(client.base_url)

    def test_gemini_selected_without_azure_or_openai(self, monkeypatch):
        client, model = _call_create(monkeypatch, {
            "AZURE_OPENAI_API_KEY": "",
            "AZURE_OPENAI_ENDPOINT": "",
            "OPENAI_API_KEY": "",
            "GEMINI_API_KEY": "gemini-key",
            "GEMINI_MODEL": "gemini-2.0-flash",
            "GEMINI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
        })
        assert model == "gemini-2.0-flash"
        assert "googleapis" in str(client.base_url)

    def test_ollama_fallback_when_no_cloud_keys(self, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING, logger="sureline.config"):
            client, model = _call_create(monkeypatch, {
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OLLAMA_MODEL": "qwen2.5:1.5b",
                "OLLAMA_BASE_URL": "http://localhost:11434",
            })
        assert model == "qwen2.5:1.5b"
        assert any("ollama" in r.message.lower() for r in caplog.records)

    def test_ollama_fallback_logs_warning(self, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING, logger="sureline.config"):
            _call_create(monkeypatch, {
                "AZURE_OPENAI_API_KEY": "",
                "AZURE_OPENAI_ENDPOINT": "",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
                "OLLAMA_MODEL": "qwen2.5:1.5b",
                "OLLAMA_BASE_URL": "http://localhost:11434",
            })
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("production" in m.lower() for m in warning_messages), (
            "Expected a production-warning when falling back to Ollama"
        )

    def test_returns_tuple_of_client_and_string(self, monkeypatch):
        import openai
        client, model = _call_create(monkeypatch, {
            "AZURE_OPENAI_API_KEY": "",
            "AZURE_OPENAI_ENDPOINT": "",
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "gpt-4o-mini",
            "GEMINI_API_KEY": "",
        })
        assert isinstance(client, openai.AsyncOpenAI)
        assert isinstance(model, str)
        assert len(model) > 0
