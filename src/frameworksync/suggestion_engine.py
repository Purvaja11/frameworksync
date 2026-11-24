"""Turns detected legacy signals into suggestions.

Two phases, so the editor never waits for the LLM:
  1. fast  - signal + migration + executable proposal (pure Python, instant);
             the explanation is taken from a cache or marked as pending.
  2. slow  - explain_signal() asks the local RAG/LLM once per signal and
             stores the answer in data/explanation_cache.json.
"""

import hashlib
import json
import threading
import time
from dataclasses import dataclass

from frameworksync.config import DATA_DIR
from frameworksync.migration_proposal import MigrationProposal, build_proposal
from frameworksync.migration_transformer import Migration, get_migration
from frameworksync.structural_signals import Signal

CACHE_PATH = DATA_DIR / "explanation_cache.json"
PENDING_TEXT = "Loading explanation from the local docs..."
FAILURE_RETRY_SECONDS = 120  # after a failed LLM call, wait before trying again

_failures: dict[str, tuple[float, str]] = {}  # cache key -> (time, message)

SIGNAL_QUESTIONS = {
    "legacy_agent_executor": (
        "How should an AgentExecutor-based agent be migrated "
        "to create_agent in LangChain v1, and what changes are "
        "required to the agent construction and invocation pattern?"
    ),
    "legacy_create_react_agent": (
        "How should create_react_agent be migrated "
        "to create_agent in LangChain v1?"
    ),
    "legacy_message_graph": (
        "Why is MessageGraph deprecated, and what should "
        "be used instead?"
    ),
    "legacy_llm_predictor": (
        "Why is LLMPredictor deprecated, and what should "
        "be used instead?"
    ),
    "legacy_prompt_helper": (
        "Why is PromptHelper deprecated, and what should "
        "be used instead?"
    ),
    "legacy_service_context": (
        "Why is ServiceContext deprecated, and what should "
        "be used instead?"
    ),
    "legacy_agent_runner": (
        "Why are AgentRunner and AgentWorker deprecated, "
        "and what should be used instead?"
    ),
    "legacy_agent_worker": (
        "Why are AgentRunner and AgentWorker deprecated, "
        "and what should be used instead?"
    ),
    "legacy_query_pipeline": (
        "Why is QueryPipeline deprecated, and what should "
        "be used instead?"
    ),
}


SIGNAL_LIBRARIES = {
    "legacy_agent_executor": "langchain",
    "legacy_create_react_agent": "langchain",
    "legacy_message_graph": "langgraph",
    "legacy_llm_predictor": "llamaindex",
    "legacy_prompt_helper": "llamaindex",
    "legacy_service_context": "llamaindex",
    "legacy_agent_runner": "llamaindex",
    "legacy_agent_worker": "llamaindex",
    "legacy_query_pipeline": "llamaindex",
}

@dataclass(frozen=True)
class Suggestion:
    signal: Signal
    explanation: str
    sources: list[str]
    migration: Migration | None = None
    proposal: MigrationProposal | None = None
    pending: bool = False  # explanation still being generated in the background


_cache_lock = threading.Lock()
_cache: dict | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(_cache, dict):
                _cache = {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _cache_key(signal_name: str, library: str) -> str:
    question = SIGNAL_QUESTIONS.get(signal_name, "")
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:10]
    return f"{library}::{signal_name}::{digest}"  # edit a question -> old answer is ignored


def _library_for(signal_name: str, library: str | None) -> str:
    return library or SIGNAL_LIBRARIES.get(signal_name, "langchain")


def _source_names(sources) -> list[str]:
    names: list[str] = []
    for src in sources or []:
        name = src if isinstance(src, str) else (
            (src.get("metadata") or {}).get("source_name") or src.get("source_name")
        )
        if name and name not in names:
            names.append(name)
    return names


def get_cached_explanation(signal_name: str, library: str | None = None) -> dict | None:
    with _cache_lock:
        return _load_cache().get(_cache_key(signal_name, _library_for(signal_name, library)))


def recent_failure(signal_name: str, library: str | None = None) -> str | None:
    key = _cache_key(signal_name, _library_for(signal_name, library))
    failure = _failures.get(key)
    if failure and time.monotonic() - failure[0] < FAILURE_RETRY_SECONDS:
        return failure[1]
    return None


def needs_explanation(signal_name: str, library: str | None = None) -> bool:
    return (
        signal_name in SIGNAL_QUESTIONS
        and get_migration(signal_name) is not None
        and get_cached_explanation(signal_name, library) is None
        and recent_failure(signal_name, library) is None
    )


def explain_signal(signal_name: str, library: str | None = None) -> dict | None:
    """Slow path: ask the local LLM once and cache the answer. None on failure."""
    question = SIGNAL_QUESTIONS.get(signal_name)
    if not question:
        return None
    lib = _library_for(signal_name, library)
    try:
        from frameworksync.rag_chain import ask  # heavy import, only when needed

        answer = ask(question, library=lib)
    except Exception as err:  # Ollama down, model missing, chroma locked...
        print(f"  [explain failed] {signal_name}: {err!r}", flush=True)
        message = f"{type(err).__name__}: {err}".replace("\n", " ")[:160]
        _failures[_cache_key(signal_name, lib)] = (time.monotonic(), message)
        return None
    _failures.pop(_cache_key(signal_name, lib), None)
    record = {"answer": answer.answer, "sources": _source_names(answer.sources)}
    with _cache_lock:
        cache = _load_cache()
        cache[_cache_key(signal_name, lib)] = record
        try:
            tmp = CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CACHE_PATH)
        except OSError as err:
            print(f"  [cache write failed] {err!r}", flush=True)
    return record


def build_suggestion(signal, path=None, library=None, explain: bool = False):
    migration = get_migration(signal.name)
    proposal = (
        build_proposal(path, signal.line, migration)
        if path is not None and migration
        else None
    )

    if migration is None:
        return Suggestion(
            signal=signal,
            explanation=(
                "FrameworkSync detected this legacy structural pattern, but no "
                "executable migration mapping is currently registered for it. "
                "Manual review is required."
            ),
            sources=[], migration=None, proposal=proposal,
        )

    if signal.name not in SIGNAL_QUESTIONS:
        return Suggestion(
            signal=signal,
            explanation=(
                "FrameworkSync detected this migration pattern, but no grounded "
                "migration question is registered for it."
            ),
            sources=[], migration=migration, proposal=proposal,
        )

    record = get_cached_explanation(signal.name, library)
    if record is None and explain:
        record = explain_signal(signal.name, library)

    if record is None:
        failure = recent_failure(signal.name, library)
        if failure:
            return Suggestion(
                signal=signal,
                explanation=(
                    f"Explanation unavailable ({failure}). Is Ollama running? "
                    "The migration itself is not affected."
                ),
                sources=[], migration=migration, proposal=proposal,
            )
        return Suggestion(
            signal=signal, explanation=PENDING_TEXT, sources=[],
            migration=migration, proposal=proposal, pending=True,
        )
    return Suggestion(
        signal=signal, explanation=record["answer"], sources=list(record["sources"]),
        migration=migration, proposal=proposal,
    )


def build_suggestions(signals, path=None, library=None, explain: bool = False):
    return [build_suggestion(s, path=path, library=library, explain=explain) for s in signals]
