"""Split raw doc files into overlapping chunks ready for embedding.

This file only knows about text -> chunks. It doesn't know about
embeddings or ChromaDB; vector_store.py handles that.
"""

from dataclasses import dataclass
from pathlib import Path

from langchain_text_splitters import MarkdownTextSplitter

from frameworksync.config import RAW_DOCS_DIR

CHUNK_SIZE = 800       # characters per chunk
CHUNK_OVERLAP = 150    # characters shared between consecutive chunks


@dataclass(frozen=True)
class Chunk:
    """One piece of a doc, with enough metadata to trace it back."""

    text: str
    library: str       # e.g. "langchain" — used to filter retrieval later
    source_name: str   # e.g. "langchain-v1-migration"
    chunk_index: int   # position within that source file


def chunk_file(path: Path, library: str, source_name: str) -> list[Chunk]:
    """Split one markdown file into a list of Chunks."""
    text = path.read_text(encoding="utf-8")

    splitter = MarkdownTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    pieces = splitter.split_text(text)

    return [
        Chunk(text=piece, library=library, source_name=source_name, chunk_index=i)
        for i, piece in enumerate(pieces)
    ]


def chunk_all_docs() -> list[Chunk]:
    """Walk data/raw_docs/<library>/*.md and chunk every file found."""
    all_chunks: list[Chunk] = []

    for library_dir in sorted(RAW_DOCS_DIR.iterdir()):
        if not library_dir.is_dir():
            continue  # skips manifest.json, which sits next to the folders

        library = library_dir.name
        for md_file in sorted(library_dir.glob("*.md")):
            source_name = md_file.stem
            chunks = chunk_file(md_file, library=library, source_name=source_name)
            all_chunks.extend(chunks)
            print(f"{source_name}: {len(chunks)} chunks")

    return all_chunks