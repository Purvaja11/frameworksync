"""FastAPI server: serves the live UI and streams watcher results over
a WebSocket.

Run with:  uv run python -m frameworksync.web.app <path-to-watch>
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from frameworksync.detector import Issue
from frameworksync.watcher import watch

WATCH_TARGET: Path | None = None
_connections: list[WebSocket] = []
_main_loop: asyncio.AbstractEventLoop | None = None


def _watcher_callback(
    path: str, libraries: set[str], issues: list[Issue], suggestions: list, error: str | None
) -> None:
    """Called by watcher.py (on its own background thread) for every
    file change. Builds a plain-dict message and schedules it to be
    sent to every connected browser tab."""
    message = {
        "path": path,
        "libraries": sorted(libraries),
        "error": error,
        "issues": [
            {
                "line": i.line,
                "old_pattern": i.old_pattern,
                "new_pattern": i.new_pattern,
                "reason": i.reason,
                "library": i.library,
                "source_name": i.source_name,
            }
            for i in issues
        ],
        "suggestions": [
            {
                "line": s.signal.line,
                "description": s.signal.description,
                "explanation": s.explanation,
                "sources": list(s.sources),
            }
            for s in suggestions
        ],
    }
    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(_broadcast(message), _main_loop)


async def _broadcast(message: dict) -> None:
    dead = []
    for ws in _connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.remove(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    observer = watch(WATCH_TARGET, callback=_watcher_callback)
    print(f"Watching {WATCH_TARGET} — open http://127.0.0.1:8000 in your browser")
    yield
    observer.stop()
    observer.join()


app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # keeps the connection open; we ignore input
    except WebSocketDisconnect:
        _connections.remove(websocket)


def main() -> None:
    global WATCH_TARGET
    if len(sys.argv) < 2:
        print("Usage: uv run python -m frameworksync.web.app <path-to-watch>")
        sys.exit(1)

    target = Path(sys.argv[1]).resolve()
    if not target.is_dir():
        print(f"Not a folder: {target}")
        sys.exit(1)
    WATCH_TARGET = target

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()