"""Central settings for FrameworkSync.

Every other file imports from here, so a path or URL only ever
needs to change in one place.
"""

from dataclasses import dataclass
from pathlib import Path

# __file__ is .../src/frameworksync/config.py
# parents[0] = src/frameworksync, parents[1] = src, parents[2] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DOCS_DIR = DATA_DIR / "raw_docs"   # step 1 writes here
CHROMA_DIR = DATA_DIR / "chroma_db"    # step 2 will use this
PATTERNS_PATH = DATA_DIR / "patterns.json"  # step 6's extracted deprecated-pattern list
VSCODE_RESULTS_PATH = DATA_DIR / "vscode_results.json"


SUPPORTED_LIBRARIES = ("langchain", "langgraph", "llamaindex")

# Maps our library names to the actual Python import prefixes they use.
# e.g. "import langchain.agents" -> starts with "langchain" -> library "langchain"
LIBRARY_IMPORT_PREFIXES = {
    "langchain": ("langchain", "langchain_core", "langchain_classic", "langchain_ollama"),
    "langgraph": ("langgraph",),
    "llamaindex": ("llama_index",),
}

@dataclass(frozen=True)
class DocSource:
    """One documentation page we want to download."""

    library: str  # which framework it belongs to (used later as vector-DB metadata)
    name: str     # short slug, becomes the filename
    url: str      # where to download it


DOC_SOURCES = [
    DocSource(
        "langchain",
        "langchain-v1-migration",
        "https://docs.langchain.com/oss/python/migrate/langchain-v1.md",
    ),
    DocSource(
        "langgraph",
        "langgraph-v1-migration",
        "https://docs.langchain.com/oss/python/migrate/langgraph-v1.md",
    ),
    DocSource(
        "llamaindex",
        "llamaindex-deprecated-terms",
        "https://developers.llamaindex.ai/python/framework/changes/deprecated_terms/index.md",
    ),
    DocSource(
        "llamaindex",
        "llamaindex-settings-migration",
        "https://developers.llamaindex.ai/python/framework/module_guides/supporting_modules/service_context_migration/index.md",
    ),
    DocSource(
    "langchain",
    "langchain-changelog",
    "https://docs.langchain.com/oss/python/releases/changelog.md",
    ),
    DocSource(
        "llamaindex",
        "llamaindex-changelog",
        "https://developers.llamaindex.ai/python/framework/changelog/index.md",
    ),
]