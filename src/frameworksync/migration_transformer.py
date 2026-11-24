from dataclasses import dataclass

@dataclass(frozen=True)
class Migration:
    signal: str
    old_pattern: str
    new_pattern: str
    description: str

MIGRATIONS = {
    "legacy_create_react_agent": Migration(
        "legacy_create_react_agent", "create_react_agent", "create_agent",
        "Migrate from create_react_agent to create_agent.",
    ),
    "legacy_agent_executor": Migration(
        "legacy_agent_executor", "AgentExecutor", "create_agent",
        "Migrate a recognizable create_react_agent + AgentExecutor pattern to create_agent.",
    ),
    "legacy_message_graph": Migration(
        "legacy_message_graph", "MessageGraph", "StateGraph",
        "Migrate MessageGraph to StateGraph with an explicit state schema; review graph state before applying.",
    ),
    "legacy_llm_predictor": Migration(
        "legacy_llm_predictor", "LLMPredictor", "direct LLM",
        "Replace LLMPredictor with direct LLM configuration; exact replacement depends on surrounding code.",
    ),
    "legacy_prompt_helper": Migration(
        "legacy_prompt_helper", "PromptHelper", "direct configuration",
        "Replace PromptHelper with the newer direct configuration approach; review surrounding configuration.",
    ),
    "legacy_service_context": Migration(
        "legacy_service_context", "ServiceContext", "Settings",
        "Migrate ServiceContext configuration to Settings; fields must be mapped individually.",
    ),
    "legacy_agent_runner": Migration(
        "legacy_agent_runner", "AgentRunner", "workflow-based API",
        "Migrate AgentRunner to the current workflow/agent API; review surrounding agent configuration.",
    ),
    "legacy_agent_worker": Migration(
        "legacy_agent_worker", "AgentWorker", "workflow-based API",
        "Migrate AgentWorker to the current workflow/agent API; review surrounding agent configuration.",
    ),
    "legacy_query_pipeline": Migration(
        "legacy_query_pipeline", "QueryPipeline", "Workflow",
        "Migrate QueryPipeline to the current workflow approach; review module/link configuration.",
    ),
}

# Only transformations whose exact source shape is proven are directly applicable.
SAFE_AUTO_MIGRATIONS = {"legacy_create_react_agent"}

def get_migration(signal_name):
    return MIGRATIONS.get(signal_name)

def get_migrations(signal_names):
    return [MIGRATIONS[name] for name in signal_names if name in MIGRATIONS]

def apply_migration(path, signal_name, line):
    if signal_name not in SAFE_AUTO_MIGRATIONS:
        return False
    migration = get_migration(signal_name)
    if migration is None:
        return False
    with open(path, encoding="utf-8", newline="") as handle:
        raw = handle.read()
    eol = "\r\n" if "\r\n" in raw else "\n"  # keep the file's own line endings
    lines = raw.splitlines()
    i = line - 1
    if not 0 <= i < len(lines) or migration.old_pattern not in lines[i]:
        return False
    lines[i] = lines[i].replace(migration.old_pattern, migration.new_pattern, 1)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(eol.join(lines) + eol)
    return True
