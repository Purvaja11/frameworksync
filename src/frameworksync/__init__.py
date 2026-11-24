"""FrameworkSync: spots legacy LangChain / LangGraph / LlamaIndex code and helps migrate it.

The `frameworksync` command is a thin dispatcher: each sub-command runs the
existing module of the same job, so `frameworksync watch .` is exactly
`python -m frameworksync.watcher .`.
"""

import runpy
import sys

COMMANDS: dict[str, tuple[str, str]] = {
    "scrape": ("frameworksync.scraper.run_scraper", "download the official migration docs"),
    "patterns": ("frameworksync.build_patterns", "extract deprecated patterns from the docs"),
    "ingest": ("frameworksync.run_ingestion", "chunk + embed the docs into ChromaDB"),
    "ask": ("frameworksync.ask", "ask migration questions in the terminal (needs Ollama)"),
    "watch": ("frameworksync.watcher", "watch a folder and analyse .py files on save"),
    "ui": ("frameworksync.web.app", "live web UI at http://127.0.0.1:8000"),
}


def _usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = ["Usage: frameworksync <command> [args]", "", "Commands:"]
    lines += [f"  {name.ljust(width)}  {help_text}" for name, (_, help_text) in COMMANDS.items()]
    lines += ["", "Examples:", "  frameworksync watch path/to/your/code", "  frameworksync ui path/to/your/code"]
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help", "help"}:
        print(_usage())
        return

    command = args[0]
    if command not in COMMANDS:
        print(f"Unknown command: {command}\n\n{_usage()}", file=sys.stderr)
        sys.exit(2)

    module = COMMANDS[command][0]
    sys.argv = [module, *args[1:]]  # the target module reads its own arguments from sys.argv
    runpy.run_module(module, run_name="__main__", alter_sys=True)
