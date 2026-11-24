"""Makes sure only one watcher writes vscode_results.json at a time.

Two watchers writing the same results file overwrite each other and each
run their own LLM calls, so a second one simply exits.
"""

import atexit
import os
import sys
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # NOTE: never use os.kill(pid, 0) on Windows - it terminates the process.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire(lock_path: Path) -> int | None:
    """Take the lock. Returns None on success, or the PID of the live owner."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            with open(lock_path, "x", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            break
        except FileExistsError:
            try:
                owner = int(lock_path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                owner = 0
            if owner != os.getpid() and _pid_alive(owner):
                return owner
            try:
                lock_path.unlink()  # stale lock from a dead process
            except OSError:
                pass
    else:
        return -1

    def _release() -> None:
        try:
            if lock_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                lock_path.unlink()
        except OSError:
            pass

    atexit.register(_release)
    return None
