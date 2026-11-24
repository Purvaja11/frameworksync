"""Data shapes describing what we found when parsing a Python file.

This file has no logic — just the containers code_parser.py fills in.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ImportInfo:
    """One import statement found in a file."""

    module: str        # e.g. "langchain.agents" or "langchain_core.tools"
    names: tuple[str, ...]  # e.g. ("create_agent",) for `from X import create_agent`
    lineno: int         # line number, so we can point the user to it later


@dataclass(frozen=True)
class CallInfo:
    """One function/method call found in a file, e.g. `llm.bind_tools(...)`."""

    name: str           # e.g. "bind_tools" or "create_agent"
    lineno: int


@dataclass
class ParsedFile:
    """Everything we extracted from one Python file."""

    path: str
    libraries_used: set[str] = field(default_factory=set)
    imports: list[ImportInfo] = field(default_factory=list)
    calls: list[CallInfo] = field(default_factory=list)