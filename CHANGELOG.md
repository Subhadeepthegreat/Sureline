# Changelog

All notable changes to Sureline are documented here.

Format: [Semantic Versioning](https://semver.org/) — MAJOR.MINOR.PATCH.MICRO

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
