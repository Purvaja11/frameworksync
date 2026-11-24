import shutil
import subprocess
from pathlib import Path

import pytest

EXT = Path(__file__).parents[1] / "vscode-extension"


@pytest.mark.skipif(shutil.which("node") is None or shutil.which("git") is None, reason="needs node and git")
def test_git_safety_net_commit_and_restore():
    out = subprocess.run(["node", str(EXT / "test" / "git_safety_test.js")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "all checks passed" in out.stdout
