"""LlamaIndex engine for ReadMind.

    ReadMindLlamaRetriever (llama_index BaseRetriever)  -> RepoIndex.retrieve as NodeWithScore
    ReadMindLlamaLLM       (llama_index CustomLLM)      -> rag.llm.generate (same provider/model)
    RetrieverQueryEngine (response_mode="compact") with ReadMind's citation prompt

Usage from Python:
    from rag.frameworks.llamaindex_rag import answer_with_llamaindex
    answer_with_llamaindex("actualbudget__actual", "Why are budgets stored per month?", "full_history")
"""
from __future__ import annotations

import time
from typing import Any

from llama_index.core.base.llms.types import CompletionResponse, CompletionResponseGen, LLMMetadata
from llama_index.core.llms.callbacks import llm_completion_callback
from llama_index.core.llms.custom import CustomLLM
from llama_index.core.prompts import PromptTemplate
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

from config import LLM_MODEL, LLM_PROVIDER, TOP_K_FINAL
from rag.frameworks.common import EvidenceSource, evidence_header, resolve_index
from rag.llm import generate

EVIDENCE_SEPARATOR = "\n\n---"  # appended to each node so the joined context matches format_evidence


class ReadMindLlamaRetriever(BaseRetriever):
    """ReadMind's hybrid + history-graph retrieval behind LlamaIndex's retriever interface."""

    def __init__(self, index: EvidenceSource, types: set, k: int = TOP_K_FINAL, expand: bool = True):
        super().__init__()
        self._index, self._types, self._k, self._expand = index, set(types), k, expand
        self.last_timings: dict = {}
        self.last_elapsed_s: float = 0.0

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        start = time.perf_counter()
        evidence, timings = self._index.retrieve(query_bundle.query_str, self._types, k=self._k, expand=self._expand)
        self.last_elapsed_s = time.perf_counter() - start
        self.last_timings = timings
        nodes = []
        for e in evidence:
            node = TextNode(
                id_=e["id"],
                text=f"{evidence_header(e)}\n{e['text'][:1800]}{EVIDENCE_SEPARATOR}",
                metadata={k: v for k, v in e.items() if k != "text"} | {"full_text": e["text"]},
                # keep metadata out of the LLM prompt; the header already carries id/type/date/title
                excluded_llm_metadata_keys=["id", "type", "title", "date", "url", "score", "full_text", "files"],
                excluded_embed_metadata_keys=["full_text"],
            )
            nodes.append(NodeWithScore(node=node, score=e.get("score")))
        return nodes


class ReadMindLlamaLLM(CustomLLM):
    """LlamaIndex LLM backed by rag.llm.generate with ReadMind's system prompt."""

    system_text: str = ""
    provider: str = LLM_PROVIDER
    model_name: str = LLM_MODEL

    @property
    def metadata(self) -> LLMMetadata:
        # Large window so LlamaIndex never truncates or repacks the evidence (keeps runs comparable).
        return LLMMetadata(context_window=1_000_000, num_output=2048, model_name=f"{self.provider}:{self.model_name}")

    @llm_completion_callback()
    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(text=generate(self.system_text, prompt, provider=self.provider,
                                                model=self.model_name))

    @llm_completion_callback()
    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        yield self.complete(prompt, formatted=formatted, **kwargs)


def build_query_engine(retriever: ReadMindLlamaRetriever, repo_name: str, llm: CustomLLM | None = None):
    from rag.pipeline import SYSTEM_RAG
    llm = llm or ReadMindLlamaLLM(system_text=SYSTEM_RAG)
    qa_template = PromptTemplate(
        f"Repository: {repo_name}\n\nEvidence:\n\n{{context_str}}\n\nQuestion: {{query_str}}\n\nAnswer with citations:")
    return RetrieverQueryEngine.from_args(retriever, llm=llm, response_mode="compact",
                                         text_qa_template=qa_template)


def _node_to_evidence(n: NodeWithScore) -> dict:
    meta = dict(n.node.metadata)
    text = meta.pop("full_text", n.node.get_content())
    return {**meta, "text": text}


def answer_with_llamaindex(slug: str, question: str, condition: str = "full_history", k: int = TOP_K_FINAL,
                           index: EvidenceSource | None = None, llm: CustomLLM | None = None) -> dict:
    from rag.pipeline import ALL_CONDITIONS, SYSTEM_NO_CONTEXT, build_result

    spec = ALL_CONDITIONS[condition]
    index = resolve_index(slug, index)
    repo_name = index.stats.get("github", slug)

    t0 = time.perf_counter()
    if spec["types"] is None:  # llm_only baseline: no retriever, plain completion
        model = llm or ReadMindLlamaLLM(system_text=SYSTEM_NO_CONTEXT.format(repo=repo_name))
        t1 = time.perf_counter()
        answer = model.complete(f"Question: {question}").text
        evidence, timings = [], {}
    else:
        retriever = ReadMindLlamaRetriever(index, spec["types"], k=k, expand=spec.get("expand", True))
        response = build_query_engine(retriever, repo_name, llm).query(question)
        t1 = t0 + retriever.last_elapsed_s
        answer = str(response.response or "")
        evidence = [_node_to_evidence(n) for n in response.source_nodes]
        timings = dict(retriever.last_timings)
    t2 = time.perf_counter()
    return build_result(slug, question, condition, answer, evidence, timings, t0, t1, t2, engine="llamaindex")
