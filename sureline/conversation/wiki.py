"""
Sureline — LLM Wiki Store

Replaces chunked RAG and full-context injection with a curated wiki:
pre-built, narrative markdown pages retrieved by keyword scoring at query time.

WHY THIS IS BETTER THAN RAG:
  - RAG chunks raw documents at character boundaries — it splits stories mid-sentence,
    strips narrative coherence, and returns fragments. The LLM has to reconstruct.
  - Full-context injection works for small doc sets but injects everything regardless
    of relevance, and doesn't scale to multi-client deployments.
  - The wiki is pre-curated: each page is a self-contained, narrative-coherent topic
    unit (e.g. "The BrahMos Incident" is one page, not spread across 4 chunks).
    Retrieval finds the right PAGE and the LLM gets the complete story.

RETRIEVAL:
  Keyword scoring over pre-loaded pages — no vector DB, no embedding model.
  Sub-millisecond latency. Better precision on proper nouns (Ramesh, BrahMos)
  than semantic embeddings, because exact tag matching > cosine similarity for
  domain-specific named entities.

PAGE FORMAT:
  Each wiki page is a .md file with YAML frontmatter:
    ---
    title: "Page Title"
    slug: page_slug
    priority: 1          # 1=most important, higher=less
    tags: [tag1, tag2]   # curated keyword list for retrieval
    ---
    Narrative body text...

SCALABILITY:
  Each client gets their own wiki directory: docs/wiki/{client_id}/
  Adding a new client = adding a new directory and pages.
  WikiStore is instantiated per-client by create_context_store() in rag.py.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Pages with score below this threshold are excluded from results.
# 0.3 = at least one meaningful tag match must occur.
_MIN_SCORE = 0.3

# Stop words to skip during tokenisation (single chars + common 2-letter words)
_STOP_WORD_MIN_LEN = 3


@dataclass
class WikiPage:
    """A single wiki page loaded from a .md file."""
    title: str
    slug: str
    priority: int               # 1 = highest, used as tiebreaker
    tags: list[str]             # lowercased tag list
    body: str                   # full markdown body (frontmatter stripped)
    source_file: str            # filename, for context string headers
    _tag_set: frozenset = field(init=False, repr=False)

    def __post_init__(self):
        self._tag_set = frozenset(self.tags)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from a markdown file.

    Returns (metadata_dict, body_text). No external dependency — handles
    the simple subset of YAML used in wiki pages (strings, ints, lists).
    """
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    fm_block = text[3:end].strip()
    body = text[end + 4:].strip()

    meta: dict = {}
    for line in fm_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw_val = line.partition(":")
        key = key.strip()
        val = raw_val.strip()

        # List value: [a, b, c]
        if val.startswith("[") and val.endswith("]"):
            items = val[1:-1].split(",")
            meta[key] = [i.strip().strip('"').strip("'") for i in items if i.strip()]
        # Integer
        elif val.isdigit():
            meta[key] = int(val)
        # String (strip surrounding quotes)
        else:
            meta[key] = val.strip('"').strip("'")

    return meta, body


def _tokenise(text: str) -> list[str]:
    """
    Extract meaningful tokens from text for keyword scoring.

    Uses word-boundary regex, lowercases, and drops tokens shorter than
    _STOP_WORD_MIN_LEN to eliminate noise without requiring NLTK.
    Multi-word tag matching (e.g. "wing commander") is handled separately.
    """
    return [t for t in re.findall(r"\b[a-z]{3,}\b", text.lower())]


class WikiStore:
    """
    LLM Wiki-backed context store — retrieves pre-curated narrative pages
    by keyword scoring rather than chunked vector search.

    Interface is identical to FullContextStore and RAGStore:
      get_context_string(question, n_results) → str
      index_documents(force_reindex)          → int
    """

    def __init__(self, wiki_dir: Path):
        self.wiki_dir = wiki_dir
        self._pages: list[WikiPage] = []
        self._load()

    def _load(self) -> None:
        """Read all .md files from wiki_dir, parse frontmatter, build page list."""
        md_files = sorted(
            f for f in self.wiki_dir.glob("*.md")
            if f.stem not in ("index", "log")  # index/log are meta-files, not knowledge pages
        )

        if not md_files:
            logger.warning("WikiStore: no .md pages found in %s", self.wiki_dir)
            return

        pages = []
        for f in md_files:
            try:
                raw = f.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(raw)

                title = meta.get("title", f.stem.replace("_", " ").title())
                slug = meta.get("slug", f.stem)
                priority = int(meta.get("priority", 3))
                tags_raw = meta.get("tags", [])
                tags = [t.lower().strip() for t in tags_raw if t.strip()]

                page = WikiPage(
                    title=title,
                    slug=slug,
                    priority=priority,
                    tags=tags,
                    body=body,
                    source_file=f.name,
                )
                pages.append(page)

            except Exception as e:
                logger.warning("WikiStore: failed to load %s: %s", f.name, e)

        self._pages = pages
        total_chars = sum(len(p.body) for p in pages)
        logger.info(
            "WikiStore: loaded %d pages (%d chars) from %s",
            len(pages), total_chars, self.wiki_dir,
        )

    def _score(self, question: str, page: WikiPage) -> float:
        """
        Score a page against a question using weighted keyword matching.

        Scoring layers:
          - Exact tag match (O(1) via frozenset):  2.0 pts × position_bonus
          - Substring tag match (e.g. "brahmos" in "brahmos incident"):  1.0 pt × position_bonus
          - Multi-word tag match (e.g. "wing commander"):  2.5 pts (no position bonus)
          - Body text match:  0.4 pts × position_bonus (fallback)
          - Priority tiebreaker: 0.05 × (4 - priority)

        Position bonus: 1.3x for tokens at positions 0-2 (front-loaded query terms
        signal the primary topic — e.g. "Ramesh — tell me what happened")
        """
        q_lower = question.lower()
        tokens = _tokenise(q_lower)
        score = 0.0

        for i, token in enumerate(tokens):
            if len(token) < _STOP_WORD_MIN_LEN:
                continue
            pos_mult = 1.3 if i < 3 else 1.0

            if token in page._tag_set:
                # Exact tag match — highest confidence
                score += 2.0 * pos_mult
            elif any(token in tag for tag in page.tags):
                # Substring match within a tag (e.g. "fridge" in "isro fridge")
                score += 1.0 * pos_mult
            # Body-text fallback removed: long pages accumulate more body-match
            # points than short, precisely-tagged pages, skewing results toward
            # verbose pages even when a short, well-tagged page is the right answer.

        # Multi-word tag matching: check if any tag phrase appears verbatim in question
        # This catches "wing commander", "permanent deputation", "paneer tikka" etc.
        for tag in page.tags:
            if " " in tag and tag in q_lower:
                score += 2.5  # high weight — multi-word exact match is unambiguous

        # Priority tiebreaker (tiny, only resolves near-ties)
        score += 0.05 * (4 - page.priority)

        return score

    def _retrieve(self, question: str, n_results: int) -> list[WikiPage]:
        """Score all pages against the question, return top n above threshold."""
        scored = [(self._score(question, p), p) for p in self._pages]
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for s, p in scored[:n_results]:
            if s >= _MIN_SCORE:
                logger.debug("WikiStore: '%s' → %.2f", p.slug, s)
                results.append(p)
            else:
                logger.debug("WikiStore: '%s' → %.2f (below threshold, excluded)", p.slug, s)

        if not results:
            # Log top candidates even when nothing passes threshold — aids tuning
            top = scored[:3]
            logger.info(
                "WikiStore: no pages above threshold %.1f for query %r. "
                "Top candidates: %s",
                _MIN_SCORE,
                question[:60],
                [(p.slug, f"{s:.2f}") for s, p in top],
            )

        return results

    def index_documents(self, force_reindex: bool = False) -> int:
        """
        No-op for WikiStore — pages are pre-built, loaded once at __init__.
        force_reindex=True re-reads from disk (for hot-reload after wiki edits).
        Returns number of pages loaded.
        """
        if force_reindex:
            self._pages = []
            self._load()
        return len(self._pages)

    def get_context_string(self, question: str, n_results: int = 3) -> str:
        """
        Retrieve the top relevant wiki pages and return them as a context string.

        The format mirrors FullContextStore's output so the system prompt in
        conversation_engine.py requires no changes.
        """
        pages = self._retrieve(question, n_results)

        if not pages:
            return "No relevant company information found for this question."

        parts = ["Relevant company information:"]
        for page in pages:
            parts.append(f"[From: {page.title}]\n{page.body}")

        return "\n\n".join(parts)


if __name__ == "__main__":
    """Quick smoke test — run from project root: python -m sureline.conversation.wiki"""
    from sureline.config import PROJECT_ROOT
    wiki_dir = PROJECT_ROOT / "docs" / "wiki" / "mahakash"

    store = WikiStore(wiki_dir=wiki_dir)
    print(f"\nLoaded {len(store._pages)} pages.\n")

    test_questions = [
        "Tell me about the BrahMos incident",
        "What happened with Ramesh?",
        "How many days of annual leave do we get?",
        "What is the revenue for Q4?",
        "Tell me about Pushpak Mk-II",
        "What is Mahakash's culture like?",
        "Who are your top clients?",
        "Tell me about the ISRO fridge story",
        "What are your unacknowledged achievements?",
    ]

    for q in test_questions:
        print(f"Q: {q}")
        pages = store._retrieve(q, n_results=2)
        for p in pages:
            score = store._score(q, p)
            print(f"   -> [{score:.2f}] {p.title}")
        print()
