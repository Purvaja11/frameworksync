"""Entry point: chunk all raw docs and load them into ChromaDB.

Run with:  uv run python -m frameworksync.run_ingestion
"""

from frameworksync.chunker import chunk_all_docs
from frameworksync.vector_store import upsert_chunks


def run() -> None:
    chunks = chunk_all_docs()
    print(f"\nEmbedding and storing {len(chunks)} chunks ...")
    upsert_chunks(chunks)
    print("Done.")


if __name__ == "__main__":
    run()