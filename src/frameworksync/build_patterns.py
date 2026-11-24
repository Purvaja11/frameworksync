"""Entry point: extract deprecated patterns from every raw doc file.

Run with:  uv run python -m frameworksync.build_patterns
"""

import json
from dataclasses import asdict

from frameworksync.config import PATTERNS_PATH, RAW_DOCS_DIR
from frameworksync.pattern_extractor import extract_patterns_from_text


def run() -> None:
    all_patterns = []

    for library_dir in sorted(RAW_DOCS_DIR.iterdir()):
        if not library_dir.is_dir():
            continue
        library = library_dir.name
        for md_file in sorted(library_dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            patterns = extract_patterns_from_text(text, library=library, source_name=md_file.stem)
            print(f"{md_file.stem}: {len(patterns)} pattern(s)")
            all_patterns.extend(patterns)

    PATTERNS_PATH.write_text(
        json.dumps([asdict(p) for p in all_patterns], indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved {len(all_patterns)} total patterns to {PATTERNS_PATH}")


if __name__ == "__main__":
    run()