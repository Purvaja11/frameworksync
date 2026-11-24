"""Wraps the local Ollama model as a LangChain chat model.

This file only knows how to get an LLM object. It doesn't know about
retrieval or prompts; rag_chain.py handles that.
"""

from langchain_ollama import ChatOllama

MODEL_NAME = "qwen2.5:3b"
TEMPERATURE = 0.1  # low = more consistent, less creative — good for factual doc answers


def get_llm() -> ChatOllama:
    """Return a configured handle to the local Ollama model."""
    return ChatOllama(model=MODEL_NAME, temperature=TEMPERATURE)
