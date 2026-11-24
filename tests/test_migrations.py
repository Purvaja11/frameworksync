from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from frameworksync.migration_proposal import build_proposal
from frameworksync.migration_transformer import MIGRATIONS
from frameworksync.structural_signals import LEGACY_CALL_SIGNALS

def test_all_signals_have_migrations():
    signals = {value[0] for value in LEGACY_CALL_SIGNALS.values()}
    assert signals == set(MIGRATIONS)

def test_create_react_agent_proposal(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "from langgraph.prebuilt import create_react_agent\n"
        "agent = create_react_agent(model, tools)\n",
        encoding="utf8",
    )
    q = build_proposal(p, 2, MIGRATIONS["legacy_create_react_agent"])
    assert q.proposed_code == "agent = create_agent(model, tools)"
    assert q.edits[0]["new_text"] == "from langchain.agents import create_agent"
    assert q.edits[1]["new_text"] == "agent = create_agent(model, tools)"
    assert q.applicable and not q.needs_review

def test_agent_executor_structural_proposal(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "from langgraph.prebuilt import create_react_agent\n"
        "from langchain.agents import AgentExecutor\n"
        "agent = create_react_agent(model, tools)\n"
        "\n"
        "executor = AgentExecutor(agent=agent, tools=tools)\n",
        encoding="utf8",
    )
    q = build_proposal(p, 5, MIGRATIONS["legacy_agent_executor"])
    assert q is not None
    assert q.original_code == "agent = create_react_agent(model, tools)\nexecutor = AgentExecutor(agent=agent, tools=tools)"
    assert q.proposed_code == "agent = create_agent(model, tools)"
    assert q.applicable and not q.needs_review
    assert len(q.edits) == 4  # import, agent line, executor delete, AgentExecutor import

def test_agent_executor_with_later_use_requires_review(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "agent = create_react_agent(model, tools)\n"
        "executor = AgentExecutor(agent=agent, tools=tools)\n"
        "result = executor.invoke({})\n",
        encoding="utf8",
    )
    q = build_proposal(p, 2, MIGRATIONS["legacy_agent_executor"])
    assert q is not None
    assert q.applicable and q.needs_review

def test_nontrivial_llamaindex_migrations_never_emit_fake_code(tmp_path):
    p = tmp_path / "x.py"
    cases = [
        ("legacy_llm_predictor", "predictor = LLMPredictor(llm)"),
        ("legacy_prompt_helper", "helper = PromptHelper()"),
        ("legacy_service_context", "context = ServiceContext()"),
        ("legacy_agent_runner", "runner = AgentRunner()"),
        ("legacy_agent_worker", "worker = AgentWorker()"),
        ("legacy_query_pipeline", "pipeline = QueryPipeline()"),
    ]
    for signal, pattern in cases:
        p.write_text(pattern + "\n", encoding="utf8")
        q = build_proposal(p, 1, MIGRATIONS[signal])
        assert q is not None
        assert q.proposed_code is None
        assert q.needs_review
