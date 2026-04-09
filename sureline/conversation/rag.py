"""
Sureline — RAG (Retrieval-Augmented Generation) Module

Uses ChromaDB to store and retrieve relevant Mahakash company document
chunks for augmenting LLM responses.
"""

import logging
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from sureline.config import DOCS_DIR, CHROMA_DIR

logger = logging.getLogger(__name__)

# Chunk settings
CHUNK_SIZE = 500     # characters per chunk
CHUNK_OVERLAP = 50   # overlap between chunks
_DEFAULT_COLLECTION_NAME = "mahakash_docs"


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

    def index_documents(self, force_reindex: bool = False) -> int:
        """
        Read all .txt files from docs_dir and index them into ChromaDB.

        Args:
            force_reindex: If True, delete existing collection and re-index.

        Returns:
            Number of chunks indexed.
        """
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
        if self.collection.count() == 0:
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

    def get_context_string(self, question: str, n_results: int = 3) -> str:
        """
        Get a formatted context string for LLM injection.
        """
        chunks = self.query(question, n_results)
        if not chunks:
            return "No relevant company documents found."

        parts = ["Relevant company information:"]
        for c in chunks:
            parts.append(f"[From {c['source']}]\n{c['text']}")

        return "\n\n".join(parts)


if __name__ == "__main__":
    store = RAGStore()
    count = store.index_documents(force_reindex=True)
    print(f"Indexed {count} chunks.")

    # Test query
    test_q = "What is Mahakash's leave policy?"
    results = store.query(test_q)
    print(f"\nQuery: {test_q}")
    for r in results:
        print(f"  [{r['source']}] {r['text'][:100]}...")
