"""
Sureline — RAG (Retrieval-Augmented Generation) Module

Two modes, selected automatically based on total document size:

  FULL CONTEXT mode  (default for small doc sets, ≤ FULL_CONTEXT_CHAR_LIMIT)
    All documents are loaded in full and injected into the system prompt on
    every call. The LLM sees everything — no chunking, no retrieval. Best for
    narrative-rich docs where chunking destroys story coherence and humour.

  RAG mode  (automatic fallback for large doc sets)
    ChromaDB vector store chunks and retrieves relevant passages. Best when
    the total document corpus exceeds the LLM's practical context budget.

For the Mahakash PoC (4 docs, ~18 KB, ~4,600 tokens), full context mode is
the correct choice. RAG is a workaround for context window limits — it should
not be used when the docs easily fit in the window.
"""

import logging
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from sureline.config import DOCS_DIR, CHROMA_DIR

logger = logging.getLogger(__name__)

# Chunk settings (RAG mode only)
CHUNK_SIZE = 500     # characters per chunk
CHUNK_OVERLAP = 50   # overlap between chunks
_DEFAULT_COLLECTION_NAME = "mahakash_docs"

# If all docs combined fit under this character limit, use full context injection
# instead of RAG. ~80,000 chars ≈ 20,000 tokens — comfortably under any modern LLM.
FULL_CONTEXT_CHAR_LIMIT = 80_000


class FullContextStore:
    """
    Simple full-document context loader for small doc sets.

    Reads all .txt files from docs_dir once at startup, concatenates them,
    and returns the full text on every query. No vector DB, no chunking.

    Use this when total docs fit comfortably in the LLM's context window —
    the LLM sees every story, every cross-reference, every joke, and can
    narrate naturally without reconstructing fragments.
    """

    def __init__(self, docs_dir: Optional[Path] = None):
        self.docs_dir = docs_dir or DOCS_DIR
        self._full_text: str = ""
        self._load()

    def _load(self) -> None:
        doc_files = sorted(self.docs_dir.glob("*.txt"))
        if not doc_files:
            logger.warning("FullContextStore: no .txt files in %s", self.docs_dir)
            return

        parts = []
        total_chars = 0
        for f in doc_files:
            text = f.read_text(encoding="utf-8")
            parts.append(f"=== {f.name} ===\n{text}")
            total_chars += len(text)

        self._full_text = "\n\n".join(parts)
        logger.info(
            "FullContextStore: loaded %d docs, %d chars (~%d tokens) — full context mode active.",
            len(doc_files), total_chars, total_chars // 4,
        )

    def index_documents(self, force_reindex: bool = False) -> int:  # noqa: ARG002
        """No-op — full context store loads once at __init__. Returns doc char count."""
        return len(self._full_text)

    def get_context_string(self, question: str = "", n_results: int = 3) -> str:  # noqa: ARG002
        """Return full document text. question/n_results ignored — everything is always included."""
        if not self._full_text:
            return "No company documents available."
        return f"Complete company knowledge base:\n\n{self._full_text}"


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks


class RAGStore:
    """
    ChromaDB-backed vector store for company documents.

    Embeds company docs (company overview, policies, product catalog,
    financial reports) and retrieves relevant chunks for any query.
    """

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        docs_dir: Optional[Path] = None,
        client_id: Optional[str] = None,
    ):
        self.persist_dir = persist_dir or CHROMA_DIR
        self.docs_dir = docs_dir or DOCS_DIR
        # Each client gets their own ChromaDB collection — prevents RAG context cross-contamination
        self._collection_name = f"{client_id}_docs" if client_id else _DEFAULT_COLLECTION_NAME

        # ChromaDB with local persistence
        self.client = None
        self.collection = None
        try:
            self.client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            self.collection = self.client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                "RAGStore initialized. Collection '%s' has %d chunks.",
                self._collection_name, self.collection.count(),
            )
        except Exception as e:
            logger.warning(
                "ChromaDB failed to initialize (%s). "
                "RAG context will be disabled — SQL queries still work. "
                "To fix: delete the chroma_db directory and restart.",
                e,
            )

    def index_documents(self, force_reindex: bool = False) -> int:
        """
        Read all .txt files from docs_dir and index them into ChromaDB.

        Args:
            force_reindex: If True, delete existing collection and re-index.

        Returns:
            Number of chunks indexed.
        """
        if self.collection is None:
            logger.warning("RAGStore: ChromaDB unavailable, skipping index_documents.")
            return 0

        if force_reindex:
            self.client.delete_collection(self._collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )

        # Skip if already indexed
        if self.collection.count() > 0 and not force_reindex:
            logger.info(f"Documents already indexed ({self.collection.count()} chunks). Skipping.")
            return self.collection.count()

        doc_files = list(self.docs_dir.glob("*.txt"))
        if not doc_files:
            logger.warning(f"No .txt files found in {self.docs_dir}")
            return 0

        all_chunks = []
        all_ids = []
        all_metadata = []

        for doc_file in doc_files:
            text = doc_file.read_text(encoding="utf-8")
            chunks = _chunk_text(text)

            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc_file.stem}_{i}"
                all_chunks.append(chunk)
                all_ids.append(chunk_id)
                all_metadata.append({
                    "source": doc_file.name,
                    "chunk_index": i,
                })

        if all_chunks:
            # Add in batches (ChromaDB limit)
            batch_size = 100
            for i in range(0, len(all_chunks), batch_size):
                self.collection.add(
                    documents=all_chunks[i:i + batch_size],
                    ids=all_ids[i:i + batch_size],
                    metadatas=all_metadata[i:i + batch_size],
                )

        total = self.collection.count()
        logger.info(f"Indexed {total} chunks from {len(doc_files)} documents.")
        return total

    def query(self, question: str, n_results: int = 3) -> list[dict]:
        """
        Retrieve the most relevant document chunks for a question.

        Args:
            question: The user's question.
            n_results: Number of chunks to return.

        Returns:
            List of dicts with 'text', 'source', and 'distance' fields.
        """
        if self.collection is None or self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[question],
            n_results=min(n_results, self.collection.count()),
        )

        chunks = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                chunks.append({
                    "text": doc,
                    "source": results["metadatas"][0][i].get("source", "unknown"),
                    "distance": results["distances"][0][i] if results.get("distances") else 0,
                })

        return chunks

    def get_context_string(self, question: str, n_results: int = 3, distance_threshold: float = 0.65) -> str:
        """
        Get a formatted context string for LLM injection.

        Only includes chunks whose cosine distance is below distance_threshold.
        Cosine distance in ChromaDB: 0 = identical, 1 = orthogonal, 2 = opposite.
        Chunks above the threshold are more noise than signal and can cause hallucination.
        """
        chunks = self.query(question, n_results)
        relevant = [c for c in chunks if c["distance"] < distance_threshold]

        if not relevant:
            logger.info(
                "RAG: no chunks below threshold %.2f for query %r (best distance: %.3f)",
                distance_threshold,
                question[:60],
                chunks[0]["distance"] if chunks else float("inf"),
            )
            return "No relevant company documents found for this question."

        parts = ["Relevant company information:"]
        for c in relevant:
            parts.append(f"[From {c['source']}]\n{c['text']}")

        return "\n\n".join(parts)


def create_context_store(
    docs_dir: Optional[Path] = None,
    client_id: Optional[str] = None,
    persist_dir: Optional[Path] = None,
    wiki_dir: Optional[Path] = None,
) -> "WikiStore | FullContextStore | RAGStore":
    """
    Factory: auto-select the best context store for the available data.

    Priority order:
      1. WikiStore  — if docs/wiki/{client_id}/ exists with .md pages
         Pre-curated narrative pages, keyword-scored retrieval. Best for
         voice agents — each page is a coherent story, not a 500-char chunk.

      2. FullContextStore — if total docs fit within FULL_CONTEXT_CHAR_LIMIT
         Entire doc set in system prompt. Works for small corpora.

      3. RAGStore — fallback for large doc sets
         Chunked ChromaDB vector search.
    """
    from sureline.conversation.wiki import WikiStore
    from sureline.config import PROJECT_ROOT

    # ── 1. WikiStore (preferred) ─────────────────────────────────
    _wiki_dir = wiki_dir
    if _wiki_dir is None and client_id:
        _wiki_dir = PROJECT_ROOT / "docs" / "wiki" / client_id
    if _wiki_dir and _wiki_dir.exists():
        pages = list(_wiki_dir.glob("*.md"))
        content_pages = [p for p in pages if p.stem not in ("index", "log")]
        if content_pages:
            logger.info(
                "create_context_store: wiki found at %s (%d pages) — using WikiStore.",
                _wiki_dir, len(content_pages),
            )
            return WikiStore(wiki_dir=_wiki_dir)

    # ── 2. FullContextStore (small doc sets) ─────────────────────
    _docs_dir = docs_dir or DOCS_DIR
    total_chars = sum(
        len(f.read_text(encoding="utf-8"))
        for f in _docs_dir.glob("*.txt")
    )

    if total_chars <= FULL_CONTEXT_CHAR_LIMIT:
        logger.info(
            "create_context_store: %d chars, no wiki — using FullContextStore.",
            total_chars,
        )
        return FullContextStore(docs_dir=_docs_dir)

    # ── 3. RAGStore (large doc sets) ─────────────────────────────
    logger.info(
        "create_context_store: %d chars exceeds limit (%d), no wiki — using RAGStore.",
        total_chars, FULL_CONTEXT_CHAR_LIMIT,
    )
    return RAGStore(docs_dir=_docs_dir, client_id=client_id, persist_dir=persist_dir)


if __name__ == "__main__":
    store = create_context_store()
    print(f"Store type: {type(store).__name__}")
    if isinstance(store, RAGStore):
        count = store.index_documents(force_reindex=True)
        print(f"Indexed {count} chunks.")
        test_q = "What is Mahakash's leave policy?"
        results = store.query(test_q)
        print(f"\nQuery: {test_q}")
        for r in results:
            print(f"  [{r['source']}] {r['text'][:100]}...")
    else:
        ctx = store.get_context_string("What are Mahakash's unacknowledged achievements?")
        print(f"\nFull context preview (first 500 chars):\n{ctx[:500]}...")
