"""Extracts deprecated-pattern records from raw doc files using regex.

Deterministic, not LLM-based. Works on whole documents (not embedding
chunks), so a header and its explanation are never split by chunk
boundaries. Only backtick-wrapped `Names` are ever captured, so plain
English words can never be mistaken for a code identifier.
"""

import re
from dataclasses import dataclass

BANNED_PATTERNS = {"langchain", "langgraph", "llamaindex", "llama_index"}

# Correctly-sourced patterns we still exclude, because the old name is too
# generic to match safely against arbitrary code (would false-positive on
# unrelated classes/variables with the same common name).
TOO_GENERIC = {
    "node",  # llamaindex-changelog: real 2023 rename, but too common a name to flag safely
}
_NAME = r"`([A-Za-z_][A-Za-z0-9_.]*)`"  # ONLY matches backtick-wrapped identifiers

# Same-sentence rules: both old and new names appear explicitly together.
INLINE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(_NAME + r"\s*(?:->|→)\s*" + _NAME), "arrow"),
    (re.compile(r"[Rr]eplace\s*" + _NAME + r"\s*with\s*" + _NAME), "replace with"),
    (re.compile(_NAME + r"\s*(?:has been)?\s*renamed to\s*" + _NAME, re.I), "renamed"),
    (re.compile(_NAME + r"\s*(?:has been)?\s*deprecated in favou?r of(?: the)?\s*" + _NAME, re.I), "deprecated in favor of"),
    (re.compile(_NAME + r"\s*(?:is|has been)?\s*replaced (?:with|by)\s*" + _NAME, re.I), "replaced by"),
]

# Markdown deprecation tables: "| `OldName` | `NewName` |" or
# "| `OldName` | [`NewName`](url) ...". Anchored to line start so it only
# matches an actual two-column table row, not arbitrary text with pipes.
TABLE_RULE = re.compile(
    r"^\|\s*`([A-Za-z_][A-Za-z0-9_.]*)`\s*\|\s*\[?`([A-Za-z_][A-Za-z0-9_.]*)`",
    re.M,
)

# Header rule: "## OldName" followed by a paragraph like "This has been
# renamed to `NewName`", where the subject is only named in the heading.
# SCOPED DELIBERATELY: this only works safely on reference pages where
# every header IS a bare deprecated symbol and nothing else (verified
# for llamaindex-deprecated-terms.md). Narrative docs — changelogs,
# migration guides — use headers as prose titles, and often nest real
# headers inside indented blocks our line-start anchor can't see, which
# lets an outer section's "body" swallow the rest of the document. We
# do not run this rule there; add a source name here only after
# checking its headers really are bare symbol names.
HEADER_RULE_SOURCES = {"llamaindex-deprecated-terms"}

_HEADER_SECTION = re.compile(
    r"^#{1,3}\s+`?([A-Za-z_][A-Za-z0-9_.]*)`?.*?$(.*?)(?=^#{1,3}\s|\Z)",
    re.M | re.S,
)
_HEADER_BODY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"renamed to\s*" + _NAME, re.I), "renamed"),
    (re.compile(r"deprecated in favou?r of(?: the)?\s*" + _NAME, re.I), "deprecated in favor of"),
    (re.compile(r"replaced (?:with|by)\s*" + _NAME, re.I), "replaced by"),
]


@dataclass
class PatternRecord:
    old_pattern: str
    new_pattern: str
    reason: str
    library: str
    source_name: str


def _valid_pair(old: str, new: str) -> bool:
    if old.lower() in BANNED_PATTERNS or new.lower() in BANNED_PATTERNS:
        return False
    if old.lower() in TOO_GENERIC:
        return False
    return old.lower() != new.lower()


def extract_patterns_from_text(text: str, library: str, source_name: str) -> list[PatternRecord]:
    """Scan one whole raw doc file for known deprecation phrasings."""
    records = []
    seen = set()  # keyed by old_pattern only: first match for a name wins

    def add(old: str, new: str, reason: str) -> None:
        if not _valid_pair(old, new):
            return
        key = old.lower()
        if key in seen:
            return
        seen.add(key)
        records.append(PatternRecord(old, new, reason, library, source_name))

    for regex, reason in INLINE_RULES:
        for match in regex.finditer(text):
            add(match.group(1), match.group(2), reason)

    for match in TABLE_RULE.finditer(text):
        add(match.group(1), match.group(2), "deprecation table")

    if source_name in HEADER_RULE_SOURCES:
        for header_match in _HEADER_SECTION.finditer(text):
            header_name, body = header_match.group(1), header_match.group(2)
            for regex, reason in _HEADER_BODY_RULES:
                body_match = regex.search(body)
                if body_match:
                    add(header_name, body_match.group(1), reason)
    return records