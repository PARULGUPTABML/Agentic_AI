"""Framework engines for ReadMind's RAG pipeline.

ReadMind's retrieval (BM25 + vectors -> RRF -> history-graph expansion -> rerank -> history quota)
is the research contribution, so it is NOT replaced. Instead it is exposed through the standard
interfaces of two RAG frameworks, and the answer is produced by a framework-native chain:

    rag/frameworks/langchain_rag.py   LangChain  BaseRetriever + BaseChatModel + LCEL chain
    rag/frameworks/llamaindex_rag.py  LlamaIndex BaseRetriever + CustomLLM + RetrieverQueryEngine

Both reuse the same prompts (rag.pipeline.SYSTEM_RAG / SYSTEM_NO_CONTEXT), the same LLM provider
(rag.llm.generate, so gemini / openai / ollama / mock all work) and the same citation verifier and
guardrails, so their answers are directly comparable with the native pipeline.

Select an engine with:   POST /api/ask {"engine": "langchain" | "llamaindex" | "native", ...}
                         python -m evaluation.run_eval --engine langchain
"""
from rag.frameworks.common import EvidenceSource, evidence_header

__all__ = ["EvidenceSource", "evidence_header"]
