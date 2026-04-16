# Changelog

All notable changes to Sureline are documented here.

Format: [Semantic Versioning](https://semver.org/) — MAJOR.MINOR.PATCH.MICRO

---

## [0.2.0.0] - 2026-04-16

### Added
- **Wiki context store** (`sureline/conversation/wiki.py`) — replaces chunked RAG with
  pre-curated narrative markdown pages retrieved by keyword scoring. Each page is a
  self-contained topic unit; retrieval finds the right page and the LLM gets the full story
- **Full-context RAG mode** — automatically selected when total docs fit under 80,000 chars
  (~20K tokens), injecting all documents into the system prompt instead of chunking. Best
  for small, narrative-rich doc sets where chunking destroys coherence
- **Web UI** (`frontend/index.html`, `web_server.py`) — real-time dashboard at
  `http://127.0.0.1:8080` showing pipeline state, transcripts, latency, and config. WebSocket
  broadcaster at `ws://127.0.0.1:8765` pushes events to all connected browser tabs
- **Mahakash demo wiki** (`docs/wiki/mahakash/`) — 14 curated wiki pages covering company
  identity, departments, launch vehicles, financials, HR policies, and company legends
- **Resilient Sarvam STT** — dead-socket circuit breaker with `_reconnecting` flag prevents
  concurrent reconnect races; rogue frames are silently suppressed until WS reconnects
- **Cloud LLM detection** (`has_cloud_llm_key()`) — `start.py` skips Ollama setup when any
  cloud provider key is configured, auto-opens the browser UI after startup
- **Duration-proportional echo cooldown** — post-TTS mute window scales with utterance
  length to prevent mic echo from short vs. long responses being treated identically
- **Social turn classifier** (`_is_social_turn()`) — regex-anchored whitelist skips the
  SQL/query-engine layer for greetings and chitchat, saving 3–5s per conversational turn
- `filler_phrase` and `client_name` public properties on `ConversationEngine`
- 12 new wiki retrieval tests covering `_parse_frontmatter`, `WikiStore.get_context`,
  force-reindex, multi-word tag scoring, and index-file exclusion

### Changed
- `MockTTSService` is now a proper `FrameProcessor` — can be inserted in the pipeline
  instead of only being callable standalone
- Context store selection is now a factory (`create_context_store()`) that auto-selects
  WikiStore → FullContextStore → RAGStore based on available data
- `RAGStore` body-text fallback scoring removed — scoring now relies only on tag matches,
  preventing long pages from outscoring short, precisely-tagged pages
- `start.py` startup sequence adapts to cloud vs. local LLM — skips Ollama when cloud key present
- `pipeline.py` imports updated from `LLMMessagesUpdateFrame` to `LLMMessagesFrame`

### Fixed
- `_bot_speaking_start` not reset on `InterruptionFrame` — could cause wrong echo cooldown
  duration on next bot turn after a barge-in
- Wiki multi-word tag guard `tag not in tokens` was always True (single-word token list can
  never contain a multi-word phrase) — removed broken guard, multi-word matching now correct
- `websockets.WebSocketServer` deprecated return type annotation causing DeprecationWarning
- Directory traversal in `_SilentHandler` — `translate_path()` now validates all requests
  stay inside `frontend/` before serving
- `.gitignore` extended to exclude API key files, local Claude config, and pip noise

---

## [0.1.0.0] - 2026-04-09

### Added
- Multi-client support wired end-to-end: `SchemaRegistry` now drives `ConversationEngine`,
  `RAGStore`, and `QueryEngine` via `CLIENT_ID` env var at runtime
- Per-client ChromaDB collection (`{client_id}_docs`) — RAG context is now isolated per
  client, preventing cross-contamination between tenants
- `SchemaRegistry.load_all()` with duplicate `client_id` detection across YAML files
- `language` and `filler_phrase` fields in `ClientConfig` — supports en/hi/hinglish/bn
  localization of the filler phrase spoken while queries run
- `caller_verification.table` field in client YAML — the verification table is now
  configurable instead of hardcoded
- LRU query result cache in `QueryEngine` with 5-minute TTL — repeated identical queries
  no longer incur LLM round-trips
- Session TTL eviction in `ConversationEngine` — sessions inactive for 30+ minutes are
  automatically evicted to prevent unbounded memory growth
- 8-second timeout around RAG+SQL `asyncio.gather` with fallback phrase on cloud LLM hangs
- `load_conversation_engine()` factory in `pipeline.py` for clean per-client initialization

### Changed
- `RAGStore` now accepts `client_id` parameter — collection name derived from client context
- `QueryEngine` accepts `csv_path` parameter — no longer defaults unconditionally to
  `sales.csv`
- `ConversationEngine` filler phrase sourced from `ClientConfig.filler_phrase` instead of
  being hardcoded
- Ollama removed as a direct Python dependency — accessed via OpenAI-compat endpoint
  (`openai.AsyncOpenAI` with `base_url=OLLAMA_BASE_URL/v1`)

### Fixed
- SQLite connection leak in `CallerVerificationProcessor._check_db` — connection now
  always closed in `finally` block
- `no_data_query_needed` results were not written to query cache — general knowledge
  answers are now cached on first call
- Silent swallowing of RAG/SQL exceptions — failures now logged as warnings before
  substituting fallback responses
- Periodic session cleanup now triggered automatically every 100 `_get_session` calls

### Removed
- `sureline/llm/ollama_service.py` — dead code, nothing imported it (Ollama accessed via
  OpenAI-compat client in `config.py`)
