"""
Tests for pipeline components added in the master branch.

Covers:
- _is_social_turn() regex matching (no SQL needed for greetings)
- FullContextStore: loading docs, empty-dir fallback, no-op index_documents
- create_context_store() factory: WikiStore > FullContextStore > RAGStore selection
- SurelineContextProcessor echo gate: bot_speaking, cooldown, barge-in reset
- ResponseEmitterProcessor: token accumulation, response_end, per-turn clear
- has_cloud_llm_key() all-provider combinations
- _ResilientSarvamSTTService reconnect: lambda loop capture correctness
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipecat.processors.frame_processor import FrameDirection

# ─── _is_social_turn ─────────────────────────────────────────────


def _get_is_social_turn():
    """Import _is_social_turn by extracting just the regex logic from pipeline.py.
    Avoids pulling in pipeline.py's full import chain (pipecat, sarvam, etc.)."""
    import re as _re
    import importlib.util, sys
    # We extract the function by exec'ing just the relevant pieces
    from pathlib import Path
    src = Path(__file__).parent.parent / "pipeline.py"
    text = src.read_text(encoding="utf-8")
    # Extract the regex and function definition
    namespace: dict = {"re": _re}
    # Find and exec just the regex and function
    import ast
    tree = ast.parse(text)
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_NON_DATA_RE":
                    start, end = node.lineno - 1, node.end_lineno
                    exec("\n".join(lines[start:end]), namespace)
        if isinstance(node, ast.FunctionDef) and node.name == "_is_social_turn":
            start, end = node.lineno - 1, node.end_lineno
            exec("\n".join(lines[start:end]), namespace)
    return namespace["_is_social_turn"]


class TestIsSocialTurn:
    """_is_social_turn() should match only explicit social patterns — not short queries."""

    @pytest.fixture(autouse=True)
    def _import(self):
        self._is_social_turn = _get_is_social_turn()

    # ── Should return True (regex-matched social patterns) ──────────
    @pytest.mark.parametrize("text", [
        "hi",
        "hello",
        "hey",
        "good morning",
        "good evening",
        "thanks",
        "thank you",
        "okay",
        "ok",
        "sure",
        "it is",
        "it is, it is",
        "interesting",
        "wow",
        "i see",
        "i understand",
        "got it",
        "that's great",
        "right",
        "absolutely",
        "who are you",
        "what can you do",
        "tell me what you do",
        "introduce yourself",
        "Hi!",            # with punctuation
        "HELLO",          # case-insensitive
        "okay.",
    ])
    def test_social_patterns_return_true(self, text):
        assert self._is_social_turn(text) is True, f"Expected social: {text!r}"

    # ── Should return False (factual queries — SQL needed) ──────────
    @pytest.mark.parametrize("text", [
        "Total revenue",          # 2 words, no ? — was incorrectly treated as social
        "Show orders",            # 2 words, no ?
        "Current balance",        # 2 words, no ?
        "Revenue last month",     # 3 words, no ?
        "Tell me about Mahakash", # 4 words, no ? — narrative, still needs RAG
        "What is our revenue?",   # explicit question
        "How many clients do we have?",
        "List all projects",
        "Top performing employees",
        "Sales figures",
    ])
    def test_data_queries_return_false(self, text):
        assert self._is_social_turn(text) is False, f"Expected data query: {text!r}"

    def test_empty_string_returns_false(self):
        assert self._is_social_turn("") is False

    def test_question_with_7_words_returns_false(self):
        # 7 words, no ? — old word-count gate would have caught this; new impl should not
        assert self._is_social_turn("Tell me about revenue last month") is False


# ─── FullContextStore ─────────────────────────────────────────────


class TestFullContextStore:
    """FullContextStore loads all .txt files at init and returns full text on every query."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from sureline.conversation.rag import FullContextStore
        self.FullContextStore = FullContextStore

    def test_loads_docs_and_returns_full_context(self, tmp_path):
        (tmp_path / "doc1.txt").write_text("Alpha story content.", encoding="utf-8")
        (tmp_path / "doc2.txt").write_text("Beta story content.", encoding="utf-8")

        store = self.FullContextStore(docs_dir=tmp_path)
        result = store.get_context_string("any question")

        assert "doc1.txt" in result
        assert "Alpha story content." in result
        assert "doc2.txt" in result
        assert "Beta story content." in result
        assert result.startswith("Complete company knowledge base:")

    def test_empty_dir_returns_fallback(self, tmp_path):
        store = self.FullContextStore(docs_dir=tmp_path)
        result = store.get_context_string("anything")
        assert result == "No company documents available."

    def test_index_documents_is_noop_returns_char_count(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
        store = self.FullContextStore(docs_dir=tmp_path)
        count = store.index_documents(force_reindex=True)
        # Returns char count of loaded text (not 0, not crashing)
        assert count > 0

    def test_get_context_ignores_question_and_n_results(self, tmp_path):
        (tmp_path / "x.txt").write_text("Full content here.", encoding="utf-8")
        store = self.FullContextStore(docs_dir=tmp_path)
        r1 = store.get_context_string("question A", n_results=1)
        r2 = store.get_context_string("question B", n_results=10)
        # Full-context mode always returns the same text regardless of query
        assert r1 == r2


# ─── create_context_store factory ────────────────────────────────


class TestCreateContextStore:
    """Factory returns WikiStore > FullContextStore > RAGStore based on available data."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from sureline.conversation.rag import create_context_store, FULL_CONTEXT_CHAR_LIMIT
        from sureline.conversation.rag import FullContextStore, RAGStore
        self.create_context_store = create_context_store
        self.FULL_CONTEXT_CHAR_LIMIT = FULL_CONTEXT_CHAR_LIMIT
        self.FullContextStore = FullContextStore
        self.RAGStore = RAGStore

    def test_returns_full_context_store_for_small_docs(self, tmp_path):
        (tmp_path / "docs").mkdir()
        docs_dir = tmp_path / "docs"
        (docs_dir / "a.txt").write_text("Short doc.", encoding="utf-8")

        store = self.create_context_store(docs_dir=docs_dir)
        assert isinstance(store, self.FullContextStore)

    def test_returns_full_context_store_when_no_docs(self, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        store = self.create_context_store(docs_dir=docs_dir)
        assert isinstance(store, self.FullContextStore)

    def test_returns_rag_store_for_large_docs(self, tmp_path):
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        # Write a file large enough to exceed FULL_CONTEXT_CHAR_LIMIT
        big_text = "x" * (self.FULL_CONTEXT_CHAR_LIMIT + 1)
        (docs_dir / "big.txt").write_text(big_text, encoding="utf-8")

        with patch("sureline.conversation.rag.RAGStore.__init__", return_value=None) as mock_init:
            with patch("sureline.conversation.rag.RAGStore.index_documents", return_value=0):
                store = self.create_context_store(docs_dir=docs_dir)
        assert isinstance(store, self.RAGStore)

    def test_returns_wiki_store_when_wiki_dir_exists_with_pages(self, tmp_path):
        wiki_dir = tmp_path / "wiki" / "testclient"
        wiki_dir.mkdir(parents=True)
        # A content page (non-index) triggers WikiStore
        (wiki_dir / "page1.md").write_text(
            "---\ntitle: Test\nslug: test\npriority: 1\ntags: [test]\n---\nBody.",
            encoding="utf-8",
        )

        from sureline.conversation.wiki import WikiStore
        store = self.create_context_store(wiki_dir=wiki_dir)
        assert isinstance(store, WikiStore)

    def test_wiki_dir_with_only_index_falls_through_to_full_context(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (wiki_dir / "index.md").write_text("# Index", encoding="utf-8")
        (docs_dir / "a.txt").write_text("Short doc.", encoding="utf-8")

        store = self.create_context_store(docs_dir=docs_dir, wiki_dir=wiki_dir)
        assert isinstance(store, self.FullContextStore)


# ─── has_cloud_llm_key ───────────────────────────────────────────


class TestHasCloudLlmKey:
    """has_cloud_llm_key returns True iff any cloud LLM key is set."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from sureline.config import has_cloud_llm_key
        self.has_cloud_llm_key = has_cloud_llm_key

    def test_azure_key_returns_true(self, monkeypatch):
        import sureline.config as cfg
        monkeypatch.setattr(cfg, "AZURE_OPENAI_API_KEY", "az-key", raising=True)
        monkeypatch.setattr(cfg, "OPENAI_API_KEY", "", raising=True)
        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "", raising=True)
        assert self.has_cloud_llm_key() is True

    def test_openai_key_returns_true(self, monkeypatch):
        import sureline.config as cfg
        monkeypatch.setattr(cfg, "AZURE_OPENAI_API_KEY", "", raising=True)
        monkeypatch.setattr(cfg, "OPENAI_API_KEY", "sk-test", raising=True)
        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "", raising=True)
        assert self.has_cloud_llm_key() is True

    def test_gemini_key_returns_true(self, monkeypatch):
        import sureline.config as cfg
        monkeypatch.setattr(cfg, "AZURE_OPENAI_API_KEY", "", raising=True)
        monkeypatch.setattr(cfg, "OPENAI_API_KEY", "", raising=True)
        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "gem-key", raising=True)
        assert self.has_cloud_llm_key() is True

    def test_all_empty_returns_false(self, monkeypatch):
        import sureline.config as cfg
        monkeypatch.setattr(cfg, "AZURE_OPENAI_API_KEY", "", raising=True)
        monkeypatch.setattr(cfg, "OPENAI_API_KEY", "", raising=True)
        monkeypatch.setattr(cfg, "GEMINI_API_KEY", "", raising=True)
        assert self.has_cloud_llm_key() is False


# ─── SurelineContextProcessor echo gate ──────────────────────────


class TestEchoGate:
    """
    SurelineContextProcessor echo gate: two-layer guard (bot_speaking + cooldown).

    These tests use an isolated fake engine + fake processor so they don't
    require a running Pipecat pipeline or real LLM connections.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        from pipeline import SurelineContextProcessor

        # Minimal engine stub
        engine = MagicMock()
        engine.filler_phrase = "Let me check..."
        engine._get_session.return_value = MagicMock()
        engine.rag = MagicMock()
        engine.query_engine = MagicMock()

        self.processor = SurelineContextProcessor(engine=engine, timing=None, emitter=None)

    @pytest.mark.asyncio
    async def test_transcript_while_bot_speaking_is_dropped(self):
        from pipecat.frames.frames import BotSpeakingFrame, TextFrame
        proc = self.processor

        # Signal bot started speaking
        await proc.process_frame(BotSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert proc._bot_speaking is True

        # User speaks — should be silently dropped (no task created)
        await proc.process_frame(TextFrame(text="Total revenue"), FrameDirection.DOWNSTREAM)
        assert proc._task is None

    @pytest.mark.asyncio
    async def test_bot_stopped_clears_speaking_flag(self):
        from pipecat.frames.frames import BotSpeakingFrame, BotStoppedSpeakingFrame
        proc = self.processor

        await proc.process_frame(BotSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert proc._bot_speaking is True

        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert proc._bot_speaking is False

    @pytest.mark.asyncio
    async def test_interruption_clears_bot_speaking(self):
        """Barge-in via InterruptionFrame must reset _bot_speaking=False.
        Without this fix the agent goes permanently deaf after the first interruption."""
        from pipecat.frames.frames import BotSpeakingFrame, InterruptionFrame
        proc = self.processor

        await proc.process_frame(BotSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert proc._bot_speaking is True

        await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        assert proc._bot_speaking is False
        assert proc._echo_cooldown_until == 0.0

    @pytest.mark.asyncio
    async def test_transcript_after_cooldown_expires_creates_task(self):
        from pipecat.frames.frames import TextFrame
        proc = self.processor

        # Simulate a post-TTS cooldown that has already expired
        proc._bot_speaking = False
        proc._echo_cooldown_until = time.monotonic() - 1.0  # expired 1 second ago

        # Should pass through and create a task
        with patch.object(proc, "_enrich_and_push", new_callable=AsyncMock) as mock_enrich:
            await proc.process_frame(TextFrame(text="Show me revenue"), FrameDirection.DOWNSTREAM)
        # Task was created (asyncio.create_task wraps _enrich_and_push)
        assert proc._task is not None

    @pytest.mark.asyncio
    async def test_transcript_within_cooldown_is_dropped(self):
        from pipecat.frames.frames import TextFrame
        proc = self.processor

        proc._bot_speaking = False
        proc._echo_cooldown_until = time.monotonic() + 10.0  # still active

        await proc.process_frame(TextFrame(text="Show me revenue"), FrameDirection.DOWNSTREAM)
        assert proc._task is None

    @pytest.mark.asyncio
    async def test_duration_proportional_cooldown_is_computed(self):
        """Cooldown after BotStoppedSpeakingFrame should exceed the base minimum."""
        from pipecat.frames.frames import BotSpeakingFrame, BotStoppedSpeakingFrame
        from pipeline import _ECHO_COOLDOWN_BASE
        proc = self.processor

        await proc.process_frame(BotSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)  # simulate brief speaking
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

        # Cooldown should be set to at least the base
        remaining = proc._echo_cooldown_until - time.monotonic()
        assert remaining >= _ECHO_COOLDOWN_BASE - 0.1  # small tolerance for execution time


# ─── ResponseEmitterProcessor token tracking ─────────────────────


class TestResponseEmitterProcessor:
    """ResponseEmitterProcessor accumulates tokens and clears between turns."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from pipeline import ResponseEmitterProcessor
        self.ResponseEmitterProcessor = ResponseEmitterProcessor

    @pytest.mark.asyncio
    async def test_accumulates_tokens_and_builds_word_set(self):
        from pipecat.frames.frames import TextFrame, LLMFullResponseEndFrame
        proc = self.ResponseEmitterProcessor()
        proc._next_processor = None  # bypass push_frame for unit test

        # Simulate streaming tokens
        frames = [TextFrame(text=t) for t in ["Mahakash ", "has ", "delivered ", "40 ", "projects."]]
        for f in frames:
            with patch.object(proc, "push_frame", new_callable=AsyncMock):
                await proc.process_frame(f, FrameDirection.DOWNSTREAM)

        with patch.object(proc, "push_frame", new_callable=AsyncMock):
            await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

        # Word set from the full response
        words = proc.last_response_words
        assert "mahakash" in words
        assert "delivered" in words
        assert "40" in words

        # Tokens list is cleared after end frame
        assert proc._tokens == []

    @pytest.mark.asyncio
    async def test_per_turn_clear_no_cross_contamination(self):
        """Words from turn 1 must not appear in last_response_words after turn 2."""
        from pipecat.frames.frames import TextFrame, LLMFullResponseEndFrame
        proc = self.ResponseEmitterProcessor()

        async def _push(frame):
            with patch.object(proc, "push_frame", new_callable=AsyncMock):
                await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

        # Turn 1
        await _push(TextFrame(text="Revenue is fifty crores."))
        await _push(LLMFullResponseEndFrame())
        assert "revenue" in proc.last_response_words

        # Turn 2
        await _push(TextFrame(text="The latest project is for ISRO."))
        await _push(LLMFullResponseEndFrame())

        # Turn 1 words gone, turn 2 words present
        assert "revenue" not in proc.last_response_words
        assert "isro" in proc.last_response_words


# ─── RAGStore null-collection guard ──────────────────────────────


class TestRAGStoreNullCollection:
    """RAGStore.index_documents should not crash when ChromaDB failed to initialize."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from sureline.conversation.rag import RAGStore
        self.RAGStore = RAGStore

    def test_index_documents_returns_zero_when_collection_is_none(self, tmp_path):
        with patch("chromadb.PersistentClient", side_effect=Exception("DB locked")):
            store = self.RAGStore(persist_dir=tmp_path)

        # ChromaDB failed — collection should be None
        assert store.collection is None

        # Must not raise, must return 0
        result = store.index_documents(force_reindex=False)
        assert result == 0

    def test_get_context_string_returns_empty_when_collection_is_none(self, tmp_path):
        with patch("chromadb.PersistentClient", side_effect=Exception("DB locked")):
            store = self.RAGStore(persist_dir=tmp_path)

        result = store.get_context_string("any question")
        assert result == "No relevant company documents found for this question."


# ─── _ResilientSarvamSTTService loop capture ────────────────────


# ─── WikiStore & _parse_frontmatter ──────────────────────────────


class TestParseFrontmatter:
    """Unit tests for wiki.py's hand-rolled frontmatter parser."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from sureline.conversation.wiki import _parse_frontmatter
        self._parse = _parse_frontmatter

    def test_full_frontmatter_parsed(self):
        text = "---\ntitle: \"BrahMos Incident\"\nslug: brahmos\npriority: 1\ntags: [brahmos, ramesh, fridge]\n---\nBody text here."
        meta, body = self._parse(text)
        assert meta["title"] == "BrahMos Incident"
        assert meta["slug"] == "brahmos"
        assert meta["priority"] == 1
        assert "brahmos" in meta["tags"]
        assert body == "Body text here."

    def test_no_frontmatter_returns_empty_dict(self):
        text = "Just plain body text, no frontmatter."
        meta, body = self._parse(text)
        assert meta == {}
        assert "plain body" in body

    def test_unclosed_frontmatter_returns_empty_dict(self):
        """Frontmatter that never closes should fall through gracefully."""
        text = "---\ntitle: Oops\n"  # no closing ---
        meta, body = self._parse(text)
        assert meta == {}

    def test_integer_value_parsed(self):
        text = "---\npriority: 2\n---\nBody."
        meta, _ = self._parse(text)
        assert meta["priority"] == 2
        assert isinstance(meta["priority"], int)

    def test_list_value_parsed(self):
        text = "---\ntags: [alpha, beta, gamma]\n---\nBody."
        meta, _ = self._parse(text)
        assert meta["tags"] == ["alpha", "beta", "gamma"]

    def test_empty_tags_list_parsed(self):
        text = "---\ntags: []\n---\nBody."
        meta, _ = self._parse(text)
        assert meta["tags"] == []


class TestWikiStoreRetrieval:
    """Tests for WikiStore page loading and context retrieval."""

    def _make_page(self, tmp_path, filename: str, title: str, tags: list[str], body: str):
        tags_str = ", ".join(tags)
        content = f"---\ntitle: \"{title}\"\nslug: {filename.replace('.md','')}\npriority: 1\ntags: [{tags_str}]\n---\n{body}"
        (tmp_path / filename).write_text(content, encoding="utf-8")

    def test_get_context_returns_relevant_page(self, tmp_path):
        from sureline.conversation.wiki import WikiStore
        self._make_page(tmp_path, "brahmos.md", "BrahMos Incident", ["brahmos", "ramesh"], "The BrahMos story is about Ramesh.")
        self._make_page(tmp_path, "hr.md", "HR Policies", ["leave", "holidays"], "Employees get 24 days of annual leave.")
        store = WikiStore(wiki_dir=tmp_path)
        ctx = store.get_context_string("Tell me about BrahMos")
        assert "BrahMos" in ctx

    def test_get_context_no_match_returns_fallback(self, tmp_path):
        from sureline.conversation.wiki import WikiStore
        self._make_page(tmp_path, "hr.md", "HR Policies", ["leave"], "Leave policy details.")
        store = WikiStore(wiki_dir=tmp_path)
        ctx = store.get_context_string("xyzzy completely irrelevant query zzz")
        assert "No relevant" in ctx

    def test_index_documents_returns_page_count(self, tmp_path):
        from sureline.conversation.wiki import WikiStore
        self._make_page(tmp_path, "page1.md", "P1", ["tag1"], "Body 1.")
        self._make_page(tmp_path, "page2.md", "P2", ["tag2"], "Body 2.")
        store = WikiStore(wiki_dir=tmp_path)
        assert store.index_documents() == 2

    def test_force_reindex_reloads_pages(self, tmp_path):
        from sureline.conversation.wiki import WikiStore
        self._make_page(tmp_path, "page1.md", "P1", ["tag1"], "Body 1.")
        store = WikiStore(wiki_dir=tmp_path)
        assert len(store._pages) == 1
        # Add a second page after initial load
        self._make_page(tmp_path, "page2.md", "P2", ["tag2"], "Body 2.")
        store.index_documents(force_reindex=True)
        assert len(store._pages) == 2

    def test_index_file_excluded(self, tmp_path):
        """index.md should not be loaded as a knowledge page."""
        from sureline.conversation.wiki import WikiStore
        self._make_page(tmp_path, "page1.md", "P1", ["tag1"], "Body.")
        (tmp_path / "index.md").write_text("# Index\n- [P1](page1.md)", encoding="utf-8")
        store = WikiStore(wiki_dir=tmp_path)
        slugs = [p.slug for p in store._pages]
        assert "index" not in slugs
        assert "page1" in slugs

    def test_multi_word_tag_match_scores_high(self, tmp_path):
        """A multi-word tag like 'wing commander' should score higher than no match."""
        from sureline.conversation.wiki import WikiStore
        self._make_page(tmp_path, "rank.md", "Wing Commander", ["wing commander", "rank"], "A military rank.")
        self._make_page(tmp_path, "other.md", "Unrelated", ["finance", "budget"], "Budget info.")
        store = WikiStore(wiki_dir=tmp_path)
        ctx = store.get_context_string("What is a wing commander?")
        assert "Wing Commander" in ctx


# ─── _ResilientSarvamSTTService loop capture ────────────────────


class TestResilientSarvamReconnectLambda:
    """
    The STT reconnect lambda must capture the event loop at schedule time,
    not at callback execution time.
    """

    def test_lambda_captures_loop_at_schedule_time(self):
        """
        Verify the lambda uses a captured loop variable (l=_loop) not
        asyncio.get_running_loop() at call time. This is a static code inspection test
        since we can't easily run the Sarvam WebSocket in tests.
        """
        import inspect
        import sureline.stt.stt_module as stt_mod
        source = inspect.getsource(stt_mod)
        # The old bug: lambda calls asyncio.get_running_loop() inside itself
        # The fix: captures loop before call_later as `_loop`
        assert "lambda l=_loop:" in source, (
            "STT reconnect lambda should use 'lambda l=_loop: l.create_task(...)' "
            "to capture the loop at schedule time, not at callback execution time."
        )
