"""Interactive CLI to ask FrameworkSync questions from the terminal.

Run with:  uv run python -m frameworksync.ask
"""

from frameworksync.config import SUPPORTED_LIBRARIES
from frameworksync.rag_chain import ask


def _pick_library() -> str | None:
    print("Filter by library? (press Enter to search all)")
    for i, lib in enumerate(SUPPORTED_LIBRARIES, start=1):
        print(f"  {i}. {lib}")
    choice = input("> ").strip()

    if not choice:
        return None
    try:
        return SUPPORTED_LIBRARIES[int(choice) - 1]
    except (ValueError, IndexError):
        print("Didn't recognize that choice — searching all libraries instead.\n")
        return None


def main() -> None:
    print("FrameworkSync — ask a migration question. Type 'exit' to quit.\n")
    library = _pick_library()
    print()

    while True:
        question = input("Q: ").strip()
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue

        result = ask(question, library=library)
        print(f"\nA: {result.answer}\n")

        if result.sources:
            print("Sources:")
            for s in result.sources:
                print(f"  - {s['metadata']['source_name']} (chunk {s['metadata']['chunk_index']})")
        print()


if __name__ == "__main__":
    main()