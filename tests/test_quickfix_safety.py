"""Quick-fix edits must never delete unrelated code, join lines, or break imports.

The edits are applied with the extension's own planner (vscode-extension/edits.js)
through node, so this tests exactly what VS Code will do.
"""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from frameworksync.migration_proposal import build_proposal  # noqa: E402
from frameworksync.migration_transformer import MIGRATIONS  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run_planner(source: str, edits: list[dict]) -> dict:
    out = subprocess.run(
        ["node", str(ROOT / "vscode-extension" / "test" / "apply_cli.js")],
        input=json.dumps({"source": source, "edits": edits}),
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def propose(tmp_path, source, line, signal):
    p = tmp_path / "x.py"
    p.write_text(source, encoding="utf8")
    return build_proposal(p, line, MIGRATIONS[signal])


def test_executor_fix_keeps_code_between_agent_and_executor(tmp_path):
    src = (
        "from langgraph.prebuilt import create_react_agent\n"
        "from langchain.agents import AgentExecutor\n"
        "\n"
        "agent = create_react_agent(model, tools)\n"
        "tools_extra = load_important_tools()\n"
        "memory = setup_memory()\n"
        "executor = AgentExecutor(agent=agent, tools=tools)\n"
        'print("done")\n'
    )
    q = propose(tmp_path, src, 7, "legacy_agent_executor")
    assert q.applicable and not q.needs_review
    res = run_planner(src, q.edits)
    assert res["ok"]
    out = res["result"]
    ast.parse(out)
    assert "tools_extra = load_important_tools()" in out
    assert "memory = setup_memory()" in out
    assert "agent = create_agent(model, tools)\n" in out
    assert 'print("done")' in out
    assert "AgentExecutor" not in out and "create_react_agent" not in out


def test_multiline_executor_gets_a_quick_fix(tmp_path):
    src = (
        "from langgraph.prebuilt import create_react_agent\n"
        "from langchain.agents import AgentExecutor\n"
        "agent = create_react_agent(\n"
        "    model,\n"
        "    tools,\n"
        ")\n"
        "executor = AgentExecutor(\n"
        "    agent=agent,\n"
        "    tools=tools,\n"
        ")\n"
        "x = 1\n"
    )
    q = propose(tmp_path, src, 7, "legacy_agent_executor")
    assert q.applicable and not q.needs_review
    assert (q.start_line, q.end_line) == (7, 10)
    out = run_planner(src, q.edits)["result"]
    ast.parse(out)
    assert "agent = create_agent(" in out and "x = 1" in out and "AgentExecutor" not in out


def test_edit_never_joins_lines(tmp_path):
    src = "from langgraph.prebuilt import create_react_agent\nagent = create_react_agent(m, t)\nprint(1)\n"
    q = propose(tmp_path, src, 2, "legacy_create_react_agent")
    out = run_planner(src, q.edits)["result"]
    assert out == "from langchain.agents import create_agent\nagent = create_agent(m, t)\nprint(1)\n"


def test_other_names_in_import_stay_in_langgraph(tmp_path):
    src = "from langgraph.prebuilt import create_react_agent, ToolNode\nagent = create_react_agent(m, t)\n"
    q = propose(tmp_path, src, 2, "legacy_create_react_agent")
    out = run_planner(src, q.edits)["result"]
    ast.parse(out)
    assert "from langgraph.prebuilt import ToolNode" in out
    assert "from langchain.agents import create_agent" in out


def test_second_create_react_agent_use_keeps_old_import(tmp_path):
    src = (
        "from langgraph.prebuilt import create_react_agent\n"
        "a = create_react_agent(m, t)\n"
        "b = create_react_agent(m, t)\n"
    )
    q = propose(tmp_path, src, 2, "legacy_create_react_agent")
    out = run_planner(src, q.edits)["result"]
    ast.parse(out)
    assert "from langgraph.prebuilt import create_react_agent" in out  # still needed by line 3
    assert "from langchain.agents import create_agent" in out


def test_attribute_call_is_not_auto_rewritten(tmp_path):
    src = "import langgraph.prebuilt as pb\nagent = pb.create_react_agent(m, t)\n"
    q = propose(tmp_path, src, 2, "legacy_create_react_agent")
    assert q.proposed_code is None and not q.applicable


def test_executor_with_extra_options_needs_review(tmp_path):
    src = (
        "from langgraph.prebuilt import create_react_agent\n"
        "agent = create_react_agent(m, t)\n"
        "executor = AgentExecutor(agent=agent, tools=t, verbose=True)\n"
    )
    q = propose(tmp_path, src, 3, "legacy_agent_executor")
    assert q.needs_review


def test_stale_edit_is_refused(tmp_path):
    src = "from langgraph.prebuilt import create_react_agent\nagent = create_react_agent(m, t)\n"
    q = propose(tmp_path, src, 2, "legacy_create_react_agent")
    changed = "# new line above\n" + src  # file edited after analysis: lines shifted
    res = run_planner(changed, q.edits)
    assert res["ok"] is False and "changed" in res["reason"]
