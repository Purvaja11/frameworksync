import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from frameworksync import watcher  # noqa: E402
from frameworksync.manual_steps import build_manual_steps  # noqa: E402
from frameworksync.migration_proposal import build_proposal  # noqa: E402
from frameworksync.migration_transformer import MIGRATIONS  # noqa: E402


def test_every_review_only_proposal_has_concrete_steps():
    """No more 'review manually' without telling the user what to do."""
    path = ROOT / "tests" / "fixtures" / "legacy_sample.py"   # frozen file with all 9 patterns
    _, _, suggestions = watcher.analyze(path)
    assert len(suggestions) == 9
    manual = 0
    for s in suggestions:
        p = s.proposal
        if p.needs_review:
            manual += 1
            assert p.manual_steps, f"{s.signal.name} has no manual steps"
            assert all(isinstance(x, str) and x.strip() for x in p.manual_steps)
        else:
            assert not p.manual_steps
    assert manual == 3   # AgentRunner, AgentWorker, QueryPipeline


def test_message_graph_steps_use_the_users_own_lines(tmp_path):
    src = (
        "from langgraph.graph import MessageGraph\n"
        "graph = MessageGraph()\n"
        "graph.add_node('a', my_node)\n"
        "app = graph.compile()\n"
        "out = app.invoke([msg])\n"
    )
    steps = "\n".join(build_manual_steps("legacy_message_graph", 2, src))
    assert "from langgraph.graph import StateGraph, MessagesState" in steps
    assert "graph = StateGraph(MessagesState)" in steps
    assert "line 3" in steps and "line 5" in steps          # add_node + invoke found
    assert 'return {"messages": [reply]}' in steps


def test_service_context_arguments_become_settings_lines():
    src = (
        "from llama_index.core import ServiceContext\n"
        "ctx = ServiceContext.from_defaults(llm=my_llm, chunk_size=512, num_output=256)\n"
        "index = VectorStoreIndex.from_documents(docs, service_context=ctx)\n"
    )
    steps = "\n".join(build_manual_steps("legacy_service_context", 2, src))
    assert "Settings.llm = my_llm" in steps
    assert "Settings.chunk_size = 512" in steps and "Settings.num_output = 256" in steps
    assert "line 3" in steps                                  # where ctx is still used


def test_executor_with_later_uses_and_options_explains_each(tmp_path):
    f = tmp_path / "x.py"
    f.write_text(
        "from langgraph.prebuilt import create_react_agent\n"
        "from langchain.agents import AgentExecutor\n"
        "agent = create_react_agent(m, t, prompt='hi')\n"
        "executor = AgentExecutor(agent=agent, tools=t, verbose=True, max_iterations=5)\n"
        "print(executor.invoke({'input': 'q'}))\n"
    )
    p = build_proposal(f, 4, MIGRATIONS["legacy_agent_executor"])
    text = "\n".join(p.manual_steps)
    assert p.needs_review
    assert "system_prompt" in text                 # prompt= rename
    assert "recursion_limit" in text and "debug=True" in text
    assert "line 5" in text                        # later use of executor
    assert "`print(agent.invoke({'input': 'q'}))`" in text      # the user's own line, rewritten
    assert '["messages"][-1].content' in text


def test_steps_never_raise_on_odd_input():
    assert build_manual_steps("legacy_message_graph", 99, "x = (\n") == []
    assert build_manual_steps("unknown_signal", 1, "x = 1\n") == []
    for sig in MIGRATIONS:
        build_manual_steps(sig, 1, "x = 1\n")      # pattern not on that line: must not crash


def _joined(signal, line, src):
    return "\n".join(build_manual_steps(signal, line, src))


def test_steps_are_derived_from_each_file_not_hardcoded():
    one = "from langgraph.graph import MessageGraph\ng = MessageGraph()\ng.add_node('n', fn)\n"
    two = ("import os\nfrom langgraph.graph import END, MessageGraph, START\n\n\n"
           "builder = MessageGraph()\nbuilder.add_node('x', fn)\n")
    a, b = _joined("legacy_message_graph", 2, one), _joined("legacy_message_graph", 5, two)

    assert "line 2" in a and "g = StateGraph(MessagesState)" in a and "line 3" in a
    assert "line 5" in b and "builder = StateGraph(MessagesState)" in b and "line 6" in b
    assert "END, START, StateGraph, MessagesState" in b          # other imports preserved
    assert "builder" not in a and "g = " not in b                # nothing leaks between files
    assert "graph = StateGraph" not in a + b                      # no hardcoded variable name


def test_module_qualified_call_keeps_users_prefix_and_no_placeholder_text():
    src = "import langgraph.graph as lg\nflow = lg.MessageGraph()\n"
    text = _joined("legacy_message_graph", 2, src)
    assert "flow = lg.StateGraph(lg.MessagesState)" in text
    assert "the right module" not in text


def test_uses_are_rewritten_from_the_users_own_lines():
    src = ("from llama_index.core import ServiceContext, set_global_service_context\n"
           "sc = ServiceContext.from_defaults(llm=my_llm)\n"
           "set_global_service_context(sc)\n"
           "idx = VectorStoreIndex.from_documents(docs, service_context=sc)\n")
    text = _joined("legacy_service_context", 2, src)
    assert "`idx = VectorStoreIndex.from_documents(docs)`" in text
    assert "delete this line" in text
    assert "Settings.llm = my_llm" in text
