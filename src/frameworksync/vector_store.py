"""Embed chunks and store/query them in a local ChromaDB collection.

This file knows about embeddings and storage. It doesn't know how
files were chunked (chunker.py) or where docs came from (config.py's
DOC_SOURCES) — it just takes Chunk objects and indexes them.
"""

import chromadb
from chromadb.utils import embedding_functions

from frameworksync.chunker import Chunk
from frameworksync.config import CHROMA_DIR

COLLECTION_NAME = "framework_docs"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

_embedding_fn = None
_collection = None


def _get_embedding_fn():
    """Load the embedding model on first use, not at import time."""
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
    return _embedding_fn


def get_collection():
    """Open (or create) the on-disk ChromaDB collection (opened once, reused)."""
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_get_embedding_fn(),
            metadata={"hnsw:space": "cosine"},  # similarity metric for retrieval
        )
    return _collection


def _chunk_id(chunk: Chunk) -> str:
    """A stable, unique ID so re-running ingestion updates instead of duplicates."""
    return f"{chunk.source_name}::{chunk.chunk_index}"


def upsert_chunks(chunks: list[Chunk]) -> None:
    """Embed and store a list of chunks. Safe to re-run (updates, not duplicates)."""
    if not chunks:
        return

    collection = get_collection()
    collection.upsert(
        ids=[_chunk_id(c) for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[
            {
                "library": c.library,
                "source_name": c.source_name,
                "chunk_index": c.chunk_index,
            }
            for c in chunks
        ],
    )


def query(text: str, library: str | None = None, n_results: int = 4) -> list[dict]:
    """Return the n_results chunks most relevant to `text`.

    If `library` is given, only search within that library's docs
    (e.g. only "langchain", ignoring langgraph/llamaindex noise).
    """
    collection = get_collection()
    where = {"library": library} if library else None

    results = collection.query(
        query_texts=[text],
        n_results=n_results,
        where=where,
    )

    return [
        {"text": doc, "metadata": meta, "distance": dist}
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )
    ]