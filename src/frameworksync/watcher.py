"""Watches a folder for .py file saves and re-analyses them automatically.

Run standalone with:  uv run python -u -m frameworksync.watcher <path-to-watch>

How a save is handled (nothing here blocks the file-event thread):
  1. a trailing-edge debounce timer waits for the burst of save events to end;
  2. the fast analysis (parse + deprecated patterns + proposals) runs and the
     result is published immediately;
  3. missing LLM explanations are produced by ONE background worker, cached,
     and the file's result is re-published as each one arrives.
"""

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from frameworksync.detector import Issue, find_issues
from frameworksync.parser.code_parser import parse_file
from frameworksync.structural_signals import find_signals
from frameworksync.suggestion_engine import (
    Suggestion,
    build_suggestions,
    explain_signal,
    needs_explanation,
)

DEBOUNCE_SECONDS = 0.6

IGNORED_DIR_NAMES = {".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache"}


def _is_ignored(path: Path, root: Path | None = None) -> bool:
    parts = path.parts
    if root is not None:
        try:
            parts = path.relative_to(root).parts  # only judge folders inside the watched root
        except ValueError:
            pass
    return any(part in IGNORED_DIR_NAMES for part in parts)


# Called after every file check with (file_path, libraries_used, issues,
# suggestions, error_message). error_message is None on success.
ResultCallback = Callable[[str, set[str], list[Issue], list[Suggestion], str | None], None]


def _print_callback(
    path: str, libraries: set[str], issues: list[Issue], suggestions: list[Suggestion], error: str | None
) -> None:
    """Default callback: prints results to the terminal."""
    print(f"\n[changed] {path}")
    if error:
        print(f"  {error}")
        return
    if not libraries:
        print("  No tracked frameworks used.")
        return
    print(f"  Libraries: {', '.join(sorted(libraries))}")
    if issues:
        for issue in issues:
            print(
                f"  [WARNING] Line {issue.line}: \"{issue.old_pattern}\" is deprecated "
                f"-> use \"{issue.new_pattern}\" ({issue.library})"
            )
    else:
        print("  No known deprecated patterns found.")
    for suggestion in suggestions:
        print(f"  [SUGGESTION] Line {suggestion.signal.line}: {suggestion.signal.description}")
        print(f"    {suggestion.explanation}")
        steps = getattr(getattr(suggestion, "proposal", None), "manual_steps", None)
        if steps:
            print("    To do by hand:")
            for number, step in enumerate(steps, 1):
                print(f"      {number}. " + step.replace("\n", "\n         "))


def analyze(path: Path) -> tuple[set[str], list[Issue], list[Suggestion]]:
    """Fast analysis of one file (no LLM). Raises SyntaxError / OSError."""
    parsed = parse_file(path)
    if not parsed.libraries_used:
        return set(), [], []
    issues = find_issues(parsed)
    signals = find_signals(parsed)
    suggestions = build_suggestions(signals, path=path) if signals else []
    return parsed.libraries_used, issues, suggestions


class PythonFileHandler(FileSystemEventHandler):
    """Reacts whenever a .py file is created, modified or moved in."""

    def __init__(self, callback: ResultCallback = _print_callback, root: Path | None = None,
                 debounce: float = DEBOUNCE_SECONDS, explain: bool = True):
        self._callback = callback
        self._root = root
        self._debounce = debounce
        self._explain = explain
        self._lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}
        self._versions: dict[str, int] = {}
        self._queue: "queue.Queue[tuple[str, int]]" = queue.Queue()
        self._queued: set[str] = set()
        self._worker: threading.Thread | None = None

    # ---- watchdog events (must stay instant) -------------------------------
    def on_modified(self, event):
        self._schedule(event)

    def on_created(self, event):
        self._schedule(event)

    def on_moved(self, event):
        self._schedule(event, use_dest=True)

    def _schedule(self, event, use_dest: bool = False):
        if event.is_directory:
            return
        path = Path(event.dest_path if use_dest else event.src_path)
        if path.suffix != ".py" or _is_ignored(path, self._root):
            return
        key = str(path)
        with self._lock:
            old = self._timers.pop(key, None)
            if old:
                old.cancel()
            timer = threading.Timer(self._debounce, self._run, args=(path,))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    # ---- analysis ----------------------------------------------------------
    def _publish(self, path: Path, libs, issues, suggestions, error) -> None:
        try:
            self._callback(str(path), libs, issues, suggestions, error)
        except Exception as err:  # a broken consumer must never poison the watcher
            print(f"  [callback failed] {err!r}", flush=True)

    def _run(self, path: Path) -> None:
        key = str(path)
        with self._lock:
            self._timers.pop(key, None)
            version = self._versions.get(key, 0) + 1
            self._versions[key] = version
        try:
            if not path.exists():
                return
            if path.stat().st_size == 0:
                time.sleep(0.2)  # editor is mid-write
                if not path.exists() or path.stat().st_size == 0:
                    return
            libs, issues, suggestions = analyze(path)
        except FileNotFoundError:
            return
        except SyntaxError as err:
            self._publish(path, set(), [], [], f"Skipped — syntax error: {err}")
            return
        except Exception as err:
            self._publish(path, set(), [], [], f"Unexpected error: {err!r}")
            return

        self._publish(path, libs, issues, suggestions, None)

        if self._explain and any(s.pending for s in suggestions):
            self._enqueue_explanations(key, version)

    # ---- background explanations -------------------------------------------
    def _enqueue_explanations(self, key: str, version: int) -> None:
        with self._lock:
            if key in self._queued:
                return
            self._queued.add(key)
            if self._worker is None:
                self._worker = threading.Thread(target=self._worker_loop, daemon=True)
                self._worker.start()
        self._queue.put((key, version))

    def _worker_loop(self) -> None:
        while True:
            key, version = self._queue.get()
            with self._lock:
                self._queued.discard(key)
            try:
                self._explain_file(Path(key), version)
            except Exception as err:
                print(f"  [explain worker] {err!r}", flush=True)

    def _explain_file(self, path: Path, version: int) -> None:
        key = str(path)
        _, _, suggestions = analyze(path)
        for suggestion in suggestions:
            name = suggestion.signal.name
            if not needs_explanation(name):
                continue
            if self._versions.get(key) != version:
                return  # file saved again; that run schedules its own explanations
            failed = explain_signal(name) is None
            if self._versions.get(key) != version:
                return
            libs, issues, fresh = analyze(path)
            self._publish(path, libs, issues, fresh, None)  # also replaces "Loading..." on failure
            if failed:
                return  # LLM is unreachable; don't hammer it once per signal


def watch(target: Path, callback: ResultCallback = _print_callback) -> Observer:
    """Start watching `target` for .py changes. Returns the Observer so the
    caller can .stop()/.join() it (used by the web server)."""
    event_handler = PythonFileHandler(callback, root=target)
    observer = Observer()
    observer.schedule(event_handler, str(target), recursive=True)
    observer.start()
    return observer


def _run_forever(observer: Observer, banner: str) -> None:
    print(banner, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nStopped.")
    observer.join()


def main_for_vscode() -> None:
    """Like main(), but also writes results to vscode_results.json for the extension."""
    if len(sys.argv) < 2:
        print("Usage: uv run python -u -m frameworksync.watcher <path-to-watch> --vscode")
        sys.exit(1)

    target = Path(sys.argv[1]).resolve()
    if not target.is_dir():
        print(f"Not a folder: {target}")
        sys.exit(1)

    from frameworksync.config import DATA_DIR
    from frameworksync.single_instance import acquire
    from frameworksync.vscode_bridge import vscode_callback

    owner = acquire(DATA_DIR / "watcher.lock")
    if owner is not None:
        print(f"Another FrameworkSync watcher is already running (pid {owner}). Exiting.", flush=True)
        sys.exit(0)

    def combined_callback(path, libraries, issues, suggestions, error):
        _print_callback(path, libraries, issues, suggestions, error)
        try:
            vscode_callback(path, libraries, issues, suggestions, error)
        except Exception as err:
            print(f"  [vscode bridge failed] {err!r}", flush=True)

    observer = watch(target, callback=combined_callback)
    _run_forever(observer, f"Watching {target} for .py changes (VS Code mode)... (Ctrl+C to stop)")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python -u -m frameworksync.watcher <path-to-watch>")
        sys.exit(1)

    target = Path(sys.argv[1]).resolve()
    if not target.exists():
        print(f"Path does not exist: {target}")
        sys.exit(1)
    if not target.is_dir():
        print(f"Path is not a folder: {target}")
        sys.exit(1)

    observer = watch(target)
    _run_forever(observer, f"Watching {target} for .py changes... (Ctrl+C to stop)")


if __name__ == "__main__":
    if "--vscode" in sys.argv:
        sys.argv.remove("--vscode")
        main_for_vscode()
    else:
        main()
