"""Pieces shared by the LangChain and LlamaIndex engines."""
from __future__ import annotations

from typing import Protocol


class EvidenceSource(Protocol):
    """What the framework adapters need from an index. `rag.retriever.RepoIndex` satisfies it, and
    tests can pass a tiny stub instead (so CI needs no Chroma / torch)."""

    stats: dict

    def retrieve(self, query: str, types: set, k: int = 8, expand: bool = True) -> tuple[list[dict], dict]:
        ...


def evidence_header(e: dict) -> str:
    """Same header format as rag.pipeline.format_evidence, so citations look identical."""
    date = f", {e['date'][:10]}" if e.get("date") else ""
    return f"[{e['id']}] ({e['type']}{date}) {e.get('title', '')}"


def evidence_block(e: dict, max_chars: int = 1800) -> str:
    return f"{evidence_header(e)}\n{e['text'][:max_chars]}"


def resolve_index(slug: str, index: EvidenceSource | None) -> EvidenceSource:
    if index is not None:
        return index
    from rag.retriever import get_index  # lazy: keeps chromadb out of CI imports
    return get_index(slug)
