"""Automatic migrations for patterns that can be rewritten safely.

An automatic fix is only produced when EVERY use of the old name in the file is
a shape we know how to rewrite. Anything else raises _Unsafe with a plain-English
reason; the caller then falls back to manual steps and shows that reason.

Before a fix is returned it is applied in memory and checked:
  * the edited file still parses,
  * the old variable no longer appears anywhere,
  * the edits do not overlap.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from frameworksync.manual_steps import _call_at, _statement


class _Unsafe(Exception):
    """This file's shape is not safe to rewrite automatically (message = why)."""


@dataclass
class AutoResult:
    edits: list[dict]
    original_code: str
    proposed_code: str
    start_line: int
    end_line: int
    description: str


@dataclass
class _Ctx:
    line: int
    lines: list[str]
    tree: ast.AST
    text: str
    parents: dict


SETTINGS_KEYWORDS = {
    "llm", "embed_model", "node_parser", "chunk_size", "chunk_overlap",
    "num_output", "context_window", "callback_manager", "transformations",
}


# ----------------------------------------------------------------------- utils

def _edit(lines, start, end, new_text):
    return {"start_line": start, "end_line": end, "new_text": new_text,
            "old_text": "\n".join(lines[start - 1:end])}


def _note(ctx: _Ctx, line: int, text: str) -> str:
    """A one-line comment left where a statement was removed, so nothing vanishes silently."""
    return f"{_indent(ctx, line)}# FrameworkSync: {text}"


def _seg(ctx: _Ctx, node: ast.AST) -> str:
    text = ast.get_source_segment(ctx.text, node)
    if text is None:
        raise _Unsafe("part of the statement could not be read back from the source")
    return text


def _indent(ctx: _Ctx, line: int) -> str:
    return re.match(r"\s*", ctx.lines[line - 1]).group(0)


def _alias(a: ast.alias) -> str:
    return a.name + (f" as {a.asname}" if a.asname else "")


def _names(ctx: _Ctx, name: str) -> list[ast.Name]:
    return [n for n in ast.walk(ctx.tree) if isinstance(n, ast.Name) and n.id == name]


def _assigned(ctx: _Ctx, name: str):
    """(var, call, first_line, last_line) of `var = <name>(...)`, on its own lines."""
    call = _call_at(ctx, name)
    if call is None:
        raise _Unsafe(f"the `{name}(...)` call could not be located")
    var, start, end = _statement(ctx, call)
    if var is None:
        raise _Unsafe(f"`{name}(...)` is not assigned to a plain variable")
    if any(";" in ctx.lines[i - 1] for i in range(start, end + 1)):
        raise _Unsafe("several statements share the same line")
    return var, call, start, end


def _remove_kwarg_edits(ctx: _Ctx, var: str, after: int, deletable_calls=()):
    """Edits that remove `<kw>=var` from calls (and delete `set_global_...(var)` lines)."""
    by_line: dict[int, list[str]] = {}
    delete_lines: list[tuple[int, int]] = []
    removed_calls: list[str] = []
    for n in _names(ctx, var):
        if n.lineno <= after:
            continue
        parent = ctx.parents.get(n)
        if isinstance(parent, ast.keyword) and parent.value is n and parent.arg:
            if parent.lineno != parent.end_lineno:
                raise _Unsafe(f"`{parent.arg}={var}` on line {parent.lineno} spans several lines")
            by_line.setdefault(parent.lineno, []).append(parent.arg)
        elif (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
              and parent.func.id in deletable_calls and parent.args == [n] and not parent.keywords
              and isinstance(ctx.parents.get(parent), ast.Expr)):
            stmt = ctx.parents[parent]
            delete_lines.append((stmt.lineno, stmt.end_lineno))
            removed_calls.append(parent.func.id)
        else:
            raise _Unsafe(f"`{var}` is used on line {n.lineno} in a way that needs a human decision")

    edits = []
    for line, args in by_line.items():
        new = ctx.lines[line - 1]
        for arg in args:
            new, count = re.subn(rf"\b{re.escape(arg)}\s*=\s*{re.escape(var)}\b", "", new, count=1)
            if count != 1:
                raise _Unsafe(f"could not remove `{arg}={var}` from line {line}")
        new = re.sub(r"\(\s*,\s*", "(", new)
        new = re.sub(r",\s*,", ",", new)
        new = re.sub(r",\s*\)", ")", new)
        edits.append(_edit(ctx.lines, line, line, "" if re.fullmatch(r"\s*,?\s*", new) else new))
    for start, end in delete_lines:
        edits.append(_edit(ctx.lines, start, end, ""))
    return edits, removed_calls


# --------------------------------------------------------------------- imports

def _render_import(ctx: _Ctx, node: ast.ImportFrom, names: list[str]) -> str:
    if not names:
        return ""
    indent = _indent(ctx, node.lineno)
    if node.lineno != node.end_lineno and len(names) > 1:
        body = "".join(f"{indent}    {n},\n" for n in names)
        return f"{indent}from {node.module} import (\n{body}{indent})"
    return f"{indent}from {node.module} import {', '.join(names)}"


def _import_edits(ctx: _Ctx, drop: list[str], add: list[str], add_module: str):
    """Remove `drop` names from where they are imported and add `add` names from `add_module`."""
    imports = [n for n in ast.walk(ctx.tree)
               if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module]

    def check(node):
        if any("#" in ctx.lines[i - 1] for i in range(node.lineno, node.end_lineno + 1)):
            raise _Unsafe(f"the import on line {node.lineno} contains a comment")
        if any(a.name == "*" for a in node.names):
            raise _Unsafe("a star import is used")

    drop_node = None
    if drop:
        drop_node = next((n for n in imports if any(a.name in drop for a in n.names)), None)
        if drop_node is None:
            raise _Unsafe(f"the import of {', '.join(drop)} was not found as `from ... import ...`")
        check(drop_node)

    already = {a.name for n in imports if n.module == add_module for a in n.names}
    need = [x for x in add if x not in already]
    edits = []
    extra_after_drop = None

    kept_drop = None
    if drop_node is not None:
        kept = [_alias(a) for a in drop_node.names if a.name not in drop]
        if drop_node.module == add_module:
            kept, need = kept + need, []
        kept_drop = kept

    target = None
    if need:
        target = next((n for n in imports if n.module == add_module and n is not drop_node), None)
        if target is not None:
            check(target)
            edits.append(_edit(ctx.lines, target.lineno, target.end_lineno,
                               _render_import(ctx, target, [_alias(a) for a in target.names] + need)))
        else:
            extra_after_drop = f"from {add_module} import {', '.join(need)}"

    if drop_node is not None:
        rendered = _render_import(ctx, drop_node, kept_drop)
        if extra_after_drop:
            rendered = (rendered + "\n" if rendered else "") + _indent(ctx, drop_node.lineno) + extra_after_drop
        edits.append(_edit(ctx.lines, drop_node.lineno, drop_node.end_lineno, rendered))
    elif extra_after_drop:
        tops = [n for n in ctx.tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        if not tops:
            raise _Unsafe("no import block to add the new import to")
        last = tops[-1]
        original = "\n".join(ctx.lines[last.lineno - 1:last.end_lineno])
        edits.append(_edit(ctx.lines, last.lineno, last.end_lineno, original + "\n" + extra_after_drop))
    return edits


def _drop_if_unused(ctx: _Ctx, name: str, removed_uses: int = 1) -> list[str]:
    """[name] when this fix removes the last use of it (so its import can go)."""
    return [name] if len(_names(ctx, name)) - removed_uses <= 0 else []


# ------------------------------------------------------------------- validation

def _simulate(lines: list[str], edits: list[dict]) -> str:
    out = list(lines)
    for e in sorted(edits, key=lambda e: -e["start_line"]):
        out[e["start_line"] - 1:e["end_line"]] = e["new_text"].split("\n") if e["new_text"] != "" else []
    return "\n".join(out) + "\n"


def _finish(ctx: _Ctx, edits: list[dict], gone_var: str | None):
    ordered = sorted(edits, key=lambda e: e["start_line"])
    for prev, cur in zip(ordered, ordered[1:]):
        if cur["start_line"] <= prev["end_line"]:
            raise _Unsafe("two edits would overlap")
    result = _simulate(ctx.lines, edits)
    try:
        tree = ast.parse(result)
    except SyntaxError as err:
        raise _Unsafe(f"the rewritten file would not parse ({err.msg})")
    if gone_var and any(isinstance(n, ast.Name) and n.id == gone_var for n in ast.walk(tree)):
        raise _Unsafe(f"`{gone_var}` would still be referenced after the rewrite")
    return ordered


def _settings_lines(ctx: _Ctx, indent: str, pairs: list[tuple[str, ast.AST]]) -> list[str]:
    return [f"{indent}Settings.{name} = {_seg(ctx, value)}" for name, value in pairs]


# ------------------------------------------------------------------- migrations

def _service_context(ctx: _Ctx) -> AutoResult:
    var, call, start, end = _assigned(ctx, "ServiceContext")
    if call.args:
        raise _Unsafe("ServiceContext is called with positional arguments")
    for k in call.keywords:
        if k.arg not in SETTINGS_KEYWORDS:
            raise _Unsafe(f"argument `{k.arg}` has no direct `Settings` equivalent")
    indent = _indent(ctx, start)
    new_lines = _settings_lines(ctx, indent, [(k.arg, k.value) for k in call.keywords])

    note = _note(ctx, start, "ServiceContext() removed - it had no settings to move to Settings")
    edits = [_edit(ctx.lines, start, end, "\n".join(new_lines) if new_lines else note)]
    use_edits, removed = _remove_kwarg_edits(ctx, var, end, deletable_calls={"set_global_service_context"})
    edits += use_edits

    drop = _drop_if_unused(ctx, "ServiceContext")
    if removed:
        drop += _drop_if_unused(ctx, "set_global_service_context", len(removed))
    edits += _import_edits(ctx, drop, ["Settings"] if new_lines else [], "llama_index.core")

    original = "\n".join(ctx.lines[start - 1:end])
    proposed = "\n".join(l.strip() for l in new_lines) or note.strip()
    parts = [f"Replace `{var} = ServiceContext(...)` with global `Settings` assignments"
             if new_lines else f"Replace the empty `{var} = ServiceContext(...)` with a comment (it had no settings)"]
    if use_edits or removed:
        parts.append(f"remove `{var}` from {len(use_edits) + len(removed)} later line(s)")
    parts.append("update the imports")
    return AutoResult(_finish(ctx, edits, var), original, proposed, start, end, "; ".join(parts) + ".")


def _prompt_helper(ctx: _Ctx) -> AutoResult:
    var, call, start, end = _assigned(ctx, "PromptHelper")
    pairs: list[tuple[str, ast.AST]] = []
    order = ["context_window", "num_output"]
    if len(call.args) > 2:
        raise _Unsafe("PromptHelper has chunk settings that now belong on the node parser")
    for name, value in zip(order, call.args):
        pairs.append((name, value))
    for k in call.keywords:
        mapped = "context_window" if k.arg == "max_input_size" else k.arg
        if mapped not in order:
            raise _Unsafe(f"argument `{k.arg}` now belongs on the node parser (chunking)")
        pairs.append((mapped, k.value))
    indent = _indent(ctx, start)
    new_lines = _settings_lines(ctx, indent, pairs)

    note = _note(ctx, start, "PromptHelper() removed - it had no settings to move to Settings")
    edits = [_edit(ctx.lines, start, end, "\n".join(new_lines) if new_lines else note)]
    use_edits, _ = _remove_kwarg_edits(ctx, var, end)
    edits += use_edits
    edits += _import_edits(ctx, _drop_if_unused(ctx, "PromptHelper"), ["Settings"] if new_lines else [], "llama_index.core")

    original = "\n".join(ctx.lines[start - 1:end])
    proposed = "\n".join(l.strip() for l in new_lines) or note.strip()
    return AutoResult(_finish(ctx, edits, var), original, proposed, start, end,
                      "Replace PromptHelper with `Settings` values and update the imports.")


def _llm_predictor(ctx: _Ctx) -> AutoResult:
    var, call, start, end = _assigned(ctx, "LLMPredictor")
    kw = {k.arg: k.value for k in call.keywords}
    if set(kw) - {"llm"} or len(call.args) + len(kw) != 1:
        raise _Unsafe("LLMPredictor is called with arguments other than the llm")
    llm_node = kw.get("llm") or call.args[0]
    if not all(isinstance(n, (ast.Name, ast.Attribute, ast.Load)) for n in ast.walk(llm_node)):
        raise _Unsafe("the llm argument is an expression, not a plain variable")
    llm_text = _seg(ctx, llm_node)

    per_line: dict[int, list[ast.Name]] = {}
    for n in _names(ctx, var):
        if n.lineno <= end:
            continue
        parent = ctx.parents.get(n)
        if not (isinstance(parent, ast.Attribute) and parent.value is n):
            raise _Unsafe(f"`{var}` is passed around on line {n.lineno} (not just method calls)")
        per_line.setdefault(n.lineno, []).append(n)

    if per_line:
        # later uses are rewritten to the llm itself, so the wrapper line simply goes away
        replacement, new_names = "", []
        proposed = None  # filled in below from the rewritten lines
    else:
        # nothing used the wrapper: hand the llm to LlamaIndex the current way
        replacement, new_names = f"{_indent(ctx, start)}Settings.llm = {llm_text}", ["Settings"]
        proposed = replacement.strip()
    edits = [_edit(ctx.lines, start, end, replacement)]
    for line, nodes in per_line.items():
        data = ctx.lines[line - 1].encode("utf-8")
        for n in sorted(nodes, key=lambda n: -n.col_offset):
            data = data[:n.col_offset] + llm_text.encode("utf-8") + data[n.end_col_offset:]
        edits.append(_edit(ctx.lines, line, line, data.decode("utf-8")))
    if proposed is None:
        proposed = "\n".join(e["new_text"].strip() for e in edits[1:])
    edits += _import_edits(ctx, _drop_if_unused(ctx, "LLMPredictor"), new_names, "llama_index.core")

    original = "\n".join(ctx.lines[start - 1:end])
    what = (f"Remove the LLMPredictor wrapper and use `{llm_text}` directly ({len(per_line)} later line(s) rewritten)."
            if per_line else
            f"Replace the LLMPredictor wrapper with `Settings.llm = {llm_text}`.")
    return AutoResult(_finish(ctx, edits, var), original, proposed, start, end, what)


_GRAPH_ALLOWED = {"add_edge", "add_conditional_edges", "set_entry_point", "set_finish_point", "compile"}


def _message_graph(ctx: _Ctx) -> AutoResult:
    var, call, start, end = _assigned(ctx, "MessageGraph")
    if call.args or call.keywords:
        raise _Unsafe("MessageGraph is called with arguments")
    prefix = _seg(ctx, call.func.value) + "." if isinstance(call.func, ast.Attribute) else ""

    compiled: set[str] = set()
    for n in _names(ctx, var):
        if n.lineno <= end:
            continue
        parent = ctx.parents.get(n)
        if not (isinstance(parent, ast.Attribute) and parent.value is n and parent.attr in _GRAPH_ALLOWED):
            attr = parent.attr if isinstance(parent, ast.Attribute) else "it"
            raise _Unsafe(f"`{var}.{attr}` is used on line {n.lineno}: nodes and call shapes need a human decision")
        if parent.attr == "compile":
            assign = ctx.parents.get(ctx.parents.get(parent))
            if isinstance(assign, ast.Assign) and len(assign.targets) == 1 and isinstance(assign.targets[0], ast.Name):
                compiled.add(assign.targets[0].id)
    for name in compiled:
        uses = [n for n in _names(ctx, name) if isinstance(n.ctx, ast.Load)]
        if uses:
            raise _Unsafe(f"the compiled graph `{name}` is used on line {uses[0].lineno}: "
                          "its input/output now use a `messages` dict")

    before = "\n".join(ctx.lines[start - 1:end])
    segment = _seg(ctx, call)
    after = before.replace(segment, f"{prefix}StateGraph({prefix}MessagesState)", 1)
    edits = [_edit(ctx.lines, start, end, after)]
    if not prefix:
        edits += _import_edits(ctx, _drop_if_unused(ctx, "MessageGraph"), ["StateGraph", "MessagesState"], "langgraph.graph")
    return AutoResult(_finish(ctx, edits, None), before, after, start, end,
                      "Replace MessageGraph with StateGraph(MessagesState) and update the imports. "
                      "No nodes or invoke calls were found that need changing.")


_BUILDERS = {
    "legacy_service_context": _service_context,
    "legacy_prompt_helper": _prompt_helper,
    "legacy_llm_predictor": _llm_predictor,
    "legacy_message_graph": _message_graph,
}


def try_auto(signal: str, line: int, text: str, lines: list[str], tree: ast.AST):
    """AutoResult, or a str explaining why this one cannot be automatic, or None (no auto builder)."""
    builder = _BUILDERS.get(signal)
    if builder is None:
        return None
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    try:
        return builder(_Ctx(line=line, lines=lines, tree=tree, text=text, parents=parents))
    except _Unsafe as why:
        return str(why)
    except Exception as err:  # never let a bug here break analysis
        return f"internal check failed ({type(err).__name__})"
