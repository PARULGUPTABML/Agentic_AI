"""LangChain engine for ReadMind.

    ReadMindRetriever (langchain_core BaseRetriever)
        wraps RepoIndex.retrieve -> hybrid BM25 + vector, RRF, history-graph expansion, rerank, quota
    ReadMindChatModel (langchain_core BaseChatModel)
        wraps rag.llm.generate -> gemini | openai | ollama | mock (same model as the native pipeline)
    build_chain()  LCEL:  {question} -> retriever -> format evidence -> ChatPromptTemplate -> model -> parser

Usage from Python:
    from rag.frameworks.langchain_rag import answer_with_langchain
    answer_with_langchain("infiniflow__ragflow", "Why was the task executor split?", "full_history")
"""
from __future__ import annotations

import time
from operator import itemgetter
from typing import Any, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from pydantic import ConfigDict, Field

from config import LLM_MODEL, LLM_PROVIDER, TOP_K_FINAL
from rag.frameworks.common import EvidenceSource, evidence_block, resolve_index
from rag.llm import generate


# --------------------------------------------------------------------------- retriever
class ReadMindRetriever(BaseRetriever):
    """Exposes ReadMind's history-aware hybrid retrieval as a LangChain retriever.
    Each Document carries the evidence id/type/date/url in metadata so citations can be verified."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    index: Any = Field(description="A RepoIndex (or any EvidenceSource)")
    types: set = Field(default_factory=lambda: {"code", "doc", "commit", "issue", "pr", "review"})
    k: int = TOP_K_FINAL
    expand: bool = True
    last_timings: dict = Field(default_factory=dict)
    last_elapsed_s: float = 0.0

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        start = time.perf_counter()
        evidence, timings = self.index.retrieve(query, self.types, k=self.k, expand=self.expand)
        self.last_elapsed_s = time.perf_counter() - start
        self.last_timings = timings
        return [Document(page_content=e["text"], metadata={k: v for k, v in e.items() if k != "text"})
                for e in evidence]


def documents_to_evidence(docs: list[Document]) -> list[dict]:
    return [{**d.metadata, "text": d.page_content} for d in docs]


def format_documents(docs: list[Document]) -> str:
    return "\n\n---\n\n".join(evidence_block(e) for e in documents_to_evidence(docs))


# --------------------------------------------------------------------------- chat model
class ReadMindChatModel(BaseChatModel):
    """LangChain chat model backed by rag.llm.generate, so LangChain answers use exactly the same
    provider, model, temperature and retry logic as the native pipeline (fair comparison)."""

    provider: str = LLM_PROVIDER
    model: str = LLM_MODEL
    temperature: float = 0.0

    @property
    def _llm_type(self) -> str:
        return f"readmind-{self.provider}"

    @property
    def _identifying_params(self) -> dict:
        return {"provider": self.provider, "model": self.model, "temperature": self.temperature}

    def _generate(self, messages: list[BaseMessage], stop: Optional[list[str]] = None,
                  run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        system = "\n\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))
        prompt = "\n\n".join(str(m.content) for m in messages if not isinstance(m, SystemMessage))
        text = generate(system, prompt, provider=self.provider, model=self.model, temperature=self.temperature)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


# --------------------------------------------------------------------------- prompts + chain
def _prompts():
    from rag.pipeline import SYSTEM_NO_CONTEXT, SYSTEM_RAG  # same wording as the native pipeline
    rag_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_RAG),
        ("human", "Repository: {repo}\n\nEvidence:\n\n{context}\n\nQuestion: {question}\n\nAnswer with citations:"),
    ])
    # SYSTEM_NO_CONTEXT contains "{repo}", which ChatPromptTemplate fills in from the input.
    no_ctx_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_NO_CONTEXT),
        ("human", "Question: {question}"),
    ])
    return rag_prompt, no_ctx_prompt


def build_chain(retriever: Optional[ReadMindRetriever], llm: Optional[BaseChatModel] = None) -> Runnable:
    """LCEL chain. Input: {"question", "repo"}. Output: {"question", "repo", "docs", "context", "answer"}.
    Pass retriever=None for the llm_only baseline."""
    llm = llm or ReadMindChatModel()
    rag_prompt, no_ctx_prompt = _prompts()
    if retriever is None:
        return RunnablePassthrough.assign(docs=RunnableLambda(lambda _: []), context=RunnableLambda(lambda _: "")) \
            | RunnablePassthrough.assign(answer=no_ctx_prompt | llm | StrOutputParser())
    return (
        RunnablePassthrough.assign(docs=itemgetter("question") | retriever)
        | RunnablePassthrough.assign(context=lambda x: format_documents(x["docs"]))
        | RunnablePassthrough.assign(answer=rag_prompt | llm | StrOutputParser())
    )


# --------------------------------------------------------------------------- entry point
def answer_with_langchain(slug: str, question: str, condition: str = "full_history", k: int = TOP_K_FINAL,
                          index: EvidenceSource | None = None, llm: Optional[BaseChatModel] = None) -> dict:
    from rag.pipeline import ALL_CONDITIONS, build_result

    spec = ALL_CONDITIONS[condition]
    index = resolve_index(slug, index)
    repo_name = index.stats.get("github", slug)

    retriever = None
    if spec["types"] is not None:
        retriever = ReadMindRetriever(index=index, types=set(spec["types"]), k=k, expand=spec.get("expand", True))
    chain = build_chain(retriever, llm).with_config(run_name="readmind-langchain")

    t0 = time.perf_counter()
    out = chain.invoke({"question": question, "repo": repo_name})
    t2 = time.perf_counter()
    t1 = t0 + (retriever.last_elapsed_s if retriever else 0.0)  # retrieval / generation split

    evidence = documents_to_evidence(out["docs"])
    timings = dict(retriever.last_timings) if retriever else {}
    return build_result(slug, question, condition, out["answer"], evidence, timings, t0, t1, t2, engine="langchain")
