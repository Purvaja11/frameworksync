"""Automatic migrations: every fix is applied through the extension's real edit engine (node)."""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from frameworksync import watcher  # noqa: E402
from frameworksync.migration_proposal import build_proposal  # noqa: E402
from frameworksync.migration_transformer import MIGRATIONS  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
APPLY = ROOT / "vscode-extension" / "test" / "apply_cli.js"


def apply(source, edits):
    out = subprocess.run(["node", str(APPLY)], input=json.dumps({"source": source, "edits": edits}),
                         capture_output=True, text=True, check=True)
    res = json.loads(out.stdout)
    assert res["ok"], res
    ast.parse(res["result"])           # the rewritten file must always be valid python
    return res["result"]


def proposal(tmp_path, src, line, signal):
    f = tmp_path / "x.py"
    f.write_text(src, encoding="utf8")
    return build_proposal(f, line, MIGRATIONS[signal])


def is_auto(p):
    return p.applicable and not p.needs_review


def test_six_of_nine_are_automatic_and_apply_one_by_one(tmp_path):
    f = tmp_path / "work.py"
    f.write_text((ROOT / "tests" / "fixtures" / "legacy_sample.py").read_text(encoding="utf8"), encoding="utf8")
    applied = []
    for _ in range(12):
        _, _, sugg = watcher.analyze(f)
        auto = [s for s in sugg if is_auto(s.proposal)]
        if not auto:
            break
        f.write_text(apply(f.read_text(encoding="utf8"), auto[0].proposal.edits), encoding="utf8")
        applied.append(auto[0].signal.name)
    assert len(applied) == 6
    _, _, left = watcher.analyze(f)
    assert sorted(s.signal.name for s in left) == ["legacy_agent_runner", "legacy_agent_worker", "legacy_query_pipeline"]


def test_service_context_arguments_uses_and_global_call(tmp_path):
    src = (
        "from llama_index.core import ServiceContext, VectorStoreIndex, set_global_service_context\n"
        "ctx = ServiceContext.from_defaults(llm=my_llm, chunk_size=512)\n"
        "set_global_service_context(ctx)\n"
        "index = VectorStoreIndex.from_documents(\n"
        "    docs,\n"
        "    service_context=ctx,\n"
        ")\n"
    )
    p = proposal(tmp_path, src, 2, "legacy_service_context")
    assert is_auto(p)
    out = apply(src, p.edits)
    assert "Settings.llm = my_llm" in out and "Settings.chunk_size = 512" in out
    assert "service_context" not in out and "ctx" not in out
    assert "from llama_index.core import VectorStoreIndex, Settings" in out


def test_service_context_used_in_unknown_way_stays_manual_with_reason(tmp_path):
    src = "from llama_index.core import ServiceContext\nctx = ServiceContext()\nprint(ctx.llm)\n"
    p = proposal(tmp_path, src, 2, "legacy_service_context")
    assert p.needs_review and "Not automatic here because" in p.description and p.manual_steps


def test_llm_predictor_uses_are_rewritten_to_the_llm(tmp_path):
    src = ("from llama_index.core import LLMPredictor\n"
           "pred = LLMPredictor(llm=my_llm)\n"
           "out = pred.predict(prompt)\n")
    p = proposal(tmp_path, src, 2, "legacy_llm_predictor")
    assert is_auto(p)
    out = apply(src, p.edits)
    assert "out = my_llm.predict(prompt)" in out and "LLMPredictor" not in out


def test_llm_predictor_passed_around_stays_manual(tmp_path):
    src = "from llama_index.core import LLMPredictor\npred = LLMPredictor(llm=m)\nbuild(llm_predictor=pred)\n"
    assert proposal(tmp_path, src, 2, "legacy_llm_predictor").needs_review


def test_prompt_helper_maps_to_settings_but_chunk_args_stay_manual(tmp_path):
    ok = "from llama_index.core import PromptHelper\nh = PromptHelper(4096, 256)\n"
    p = proposal(tmp_path, ok, 2, "legacy_prompt_helper")
    assert is_auto(p)
    out = apply(ok, p.edits)
    assert "Settings.context_window = 4096" in out and "Settings.num_output = 256" in out
    chunky = "from llama_index.core import PromptHelper\nh = PromptHelper(4096, 256, 0.1, 512)\n"
    assert proposal(tmp_path, chunky, 2, "legacy_prompt_helper").needs_review


def test_message_graph_is_automatic_only_when_nothing_depends_on_its_shape(tmp_path):
    simple = "from langgraph.graph import MessageGraph\ng = MessageGraph()\ng.add_edge('a', 'b')\n"
    p = proposal(tmp_path, simple, 2, "legacy_message_graph")
    assert is_auto(p)
    out = apply(simple, p.edits)
    assert "g = StateGraph(MessagesState)" in out and "import StateGraph, MessagesState" in out
    with_nodes = "from langgraph.graph import MessageGraph\ng = MessageGraph()\ng.add_node('a', fn)\n"
    p2 = proposal(tmp_path, with_nodes, 2, "legacy_message_graph")
    assert p2.needs_review and "add_node" in p2.description


def test_executor_is_automatic_when_agent_already_uses_create_agent(tmp_path):
    src = ("from langchain.agents import AgentExecutor, create_agent\n"
           "agent = create_agent(model, tools)\n"
           "executor = AgentExecutor(\n    agent=agent,\n    tools=tools,\n)\n"
           "x = 1\n")
    p = proposal(tmp_path, src, 3, "legacy_agent_executor")
    assert is_auto(p)
    out = apply(src, p.edits)
    assert "AgentExecutor(" not in out and "import AgentExecutor" not in out
    assert "x = 1" in out and "agent = create_agent(model, tools)" in out
    assert "# FrameworkSync: AgentExecutor wrapper removed" in out   # the explanatory note is kept on purpose
