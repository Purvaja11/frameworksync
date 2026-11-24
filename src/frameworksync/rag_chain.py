"""Combines retrieval (vector_store.py) with the LLM (llm.py) to answer
questions grounded in the scraped migration docs.
"""

from dataclasses import dataclass

from langchain_core.prompts import ChatPromptTemplate

from frameworksync.llm import get_llm
from frameworksync.vector_store import query

# Cosine distance threshold: chunks farther than this are treated as
# "not actually relevant" and dropped before the LLM ever sees them.
# Tune by testing — lower = stricter.
MAX_RELEVANT_DISTANCE = 0.6

SYSTEM_PROMPT = """You are FrameworkSync, a migration assistant for LangChain, \
LangGraph, and LlamaIndex.

Answer the question using only the Context below. Do not mention any class, \
function, or method name that does not appear in the Context. If the Context \
does not contain the answer, say exactly: "The docs I have don't cover this."

Context:
{context}
"""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "{question}"),
])


@dataclass
class RagAnswer:
    """An answer plus the sources it was grounded in, for traceability."""

    answer: str
    sources: list[dict]


def ask(question: str, library: str | None = None, n_results: int = 4) -> RagAnswer:
    """Answer a question using retrieved doc chunks as grounding."""
    chunks = query(question, library=library, n_results=n_results)
    relevant_chunks = [c for c in chunks if c["distance"] <= MAX_RELEVANT_DISTANCE]

    if not relevant_chunks:
        return RagAnswer(
            answer="The docs I have don't cover this.",
            sources=[],
        )

    context = "\n\n---\n\n".join(c["text"] for c in relevant_chunks)

    llm = get_llm()
    chain = prompt | llm
    response = chain.invoke({"context": context, "question": question})

    return RagAnswer(answer=response.content, sources=relevant_chunks)
