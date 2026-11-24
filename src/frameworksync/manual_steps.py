"""Step-by-step instructions for migrations FrameworkSync cannot apply by itself.

Every step is built from the user's own code (real line numbers, real variable
names, real arguments) so it says exactly what to type, not just "review".
Steps are markdown: a bold title line, optionally followed by a python block.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass
class _Ctx:
    line: int
    lines: list[str]
    tree: ast.AST
    text: str = ""


def _fence(code: str) -> str:
    return "```python\n" + code.strip("\n") + "\n```"


def _src(node: ast.AST | None) -> str:
    try:
        return ast.unparse(node) if node is not None else ""
    except Exception:
        return ""


def _text(ctx: _Ctx, n: int) -> str:
    return ctx.lines[n - 1].strip() if 1 <= n <= len(ctx.lines) else ""


def _call_matches(node: ast.Call, name: str) -> bool:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id == name
    if isinstance(f, ast.Attribute):
        if f.attr == name:
            return True
        return f.attr.startswith("from_") and isinstance(f.value, ast.Name) and f.value.id == name
    return False


def _call_at(ctx: _Ctx, name: str) -> ast.Call | None:
    for n in ast.walk(ctx.tree):
        if isinstance(n, ast.Call) and n.lineno == ctx.line and _call_matches(n, name):
            return n
    return None


def _statement(ctx: _Ctx, call: ast.Call | None):
    """(assigned variable | None, first line, last line) of the statement holding `call`."""
    if call is not None:
        for n in ast.walk(ctx.tree):
            if isinstance(n, ast.Assign) and n.value is call:
                t = n.targets[0] if len(n.targets) == 1 else None
                return (t.id if isinstance(t, ast.Name) else None), n.lineno, n.end_lineno
            if isinstance(n, ast.Expr) and n.value is call:
                return None, n.lineno, n.end_lineno
        return None, call.lineno, call.end_lineno or call.lineno
    return None, ctx.line, ctx.line


def _uses(ctx: _Ctx, var: str | None, after: int, limit: int = 6) -> list[int]:
    if not var:
        return []
    found = sorted({
        n.lineno for n in ast.walk(ctx.tree)
        if isinstance(n, ast.Name) and n.id == var and n.lineno > after
    })
    return found[:limit]


def _use_list(ctx: _Ctx, numbers: list[int]) -> str:
    return "\n".join(f"- line {n}: `{_text(ctx, n)}`" for n in numbers)


def _import_of(ctx: _Ctx, name: str) -> ast.ImportFrom | None:
    for n in ast.walk(ctx.tree):
        if isinstance(n, ast.ImportFrom) and any(a.name == name for a in n.names):
            return n
    return None


def _stmt_before_after(ctx: _Ctx, call: ast.Call | None, new_call: str, start: int, end: int):
    """The user's own statement before, and the same statement with only the call replaced."""
    before = "\n".join(ctx.lines[start - 1:end])
    segment = ast.get_source_segment(ctx.text, call) if call is not None else None
    if segment and segment in before:
        return before, before.replace(segment, new_call, 1)
    return before, None


def _rewrite_uses(ctx: _Ctx, numbers: list[int], pattern: str, repl: str) -> str:
    """List the user's own lines, each with its rewritten version."""
    out = []
    for n in numbers:
        before = _text(ctx, n)
        after = re.sub(pattern, repl, before)
        if "set_global_service_context" in before:
            out.append(f"- line {n}: `{before}` → delete this line")
        elif after != before:
            out.append(f"- line {n}: `{before}` → `{after}`")
        else:
            out.append(f"- line {n}: `{before}` → update by hand")
    return "\n".join(out)


def _alias(a: ast.alias) -> str:
    return a.name + (f" as {a.asname}" if a.asname else "")


def _import_step(ctx: _Ctx, old: str, new_names: list[str], new_module: str | None = None) -> str:
    """Tell the user exactly how to change the import that brings in `old`."""
    imp = _import_of(ctx, old)
    new_names = [n for n in new_names if n]
    if imp is None:
        if not new_module:
            return (f"**Add the import** for {', '.join('`'+n+'`' for n in new_names)} "
                    f"(`{old}` is not imported with a plain `from ... import` in this file).")
        return (f"**Add this import** at the top (`{old}` is not imported with a plain `from ... import` here):\n"
                + _fence(f"from {new_module} import {', '.join(new_names)}"))
    kept = [_alias(a) for a in imp.names if a.name != old]
    span = f"line {imp.lineno}" if imp.lineno == imp.end_lineno else f"lines {imp.lineno}-{imp.end_lineno}"
    if new_module and new_module != imp.module:
        out = []
        if kept:
            out.append(f"from {imp.module} import {', '.join(kept)}")
        out.append(f"from {new_module} import {', '.join(new_names)}")
        return f"**Change the import on {span}** (`{old}` moves to `{new_module}`):\n" + _fence("\n".join(out))
    names = kept + [n for n in new_names if n not in kept]
    return (f"**Change the import on {span}** (remove `{old}`, add {', '.join('`'+n+'`' for n in new_names)}):\n"
            + _fence(f"from {imp.module} import {', '.join(names)}"))


# ------------------------------------------------------------------ langgraph

def _message_graph(ctx: _Ctx) -> list[str]:
    call = _call_at(ctx, "MessageGraph")
    var, start, end = _statement(ctx, call)
    # `lg.MessageGraph()` -> keep the user's `lg.` prefix; aliased names -> plain StateGraph
    prefix = _src(call.func.value) + "." if call is not None and isinstance(call.func, ast.Attribute) else ""
    state_graph, messages_state = f"{prefix}StateGraph", f"{prefix}MessagesState"

    steps = []
    if prefix:
        steps.append(f"**No import change needed.** You reach `MessageGraph` through `{prefix[:-1]}`, so "
                     f"`{state_graph}` and `{messages_state}` are available the same way.")
    else:
        steps.append(_import_step(ctx, "MessageGraph", ["StateGraph", "MessagesState"], "langgraph.graph"))

    before, after = _stmt_before_after(ctx, call, f"{state_graph}({messages_state})", start, end)
    span = f"line {start}" if start == end else f"lines {start}-{end}"
    why = ("a `MessageGraph` passed a plain list of messages between nodes; "
           "`StateGraph(MessagesState)` passes a dict with a `messages` key")
    if after:
        steps.append(f"**Replace {span}** ({why}):\n" + _fence(f"# before\n{before}\n# after\n{after}"))
    else:
        steps.append(f"**Replace the `MessageGraph(...)` call on {span}** with `{state_graph}({messages_state})` ({why}).")

    if var is None:
        steps.append("**Nodes:** the graph is not assigned to a variable here, so its `add_node` calls could not be "
                     "located. Every node function must take the state dict and return `{\"messages\": [...]}`.")
        return steps
    nodes = sorted({
        n.lineno for n in ast.walk(ctx.tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_node"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == var
    })
    if nodes:
        steps.append(
            f"**Update every node function you add to `{var}`** - it now receives the state dict and must "
            "return a dict with `messages`. These are the `add_node` calls in this file:\n"
            + _use_list(ctx, nodes) + "\n"
            + _fence('# generic example (not from your file)\n'
                     'def my_node(state: MessagesState):\n'
                     '    reply = llm.invoke(state["messages"])   # was: llm.invoke(messages)\n'
                     '    return {"messages": [reply]}'))
    else:
        steps.append(f"**Nodes:** no `add_node` calls on `{var}` in this file, so there are no node functions "
                     "to change here. (Nodes added in other files need the same change.)")

    compiled = {var}
    for n in ast.walk(ctx.tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "compile" \
                and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == var \
                and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            compiled.add(n.targets[0].id)
    runs = sorted({
        n.lineno for n in ast.walk(ctx.tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"invoke", "ainvoke", "stream", "astream"}
        and isinstance(n.func.value, ast.Name) and n.func.value.id in compiled
    })
    if runs:
        steps.append(
            "**Update how you call the graph** - input and output are now dicts with `messages`. "
            "Your calls:\n" + _use_list(ctx, runs) + "\n"
            + _fence('# generic example (not from your file)\n'
                     'result = app.invoke({"messages": [HumanMessage("hi")]})   # was: app.invoke([HumanMessage("hi")])\n'
                     'last = result["messages"][-1]'))
    return steps


# ------------------------------------------------------------------ langchain

def _agent_call_steps(ctx: _Ctx, call: ast.Call | None, lead: str = "") -> list[str]:
    steps = []
    if call is not None:
        n = call.lineno
        target = _text(ctx, n)
        new = target.replace("create_react_agent", "create_agent", 1)
        steps.append(f"**{lead}Rename the call on line {n}**:\n" + _fence(f"# before\n{target}\n# after\n{new}"))
        if any(k.arg == "prompt" for k in call.keywords):
            steps.append("**Rename the keyword** `prompt=` to `system_prompt=` in that call "
                         "(`create_agent` calls it `system_prompt`).")
    return steps


def _create_react_agent(ctx: _Ctx) -> list[str]:
    call = _call_at(ctx, "create_react_agent")
    steps = []
    if call is not None and isinstance(call.func, ast.Attribute):
        steps.append(f"**Line {ctx.line} calls it as `{_src(call.func)}(...)`.** Change it to a plain call:\n"
                     + _fence(f"# before\n{_text(ctx, ctx.line)}\n# after\n"
                              + _text(ctx, ctx.line).replace(_src(call.func), "create_agent", 1)))
        steps.append("**Add the import** at the top of the file:\n"
                     + _fence("from langchain.agents import create_agent"))
    else:
        steps += _agent_call_steps(ctx, call)
        steps.append(_import_step(ctx, "create_react_agent", ["create_agent"], "langchain.agents"))
    steps.append("**Invoking it stays the same shape:** `agent.invoke({\"messages\": [...]})`.")
    return steps


def _agent_executor(ctx: _Ctx) -> list[str]:
    call = _call_at(ctx, "AgentExecutor")
    var, start, end = _statement(ctx, call)
    kwargs = {k.arg: k.value for k in call.keywords if k.arg} if call else {}
    agent_var = kwargs["agent"].id if isinstance(kwargs.get("agent"), ast.Name) else "agent"

    agent_call = None
    for n in ast.walk(ctx.tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and n.targets[0].id == agent_var and isinstance(n.value, ast.Call) \
                and _call_matches(n.value, "create_react_agent") and n.end_lineno < start:
            agent_call = n.value
    steps = []
    if agent_call is not None:
        steps += _agent_call_steps(ctx, agent_call)
    else:
        steps.append(
            f"**Build the agent with `create_agent`.** `{agent_var}` was not made by `create_react_agent` in "
            "this file, so create it directly:\n"
            + _fence("agent = create_agent(model, tools, system_prompt=\"...\")"))

    span = f"line {start}" if start == end else f"lines {start}-{end}"
    steps.append(
        f"**Delete the `AgentExecutor` statement on {span}.** `create_agent` already returns the runnable "
        f"agent, so `{agent_var}` replaces `{var or 'the executor'}`. Do not delete anything else between the lines."
    )

    mapping = {
        "max_iterations": "pass `config={\"recursion_limit\": N}` to `agent.invoke(...)`",
        "verbose": "pass `debug=True` to `create_agent(...)`",
        "handle_parsing_errors": "remove it - tool-calling agents do not parse text output",
        "return_intermediate_steps": "remove it - the intermediate steps are the `messages` in the result",
        "early_stopping_method": "remove it - use `recursion_limit` (see `max_iterations`)",
        "memory": "remove it - use a checkpointer: `create_agent(..., checkpointer=...)`",
    }
    extra = [k for k in kwargs if k not in {"agent", "tools"}]
    if extra:
        lines_ = [f"- `{k}={_src(kwargs[k])}`: {mapping.get(k, 'no direct equivalent - check the create_agent docs')}"
                  for k in extra]
        steps.append("**Options you passed to `AgentExecutor`** do not move over automatically:\n" + "\n".join(lines_))

    later = _uses(ctx, var, end)
    if later:
        steps.append(
            f"**Update where `{var}` is used** - use `{agent_var}` instead, and the input/output shape changed. "
            "Your lines:\n"
            + _rewrite_uses(ctx, later, rf"\b{re.escape(var)}\b", agent_var) + "\n"
            + _fence('# shape change (generic example, not from your file)\n'
                     'agent.invoke({"input": q})["output"]   ->   '
                     'agent.invoke({"messages": [{"role": "user", "content": q}]})["messages"][-1].content'))

    imp = _import_of(ctx, "AgentExecutor")
    if imp is not None:
        kept = [_alias(a) for a in imp.names if a.name != "AgentExecutor"]
        steps.append(f"**Imports:** remove `AgentExecutor` from line {imp.lineno}"
                     + (f" (keep: {', '.join(kept)})." if kept else " (delete that line)."))
    if _import_of(ctx, "create_react_agent") is not None and agent_call is not None:
        steps.append(_import_step(ctx, "create_react_agent", ["create_agent"], "langchain.agents"))
    return steps


# ----------------------------------------------------------------- llamaindex

_SETTINGS_NAMES = {
    "llm": "Settings.llm", "embed_model": "Settings.embed_model", "node_parser": "Settings.node_parser",
    "chunk_size": "Settings.chunk_size", "chunk_overlap": "Settings.chunk_overlap",
    "num_output": "Settings.num_output", "context_window": "Settings.context_window",
    "callback_manager": "Settings.callback_manager", "transformations": "Settings.transformations",
}


def _service_context(ctx: _Ctx) -> list[str]:
    call = _call_at(ctx, "ServiceContext")
    var, start, end = _statement(ctx, call)
    steps = [_import_step(ctx, "ServiceContext", ["Settings"])]
    ported, unknown = [], []
    for k in (call.keywords if call else []):
        if k.arg in _SETTINGS_NAMES:
            ported.append(f"{_SETTINGS_NAMES[k.arg]} = {_src(k.value)}")
        elif k.arg == "prompt_helper":
            unknown.append(f"`prompt_helper={_src(k.value)}`: see the PromptHelper steps - set "
                           "`Settings.context_window` and `Settings.num_output` instead")
        elif k.arg:
            unknown.append(f"`{k.arg}={_src(k.value)}`: no `Settings` attribute with this name - check the "
                           "Settings docs or pass it to the module that needs it")
    span = f"line {start}" if start == end else f"lines {start}-{end}"
    if ported:
        steps.append(f"**Replace {span}** with one `Settings` assignment per argument you passed:\n"
                     + _fence("\n".join(ported)))
    else:
        steps.append(f"**Delete {span}.** No arguments were passed, so there is no configuration to port "
                     "(defaults stay in effect).")
    if unknown:
        steps.append("**Arguments to handle yourself:**\n" + "\n".join("- " + u for u in unknown))
    uses = _uses(ctx, var, end)
    removed_global = [n.lineno for n in ast.walk(ctx.tree)
                      if isinstance(n, ast.Call) and _call_matches(n, "set_global_service_context")]
    todo = sorted(set(uses) | set(removed_global))
    if todo:
        steps.append(
            f"**Remove `{var or 'the service context'}` everywhere it is used** (`Settings` is global, so "
            "it is no longer passed around; delete `set_global_service_context(...)` too):\n"
            + (_rewrite_uses(ctx, todo, rf",?\s*\w+\s*=\s*{re.escape(var)}\b", "") if var
               else _use_list(ctx, todo)))
    return steps


def _llm_predictor(ctx: _Ctx) -> list[str]:
    call = _call_at(ctx, "LLMPredictor")
    var, start, end = _statement(ctx, call)
    llm_expr = ""
    if call is not None:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        llm_expr = _src(kw.get("llm") or (call.args[0] if call.args else None))
    steps = []
    imp = _import_of(ctx, "LLMPredictor")
    if imp is not None:
        steps.append(f"**Imports:** remove `LLMPredictor` from line {imp.lineno}.")
    span = f"line {start}" if start == end else f"lines {start}-{end}"
    llm_name = llm_expr or "llm"
    steps.append(f"**Delete {span}.** `LLMPredictor` is no longer meant to be used; the LLM object "
                 f"(`{llm_name}`) has the same methods (`predict`, `complete`, `chat`).")
    uses = _uses(ctx, var, end)
    if uses:
        steps.append(f"**Use `{llm_name}` directly where `{var}` is used:**\n"
                     + _rewrite_uses(ctx, uses, rf"\b{re.escape(var)}\b", llm_name))
    steps.append("**Make LlamaIndex use that LLM globally:**\n"
                 + _fence(f"from llama_index.core import Settings\n\nSettings.llm = {llm_name}"))
    return steps


def _prompt_helper(ctx: _Ctx) -> list[str]:
    call = _call_at(ctx, "PromptHelper")
    var, start, end = _statement(ctx, call)
    steps = []
    imp = _import_of(ctx, "PromptHelper")
    if imp is not None:
        steps.append(_import_step(ctx, "PromptHelper", ["Settings"], "llama_index.core"))
    lines_, notes = [], []
    arg_names = ["context_window", "num_output", "chunk_overlap_ratio", "chunk_size_limit"]
    given: dict[str, ast.AST] = {}
    if call is not None:
        for i, a in enumerate(call.args[:4]):
            given[arg_names[i]] = a
        for k in call.keywords:
            if k.arg:
                given["context_window" if k.arg == "max_input_size" else k.arg] = k.value
    if "context_window" in given:
        lines_.append(f"Settings.context_window = {_src(given['context_window'])}")
    if "num_output" in given:
        lines_.append(f"Settings.num_output = {_src(given['num_output'])}")
    if "chunk_size_limit" in given or "chunk_overlap_ratio" in given:
        notes.append("`chunk_size_limit` / `chunk_overlap_ratio` now live on the node parser, and the overlap is "
                     "a number of tokens (not a ratio): `Settings.node_parser = SentenceSplitter(chunk_size=..., "
                     "chunk_overlap=...)` (`from llama_index.core.node_parser import SentenceSplitter`).")
    span = f"line {start}" if start == end else f"lines {start}-{end}"
    if lines_:
        steps.append(f"**Replace {span}** with:\n" + _fence("\n".join(lines_)))
    else:
        steps.append(f"**Delete {span}.** No arguments were passed, so there is nothing to port.")
    steps += ["**Also:** " + n for n in notes]
    uses = _uses(ctx, var, end)
    if uses:
        steps.append(f"**Remove `{var}` from where it is used** (e.g. `prompt_helper={var}`):\n" + _use_list(ctx, uses))
    return steps


def _agent_runner_or_worker(ctx: _Ctx, name: str) -> list[str]:
    call = _call_at(ctx, name)
    var, start, end = _statement(ctx, call)
    span = f"line {start}" if start == end else f"lines {start}-{end}"
    steps = [
        f"**Replace {span}.** `AgentRunner` / `AgentWorker` were removed. Use a workflow agent instead "
        "(`FunctionAgent` for tool-calling LLMs, or `AgentWorkflow` for several agents):\n"
        + _fence("from llama_index.core.agent.workflow import FunctionAgent\n\n"
                 f"{var or 'agent'} = FunctionAgent(\n    tools=tools,        # the tools you gave the worker\n"
                 "    llm=llm,          # the llm you gave the worker\n"
                 "    system_prompt=\"...\",  # replaces the worker's prompt settings\n)")
    ]
    imp = _import_of(ctx, name)
    if imp is not None:
        steps.append(f"**Imports:** remove `{name}` from line {imp.lineno}; the new import is shown above.")
    steps.append("**Remove the second class.** You no longer build a worker AND a runner: one `FunctionAgent` "
                 "replaces both, so delete the other `AgentWorker(...)` / `AgentRunner(...)` line too.")
    uses = _uses(ctx, var, end)
    if uses:
        steps.append("**Update how you talk to the agent** - it is async now:\n" + _use_list(ctx, uses) + "\n"
                     + _fence("# generic example (not from your file)\n"
                              "response = await agent.run(user_msg=\"question\")   # was: runner.chat(\"question\")\n"
                              "# async: call it inside an async function, or wrap with asyncio.run(...)"))
    return steps


def _query_pipeline(ctx: _Ctx) -> list[str]:
    call = _call_at(ctx, "QueryPipeline")
    var, start, end = _statement(ctx, call)
    span = f"line {start}" if start == end else f"lines {start}-{end}"
    steps = []
    imp = _import_of(ctx, "QueryPipeline")
    if imp is not None:
        steps.append(f"**Imports:** remove `QueryPipeline` from line {imp.lineno}, and add:\n"
                     + _fence("from llama_index.core.workflow import Workflow, StartEvent, StopEvent, step"))
    steps.append(
        f"**Rewrite {span} as a `Workflow`.** Each module/link in the pipeline becomes one `@step` method; "
        "what used to be a link becomes an event passed to the next step:\n"
        + _fence("# generic skeleton - fill in your own modules (not from your file)\n"
                 "class MyWorkflow(Workflow):\n"
                 "    @step\n"
                 "    async def retrieve(self, ev: StartEvent) -> StopEvent:\n"
                 "        nodes = await retriever.aretrieve(ev.query)   # was: first module\n"
                 "        return StopEvent(result=nodes)                # was: last link\n"))
    uses = _uses(ctx, var, end)
    if uses:
        steps.append(f"**Update where `{var}` is run:**\n" + _use_list(ctx, uses) + "\n"
                     + _fence("# generic example (not from your file)\n"
                              "result = await MyWorkflow(timeout=60).run(query=\"q\")   # was: pipeline.run(query=\"q\")"))
    return steps


_BUILDERS = {
    "legacy_message_graph": _message_graph,
    "legacy_create_react_agent": _create_react_agent,
    "legacy_agent_executor": _agent_executor,
    "legacy_service_context": _service_context,
    "legacy_llm_predictor": _llm_predictor,
    "legacy_prompt_helper": _prompt_helper,
    "legacy_agent_runner": lambda c: _agent_runner_or_worker(c, "AgentRunner"),
    "legacy_agent_worker": lambda c: _agent_runner_or_worker(c, "AgentWorker"),
    "legacy_query_pipeline": _query_pipeline,
}


def build_manual_steps(signal_name: str, line: int, text: str) -> list[str]:
    """Concrete steps for one detected signal. Never raises; may be empty."""
    builder = _BUILDERS.get(signal_name)
    if builder is None:
        return []
    try:
        tree = ast.parse(text)
        steps = builder(_Ctx(line=line, lines=text.splitlines(), tree=tree, text=text))
        return [s for s in steps if s]
    except Exception:
        return []
