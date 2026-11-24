import json
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

import frameworksync.vscode_bridge as bridge  # noqa: E402
import frameworksync.suggestion_engine as engine  # noqa: E402
from frameworksync import watcher  # noqa: E402
from frameworksync.single_instance import acquire  # noqa: E402

GOOD = (
    "from langgraph.prebuilt import create_react_agent\n"
    "from langgraph.graph import MessageGraph\n"
    "agent = create_react_agent(m, t)\n"
    "g = MessageGraph()\n"
)


def _use_tmp_results(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "RESULTS_PATH", tmp_path / "results.json")
    monkeypatch.setattr(engine, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(engine, "_cache", None)


def _analyze_to_bridge(path):
    libs, issues, suggestions = watcher.analyze(path)
    bridge.vscode_callback(path, libs, issues, suggestions, None)


def test_syntax_error_keeps_last_good_results(tmp_path, monkeypatch):
    _use_tmp_results(tmp_path, monkeypatch)
    f = tmp_path / "a.py"
    f.write_text(GOOD)
    _analyze_to_bridge(f)
    before = json.loads(bridge.RESULTS_PATH.read_text())[str(f.resolve())]
    assert len(before["suggestions"]) == 2

    bridge.vscode_callback(f, set(), [], [], "Skipped — syntax error: boom")
    after = json.loads(bridge.RESULTS_PATH.read_text())[str(f.resolve())]
    assert len(after["suggestions"]) == 2          # NOT wiped
    assert after["error"] and after["stale"] is True

    _analyze_to_bridge(f)                           # next good save clears the error
    healed = json.loads(bridge.RESULTS_PATH.read_text())[str(f.resolve())]
    assert healed["error"] is None and healed["stale"] is False


def test_sources_are_plain_names_and_issues_have_messages(tmp_path, monkeypatch):
    _use_tmp_results(tmp_path, monkeypatch)
    f = tmp_path / "a.py"
    f.write_text(GOOD)
    libs, issues, sugg = watcher.analyze(f)
    from frameworksync.structural_signals import Signal
    s = engine.Suggestion(Signal("legacy_message_graph", "d", 4), "x",
                          [{"text": "t" * 500, "metadata": {"source_name": "doc-a"}, "distance": 0.1}])
    bridge.vscode_callback(f, libs, issues, [s], None)
    entry = json.loads(bridge.RESULTS_PATH.read_text())[str(f.resolve())]
    assert entry["suggestions"][0]["sources"] == ["doc-a"]
    assert all("deprecated" in i["description"] for i in entry["issues"])
    keys = [(i["line"], i["old_pattern"]) for i in entry["issues"]]
    assert len(keys) == len(set(keys))              # create_react_agent not reported twice


def test_save_is_not_blocked_by_slow_llm(tmp_path, monkeypatch):
    _use_tmp_results(tmp_path, monkeypatch)
    calls = []

    def slow_ask(question, library=None, n_results=4):
        time.sleep(1.5)
        calls.append(question)
        return types.SimpleNamespace(answer="because", sources=[{"metadata": {"source_name": "doc"}}])

    fake = types.ModuleType("frameworksync.rag_chain")
    fake.ask = slow_ask
    monkeypatch.setitem(sys.modules, "frameworksync.rag_chain", fake)

    seen = []
    t0 = time.monotonic()
    handler = watcher.PythonFileHandler(
        lambda p, l, i, s, e: seen.append((time.monotonic() - t0, [x.pending for x in s], e)),
        debounce=0.1,
    )
    f = tmp_path / "a.py"
    f.write_text(GOOD)
    ev = types.SimpleNamespace(is_directory=False, src_path=str(f))
    handler.on_modified(ev)
    handler.on_modified(ev)  # burst of duplicate save events -> one analysis
    time.sleep(0.6)

    assert len(seen) == 1, seen
    assert seen[0][0] < 1.0                      # published long before the 1.5s LLM call finishes
    assert seen[0][1] == [True, True]            # explanations still pending

    time.sleep(5)                                 # background worker finishes
    assert len(calls) == 2                        # one LLM call per distinct signal, once
    assert seen[-1][1] == [False, False]          # republished with explanations
    # saving again uses the cache: no new LLM calls
    handler.on_modified(ev)
    time.sleep(0.6)
    assert len(calls) == 2


def test_syntax_error_reports_error_not_exception(tmp_path, monkeypatch):
    _use_tmp_results(tmp_path, monkeypatch)
    seen = []
    handler = watcher.PythonFileHandler(lambda p, l, i, s, e: seen.append(e), debounce=0.05, explain=False)
    f = tmp_path / "a.py"
    f.write_text("agent = create_react_agent(m, t\n")
    handler.on_modified(types.SimpleNamespace(is_directory=False, src_path=str(f)))
    time.sleep(0.4)
    assert seen and "syntax error" in seen[0]


def test_only_one_watcher_instance(tmp_path):
    lock = tmp_path / "w.lock"
    assert acquire(lock) is None                  # first one gets it
    lock.write_text(str(os.getppid()))            # pretend a different live process owns it
    assert acquire(lock) == os.getppid()          # second one is told who owns it
    lock.write_text("999999999")                  # dead pid -> stale lock is taken over
    assert acquire(lock) is None


def test_parent_folder_named_venv_is_not_ignored(tmp_path):
    root = tmp_path / "venv" / "proj"
    assert not watcher._is_ignored(root / "a.py", root)
    assert watcher._is_ignored(root / ".venv" / "lib" / "a.py", root)


def test_llm_failure_replaces_loading_text(tmp_path, monkeypatch):
    _use_tmp_results(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "_failures", {})

    def broken_ask(*a, **k):
        raise ConnectionError("ollama not reachable")

    fake = types.ModuleType("frameworksync.rag_chain")
    fake.ask = broken_ask
    monkeypatch.setitem(sys.modules, "frameworksync.rag_chain", fake)

    seen = []
    handler = watcher.PythonFileHandler(lambda p, l, i, s, e: seen.append(s), debounce=0.05)
    f = tmp_path / "a.py"
    f.write_text(GOOD)
    handler.on_modified(types.SimpleNamespace(is_directory=False, src_path=str(f)))
    time.sleep(1.5)

    assert seen[0][0].pending is True                         # first publish: loading
    last = seen[-1][0]
    assert last.pending is False and "unavailable" in last.explanation
    assert "ollama not reachable" in last.explanation


def test_results_record_file_version(tmp_path, monkeypatch):
    _use_tmp_results(tmp_path, monkeypatch)
    f = tmp_path / "a.py"
    f.write_text(GOOD)
    _analyze_to_bridge(f)
    entry = json.loads(bridge.RESULTS_PATH.read_text())[str(f.resolve())]
    assert abs(entry["file_mtime"] - os.stat(f).st_mtime * 1000) < 2
    f.write_text(GOOD + "# edit\n")
    bridge.vscode_callback(f, set(), [], [], "Skipped — syntax error")   # carried-over entry
    entry = json.loads(bridge.RESULTS_PATH.read_text())[str(f.resolve())]
    assert abs(entry["file_mtime"] - os.stat(f).st_mtime * 1000) < 2
