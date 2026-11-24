"""Parses a Python file to find which framework(s) it uses, and how.

Uses Python's built-in `ast` module — we read the code's structure,
we never execute it. Parsing untrusted or WIP code should never run it.
"""

import ast
from pathlib import Path

from frameworksync.config import LIBRARY_IMPORT_PREFIXES
from frameworksync.parser.models import CallInfo, ImportInfo, ParsedFile


def _library_for_module(module: str) -> str | None:
    """Given an imported module name, return which of our libraries it belongs to."""
    for library, prefixes in LIBRARY_IMPORT_PREFIXES.items():
        if any(module == p or module.startswith(p + ".") for p in prefixes):
            return library
    return None


def parse_file(path: Path) -> ParsedFile:
    """Parse one .py file and extract its framework imports and calls."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    result = ParsedFile(path=str(path))

    for node in ast.walk(tree):
        # `import x.y` or `import x.y as z`
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                library = _library_for_module(module)
                if library:
                    result.libraries_used.add(library)
                    result.imports.append(
                        ImportInfo(module=module, names=(), lineno=node.lineno)
                    )

        # `from x.y import a, b`
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            library = _library_for_module(module)
            if library:
                names = tuple(alias.name for alias in node.names)
                result.libraries_used.add(library)
                result.imports.append(
                    ImportInfo(module=module, names=names, lineno=node.lineno)
                )

        # any function/method call: foo(...) or obj.foo(...)
        elif isinstance(node, ast.Call):
            call_name = _call_name(node)
            if call_name:
                result.calls.append(CallInfo(name=call_name, lineno=node.lineno))
            # `ServiceContext.from_defaults(...)` is a constructor in disguise: also record the
            # class name, otherwise the legacy class is never matched (only "from_defaults" is).
            factory_class = _factory_class(node)
            if factory_class:
                result.calls.append(CallInfo(name=factory_class, lineno=node.lineno))

    return result


def _call_name(node: ast.Call) -> str | None:
    """Get the plain name of what's being called, e.g. 'bind_tools' from obj.bind_tools(...)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id            # e.g. create_agent(...)
    if isinstance(func, ast.Attribute):
        return func.attr          # e.g. llm.bind_tools(...) -> "bind_tools"
    return None

def _factory_class(node: ast.Call) -> str | None:
    """The class name in `ClassName.from_xxx(...)` (the usual classmethod-constructor style)."""
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr.startswith("from_")
        and isinstance(func.value, ast.Name)
        and func.value.id[:1].isupper()
    ):
        return func.value.id
    return None
