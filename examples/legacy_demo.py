"""FrameworkSync demo: all 9 legacy patterns in one file.
Save this file and hover each underline. Nothing here is meant to run."""

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


# 1. create_react_agent  (AUTOMATIC: renamed, import moved to langchain.agents)
agent = create_react_agent(model, tools)

# 2. AgentExecutor  (AUTOMATIC: wrapper removed, the agent is used directly)
executor = AgentExecutor(
    agent=agent,
    tools=tools,
)

# 3. MessageGraph  (AUTOMATIC: becomes StateGraph(MessagesState))
graph = MessageGraph()
graph.add_edge("start", "reply")

# 4. LLMPredictor  (AUTOMATIC: uses become llm.predict(...))
predictor = LLMPredictor(llm=llm)
answer = predictor.predict(prompt)

# 5. PromptHelper  (AUTOMATIC: becomes Settings.context_window / num_output)
helper = PromptHelper(context_window=4096, num_output=256)

# 6. ServiceContext  (AUTOMATIC: becomes Settings.llm / chunk_size)
context = ServiceContext.from_defaults(llm=llm, chunk_size=512)
index = VectorStoreIndex.from_documents(docs, service_context=context)

# 7. AgentWorker  (GUIDED: numbered manual steps, no automatic fix)
worker = AgentWorker(tools, llm=llm)

# 8. AgentRunner  (GUIDED: numbered manual steps, no automatic fix)
runner = AgentRunner(worker)

# 9. QueryPipeline  (GUIDED: numbered manual steps, no automatic fix)
pipeline = QueryPipeline(chain=[prompt_tmpl, llm])
