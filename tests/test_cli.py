"""The `frameworksync` command only dispatches to existing modules."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import frameworksync  # noqa: E402


def test_help_lists_every_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["frameworksync", "--help"])
    frameworksync.main()
    out = capsys.readouterr().out
    for name in frameworksync.COMMANDS:
        assert name in out


def test_unknown_command_exits_with_code_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["frameworksync", "nope"])
    with pytest.raises(SystemExit) as info:
        frameworksync.main()
    assert info.value.code == 2
    assert "Unknown command" in capsys.readouterr().err


def test_command_runs_target_module_with_remaining_args(monkeypatch):
    calls = []
    monkeypatch.setattr(frameworksync.runpy, "run_module", lambda module, **kw: calls.append((module, kw, list(sys.argv))))
    monkeypatch.setattr(sys, "argv", ["frameworksync", "watch", "some/folder"])
    frameworksync.main()
    module, kw, argv = calls[0]
    assert module == "frameworksync.watcher"
    assert kw["run_name"] == "__main__"
    assert argv == ["frameworksync.watcher", "some/folder"]


def test_every_command_points_at_a_real_module():
    import importlib.util
    for module, _ in frameworksync.COMMANDS.values():
        assert importlib.util.find_spec(module) is not None, module
