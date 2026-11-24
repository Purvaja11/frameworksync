from dataclasses import dataclass


LEGACY_CALL_SIGNALS = {
    "AgentExecutor": (
        "legacy_agent_executor",
        "Manually constructed agent executor",
    ),
    "create_react_agent": (
        "legacy_create_react_agent",
        "Legacy create_react_agent agent construction",
    ),
    "MessageGraph": (
        "legacy_message_graph",
        "Legacy MessageGraph construction",
    ),
    "LLMPredictor": (
        "legacy_llm_predictor",
        "Deprecated LLMPredictor construction",
    ),
    "PromptHelper": (
        "legacy_prompt_helper",
        "Deprecated PromptHelper construction",
    ),
    "ServiceContext": (
        "legacy_service_context",
        "Deprecated ServiceContext construction",
    ),
    "AgentRunner": (
        "legacy_agent_runner",
        "Deprecated AgentRunner construction",
    ),
    "AgentWorker": (
        "legacy_agent_worker",
        "Deprecated AgentWorker construction",
    ),
    "QueryPipeline": (
        "legacy_query_pipeline",
        "Deprecated QueryPipeline construction",
    ),
}


@dataclass(frozen=True)
class Signal:
    name: str
    description: str
    line: int


def find_signals(parsed):
    signals = []

    for call in parsed.calls:
        name = getattr(call, "name", None)
        line = getattr(call, "lineno", None)

        if name in LEGACY_CALL_SIGNALS and line is not None:
            signal_name, description = LEGACY_CALL_SIGNALS[name]

            signals.append(
                Signal(
                    name=signal_name,
                    description=description,
                    line=line,
                )
            )

    return signals