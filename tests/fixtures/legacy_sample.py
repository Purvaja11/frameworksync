"""Frozen test fixture: one file containing each of the 9 legacy patterns exactly once.

Used by tests/test_auto_migrations.py and tests/test_manual_steps.py.
Nothing here is meant to run. Do not edit: the tests count on this exact content.
"""

# ---- LangChain -------------------------------------------------------------
from langchain.agents import AgentExecutor
from langgraph.prebuilt import create_react_agent

# ---- LangGraph -------------------------------------------------------------
from langgraph.graph import MessageGraph

# ---- LlamaIndex ------------------------------------------------------------
from llama_index.core import (
    LLMPredictor,
    PromptHelper,
    ServiceContext,
    VectorStoreIndex,
)
from llama_index.core.agent import AgentRunner, AgentWorker
from llama_index.core.query_pipeline import QueryPipeline


# 1. create_react_agent  (AUTOMATIC)
agent = create_react_agent(model, tools)

# 2. AgentExecutor  (AUTOMATIC once the agent uses create_agent)
executor = AgentExecutor(
    agent=agent,
    tools=tools,
)

# 3. MessageGraph  (AUTOMATIC)
graph = MessageGraph()
graph.add_edge("start", "reply")

# 4. LLMPredictor  (AUTOMATIC)
predictor = LLMPredictor(llm=llm)
answer = predictor.predict(prompt)

# 5. PromptHelper  (AUTOMATIC)
helper = PromptHelper(context_window=4096, num_output=256)

# 6. ServiceContext  (AUTOMATIC)
context = ServiceContext.from_defaults(llm=llm, chunk_size=512)
index = VectorStoreIndex.from_documents(docs, service_context=context)

# 7. AgentWorker  (GUIDED: manual steps)
worker = AgentWorker(tools, llm=llm)

# 8. AgentRunner  (GUIDED: manual steps)
runner = AgentRunner(worker)

# 9. QueryPipeline  (GUIDED: manual steps)
pipeline = QueryPipeline(chain=[prompt_tmpl, llm])
