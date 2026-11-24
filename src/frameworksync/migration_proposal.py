"""Builds executable migration proposals for detected legacy patterns.

Every edit is line-based and carries `old_text` (the exact original lines),
so the editor can refuse to apply it if the file changed since analysis.
Edits only ever touch the lines they name: code between an agent and its
executor is never deleted.
"""

import ast
import re
from dataclasses import dataclass, replace
from pathlib import Path

from frameworksync.auto_migrations import AutoResult, try_auto
from frameworksync.manual_steps import build_manual_steps
from frameworksync.migration_transformer import Migration


@dataclass(frozen=True)
class MigrationProposal:
    file_path: str
    line: int
    old_pattern: str
    new_pattern: str
    original_line: str
    proposed_line: str
    description: str
    original_code: str | None = None
    proposed_code: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    applicable: bool = False
    needs_review: bool = True
    edits: list[dict] | None = None
    manual_steps: list[str] | None = None  # what the user must do by hand (review-only migrations)


def _proposal(path, line, migration, original_line, proposed_line, description,
              original_code=None, proposed_code=None, start_line=None,
              end_line=None, applicable=False, needs_review=True, edits=None):
    return MigrationProposal(
        file_path=str(path), line=line,
        old_pattern=migration.old_pattern, new_pattern=migration.new_pattern,
        original_line=original_line, proposed_line=proposed_line,
        description=description, original_code=original_code,
        proposed_code=proposed_code, start_line=start_line, end_line=end_line,
        applicable=applicable, needs_review=needs_review, edits=edits,
    )


def _edit(lines, start_line, end_line, new_text):
    """A line-range replacement. new_text == "" means delete those lines."""
    return {
        "start_line": start_line,
        "end_line": end_line,
        "new_text": new_text,
        "old_text": "\n".join(lines[start_line - 1:end_line]),
    }


# ---------------------------------------------------------------- AST helpers

def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _find_call_at(tree: ast.AST, line: int, name: str) -> ast.Call | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == line and _call_name(node) == name:
            return node
    return None


def _count_names(tree: ast.AST, name: str) -> int:
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == name)


def _replace_name_at(line_text: str, col: int, old: str, new: str) -> str | None:
    """Replace `old` at UTF-8 byte offset `col` (what ast reports)."""
    data = line_text.encode("utf-8")
    if data[col:col + len(old.encode())] != old.encode():
        return None
    return (data[:col] + new.encode() + data[col + len(old.encode()):]).decode("utf-8")


def _simple_import(tree, lines, module: str, name: str) -> ast.ImportFrom | None:
    """A single-line `from <module> import ... name ...` we can safely rewrite."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == module
            and node.level == 0
            and node.lineno == node.end_lineno
            and any(a.name == name and a.asname is None for a in node.names)
        ):
            text = lines[node.lineno - 1]
            if "(" in text or "#" in text or ";" in text:
                return None
            return node
    return None


def _has_import(tree, module: str, name: str) -> bool:
    return any(
        isinstance(n, ast.ImportFrom) and n.module == module
        and any(a.name == name for a in n.names)
        for n in ast.walk(tree)
    )


def _alias_text(a: ast.alias) -> str:
    return a.name + (f" as {a.asname}" if a.asname else "")


def _create_agent_import_edit(tree, lines, other_uses_remain: bool):
    """Edit for `from langgraph.prebuilt import create_react_agent[, ...]`.

    Returns (edit | None, recognized). Other names on the line stay where
    they were (ToolNode is NOT in langchain.agents); old import is kept if
    create_react_agent is still used elsewhere in the file.
    """
    node = _simple_import(tree, lines, "langgraph.prebuilt", "create_react_agent")
    if node is None:
        return None, False
    line_no = node.lineno
    original = lines[line_no - 1]
    indent = re.match(r"\s*", original).group(0)
    new_import = f"{indent}from langchain.agents import create_agent"
    already = _has_import(tree, "langchain.agents", "create_agent")

    if other_uses_remain:
        if already:
            return None, True
        return _edit(lines, line_no, line_no, original + "\n" + new_import), True

    others = [_alias_text(a) for a in node.names if a.name != "create_react_agent"]
    out = []
    if others:
        out.append(f"{indent}from langgraph.prebuilt import {', '.join(others)}")
    if not already:
        out.append(new_import)
    return _edit(lines, line_no, line_no, "\n".join(out)), True


def _remove_name_import_edit(tree, lines, module: str, name: str):
    node = _simple_import(tree, lines, module, name)
    if node is None:
        return None
    original = lines[node.lineno - 1]
    indent = re.match(r"\s*", original).group(0)
    others = [_alias_text(a) for a in node.names if a.name != name]
    new_text = f"{indent}from {module} import {', '.join(others)}" if others else ""
    return _edit(lines, node.lineno, node.lineno, new_text)


# ------------------------------------------------------------------ entry point

def build_proposal(path: Path, line: int, migration: Migration):
    proposal = _build_proposal(path, line, migration)
    if proposal is not None and proposal.needs_review:
        text = path.read_text(encoding="utf-8")
        steps = build_manual_steps(migration.signal, line, text)
        if steps:
            proposal = replace(proposal, manual_steps=steps)
    return proposal


def _build_proposal(path: Path, line: int, migration: Migration):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    i = line - 1
    if not 0 <= i < len(lines):
        return None
    original_line = lines[i]
    if migration.old_pattern not in original_line:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    auto = try_auto(migration.signal, line, text, lines, tree)
    auto_reason = None
    if isinstance(auto, AutoResult):
        return _proposal(
            path, line, migration, original_line, auto.proposed_code, auto.description,
            auto.original_code, auto.proposed_code, auto.start_line, auto.end_line,
            applicable=True, needs_review=False, edits=auto.edits,
        )
    if isinstance(auto, str):
        auto_reason = auto  # why this one is manual in THIS file

    manual = _build_manual_proposal(path, line, lines, tree, migration, original_line)
    if manual is not None and auto_reason:
        manual = replace(manual, description=f"{manual.description} Not automatic here because: {auto_reason}.")
    return manual


def _build_manual_proposal(path, line, lines, tree, migration, original_line):
    if migration.signal == "legacy_create_react_agent":
        return _build_create_react_agent_proposal(path, line, lines, tree, migration)
    if migration.signal == "legacy_agent_executor":
        return _build_agent_executor_proposal(path, line, lines, tree, migration)
    if migration.signal == "legacy_message_graph":
        return _build_message_graph_proposal(path, line, lines, migration)

    # No fake pseudo-code: these require context-specific transformations.
    return _proposal(
        path, line, migration, original_line, original_line,
        migration.description, original_line, None, line, line,
        applicable=False, needs_review=True,
    )


def _build_create_react_agent_proposal(path, line, lines, tree, migration):
    original_line = lines[line - 1]
    call = _find_call_at(tree, line, "create_react_agent")

    if call is None or not isinstance(call.func, ast.Name):
        return _proposal(
            path, line, migration, original_line, original_line,
            "create_react_agent is not called as a plain name here "
            "(e.g. module.create_react_agent). Review manually.",
            original_line, None, line, line,
        )

    proposed_line = _replace_name_at(
        original_line, call.func.col_offset, "create_react_agent", "create_agent"
    )
    if proposed_line is None:
        return None

    other_uses = _count_names(tree, "create_react_agent") - 1 > 0
    import_edit, recognized = _create_agent_import_edit(tree, lines, other_uses)

    edits = [_edit(lines, line, line, proposed_line)]
    if import_edit is not None:
        edits.insert(0, import_edit)

    if recognized:
        description = (
            "Migrate create_react_agent to create_agent and update the "
            "legacy langgraph.prebuilt import to langchain.agents."
        )
    else:
        description = (
            "Migrate create_react_agent to create_agent. The source import "
            "was not recognized, so review the import before applying."
        )

    return _proposal(
        path, line, migration, original_line, proposed_line, description,
        original_line, proposed_line, line, line,
        applicable=recognized, needs_review=not recognized, edits=edits,
    )


def _build_agent_executor_proposal(path, line, lines, tree, migration):
    executor_line = lines[line - 1]

    def review(message, original_code=None, proposed_code=None, applicable=False,
               proposed_line=None, start=line, end=line):
        return _proposal(
            path, line, migration, executor_line, proposed_line or executor_line,
            message, original_code or executor_line, proposed_code, start, end,
            applicable=applicable, needs_review=True,
        )

    call = _find_call_at(tree, line, "AgentExecutor")
    if call is None:
        return review("AgentExecutor was detected but its call could not be located. Review manually.")

    stmt = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.Assign) and n.value is call),
        None,
    )
    if (
        stmt is None
        or len(stmt.targets) != 1
        or not isinstance(stmt.targets[0], ast.Name)
    ):
        return review("AgentExecutor was detected, but its assignment shape is not recognized. Review manually.")

    start, end = stmt.lineno, stmt.end_lineno
    executor_var = stmt.targets[0].id

    if call.args or any(k.arg is None for k in call.keywords):
        return review("AgentExecutor uses positional or ** arguments. Review manually.", start=start, end=end)

    kwargs = {k.arg: k.value for k in call.keywords}
    agent_node = kwargs.get("agent")
    if not isinstance(agent_node, ast.Name):
        return review("AgentExecutor does not reference the agent variable in the expected way. Review manually.",
                      start=start, end=end)
    agent_var = agent_node.id

    candidates = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == agent_var
        and isinstance(n.value, ast.Call)
        and _call_name(n.value) in {"create_react_agent", "create_agent"}
        and n.end_lineno < start
    ]
    if not candidates:
        return review("AgentExecutor was detected, but no earlier create_react_agent assignment "
                      "for its agent was found. Review manually.", start=start, end=end)
    agent_stmt = max(candidates, key=lambda n: n.lineno)
    func = agent_stmt.value.func
    if not isinstance(func, ast.Name):
        return review("The create_react_agent call is not a plain-name call. Review manually.",
                      start=start, end=end)

    agent_line_no = func.lineno
    agent_line = lines[agent_line_no - 1]
    already_migrated = func.id == "create_agent"  # agent was already moved to create_agent
    if already_migrated:
        new_agent_line = agent_line
    else:
        new_agent_line = _replace_name_at(agent_line, func.col_offset, "create_react_agent", "create_agent")
        if new_agent_line is None:
            return review("Could not rewrite the create_react_agent call. Review manually.", start=start, end=end)

    executor_text = "\n".join(lines[start - 1:end])
    original_code = executor_text if already_migrated else agent_line + "\n" + executor_text
    proposed_code = new_agent_line

    later_uses = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == executor_var and n.lineno > end
    ]
    extra_kwargs = sorted(set(kwargs) - {"agent", "tools"})

    problems = []
    if later_uses:
        problems.append("later executor uses were found")
    if extra_kwargs:
        problems.append(f"AgentExecutor options would be lost ({', '.join(extra_kwargs)})")

    if already_migrated:
        import_edit, recognized = None, True
    else:
        other_react_uses = _count_names(tree, "create_react_agent") - 1 > 0
        import_edit, recognized = _create_agent_import_edit(tree, lines, other_react_uses)
    if not recognized:
        problems.append("the create_react_agent import was not recognized")

    if problems:
        return _proposal(
            path, line, migration, executor_line, new_agent_line,
            "The AgentExecutor wrapper can be removed, but review is needed: "
            + "; ".join(problems) + ".",
            original_code, proposed_code, start, end,
            applicable=True, needs_review=True,
        )

    if already_migrated:
        # The agent is already create_agent: the wrapper is not replaced by anything, so leave a
        # visible note instead of deleting the lines silently.
        indent = re.match(r"\s*", lines[start - 1]).group(0)
        note = f"{indent}# FrameworkSync: AgentExecutor wrapper removed - use `{agent_var}` directly"
        proposed_code = note.strip()
        edits = [_edit(lines, start, end, note)]
    else:
        edits = [_edit(lines, start, end, "")]  # the rewritten agent line replaces it
        edits.insert(0, _edit(lines, agent_line_no, agent_line_no, new_agent_line))
    if import_edit is not None:
        edits.insert(0, import_edit)

    if _count_names(tree, "AgentExecutor") - 1 <= 0:
        removal = _remove_name_import_edit(tree, lines, "langchain.agents", "AgentExecutor")
        if removal is not None:
            edits.append(removal)

    return _proposal(
        path, line, migration, executor_line, new_agent_line,
        ("Remove the AgentExecutor wrapper (the agent already uses create_agent) and update the imports."
         if already_migrated else
         "Replace the recognizable create_react_agent + AgentExecutor wrapper with "
         "create_agent and update the related imports."),
        original_code, proposed_code, start, end,
        applicable=True, needs_review=False, edits=edits,
    )


def _build_message_graph_proposal(path, line, lines, migration):
    line_text = lines[line - 1]
    if re.search(r"MessageGraph\s*\(\s*\)", line_text):
        proposed = line_text.replace("MessageGraph()", "StateGraph(MessagesState)", 1)
        return _proposal(
            path, line, migration, line_text, proposed,
            "Replace MessageGraph with StateGraph(MessagesState). "
            "The import/state schema must be reviewed before applying.",
            line_text, proposed, line, line,
            applicable=True, needs_review=True,
        )
    return _proposal(
        path, line, migration, line_text, line_text,
        "MessageGraph requires an explicit state schema in StateGraph. Review manually.",
        line_text, None, line, line,
        applicable=False, needs_review=True,
    )
