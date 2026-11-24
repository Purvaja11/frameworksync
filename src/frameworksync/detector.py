"""Checks a parsed Python file's calls against known deprecated patterns.

Fast, deterministic lookup against data/patterns.json — no LLM call here.
This is what runs on every file save.
"""

import json
from dataclasses import dataclass

from frameworksync.config import PATTERNS_PATH
from frameworksync.parser.models import ParsedFile


@dataclass
class Issue:
    """One deprecated pattern found in the user's code."""

    line: int
    old_pattern: str
    new_pattern: str
    reason: str
    library: str
    source_name: str

    @property
    def description(self) -> str:
        return f'"{self.old_pattern}" is deprecated -> use "{self.new_pattern}" ({self.library})'


def _load_patterns() -> list[dict]:
    if not PATTERNS_PATH.exists():
        return []
    return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))


def find_issues(parsed: ParsedFile) -> list[Issue]:
    """Compare a parsed file's calls against the known pattern list."""
    patterns = _load_patterns()
    if not patterns:
        return []

    # Index patterns by old_pattern for O(1) lookup instead of scanning
    # the whole list for every single call found in the file.
    by_old_name: dict[str, list[dict]] = {}
    for p in patterns:
        by_old_name.setdefault(p["old_pattern"], []).append(p)

    issues = []
    seen: set[tuple[int, str, str]] = set()
    for call in parsed.calls:
        matches = by_old_name.get(call.name)
        if not matches:
            continue
        for m in matches:
            # Only flag patterns of libraries this file actually imports.
            if m["library"] not in parsed.libraries_used:
                continue
            key = (call.lineno, m["old_pattern"], m["new_pattern"])
            if key in seen:  # e.g. create_react_agent listed by langchain AND langgraph
                continue
            seen.add(key)
            issues.append(
                Issue(
                    line=call.lineno,
                    old_pattern=m["old_pattern"],
                    new_pattern=m["new_pattern"],
                    reason=m["reason"],
                    library=m["library"],
                    source_name=m["source_name"],
                )
            )
    return issues
