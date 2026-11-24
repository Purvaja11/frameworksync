"""Writes FrameworkSync analysis results for the VS Code extension.

Rules this file guarantees:
  * a failed/partial analysis (syntax error, unexpected error) never erases
    the last good issues/suggestions of that file - it only adds `error`;
  * the JSON is replaced atomically, with retries for Windows file locks;
  * a problem while writing never raises into the watcher.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from frameworksync.config import VSCODE_RESULTS_PATH

RESULTS_PATH = VSCODE_RESULTS_PATH
_write_lock = threading.Lock()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(v) for v in sorted(value, key=str)]
    return str(value)


def _source_names(sources: Any) -> list[str]:
    names: list[str] = []
    for src in sources or []:
        if isinstance(src, str):
            name = src
        elif isinstance(src, dict):
            name = (src.get("metadata") or {}).get("source_name") or src.get("source_name")
        else:
            name = None
        if name and name not in names:
            names.append(name)
    return names


def _serialize_suggestion(suggestion: Any) -> dict:
    result: dict[str, Any] = {
        "line": suggestion.signal.line,
        "description": suggestion.signal.description,
        "explanation": suggestion.explanation,
        "sources": _source_names(suggestion.sources),
        "pending": bool(getattr(suggestion, "pending", False)),
    }

    migration = getattr(suggestion, "migration", None)
    if migration is not None:
        result["migration_signal"] = migration.signal
        result["migration"] = {
            "old_pattern": migration.old_pattern,
            "new_pattern": migration.new_pattern,
            "description": migration.description,
        }

    proposal = getattr(suggestion, "proposal", None)
    if proposal is not None:
        result["proposal"] = {
            "file_path": proposal.file_path,
            "line": proposal.line,
            "old_pattern": proposal.old_pattern,
            "new_pattern": proposal.new_pattern,
            "original_line": proposal.original_line,
            "proposed_line": proposal.proposed_line,
            "description": proposal.description,
            "original_code": proposal.original_code,
            "proposed_code": proposal.proposed_code,
            "start_line": proposal.start_line,
            "end_line": proposal.end_line,
            "applicable": proposal.applicable,
            "needs_review": proposal.needs_review,
            "edits": proposal.edits,
            "manual_steps": proposal.manual_steps,
        }
    return _json_safe(result)


def _serialize_issue(issue: Any) -> dict:
    return {
        "line": issue.line,
        "old_pattern": issue.old_pattern,
        "new_pattern": issue.new_pattern,
        "reason": issue.reason,
        "description": getattr(issue, "description", None)
        or f'"{issue.old_pattern}" is deprecated -> use "{issue.new_pattern}"',
        "library": issue.library,
        "source_name": issue.source_name,
    }


def _mtime_ms(path: Path) -> float | None:
    try:
        return os.stat(path).st_mtime * 1000
    except OSError:
        return None


def _load_existing() -> dict[str, Any]:
    try:
        text = RESULTS_PATH.read_text(encoding="utf-8")
        loaded = json.loads(text) if text.strip() else {}
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_write(data: dict[str, Any]) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS_PATH.with_name(f"{RESULTS_PATH.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    last_error: Exception | None = None
    for attempt in range(12):  # Windows: the editor may be reading the file right now
        try:
            tmp.replace(RESULTS_PATH)
            return
        except PermissionError as err:
            last_error = err
            time.sleep(0.05 * (attempt + 1))
    try:
        tmp.unlink()
    except OSError:
        pass
    raise last_error  # type: ignore[misc]


def vscode_callback(
    path: str | Path,
    libraries,
    issues: list[Any],
    suggestions: list[Any],
    error: str | None = None,
) -> None:
    absolute_path = str(Path(path).resolve())

    with _write_lock:
        results = _load_existing()
        previous = results.get(absolute_path)

        if error and isinstance(previous, dict):
            # Keep the last good analysis; just flag that the latest save could not be analysed.
            entry = dict(previous)
            entry["error"] = error
            entry["stale"] = True
            entry["file_mtime"] = _mtime_ms(Path(absolute_path))  # carried-over results stay visible
        else:
            entry = {
                "libraries": _json_safe(libraries),
                "error": error,
                "stale": bool(error),
                "file_mtime": _mtime_ms(Path(absolute_path)),  # editor hides results older than the file
                "issues": [_serialize_issue(i) for i in issues],
                "suggestions": [_serialize_suggestion(s) for s in suggestions],
            }
        results[absolute_path] = entry

        # Forget files that no longer exist so the file doesn't grow forever.
        results = {k: v for k, v in results.items() if k == absolute_path or os.path.exists(k)}

        _atomic_write(_json_safe(results))
