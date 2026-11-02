"""Hybrid retrieval: BM25 + vector search, fused with Reciprocal Rank Fusion, expanded through the
history graph, then (optionally) reranked with a cross-encoder."""
import json
import re
import time
from functools import lru_cache

import chromadb
import numpy as np
from rank_bm25 import BM25Okapi

from config import HISTORY_QUOTA, INDEX_DIR, RERANK_MODEL, TOP_K_CANDIDATES, TOP_K_FINAL, USE_RERANKER
from ingestion.link_graph import linked, load_graph, node_type
from rag.embeddings import embed_query
from rag.text_utils import tokenize

HISTORY_TYPES = {"commit", "issue", "pr", "review"}
RRF_K = 60


@lru_cache(maxsize=1)
def _reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(RERANK_MODEL)


class RepoIndex:
    def __init__(self, slug: str):
        d = INDEX_DIR / slug
        if not (d / "docs.jsonl").exists():
            raise FileNotFoundError(f"Repo '{slug}' is not indexed")
        self.slug = slug
        self.stats = json.loads((d / "stats.json").read_text(encoding="utf-8"))
        self.docs = {}
        with open(d / "docs.jsonl", encoding="utf-8") as f:
            for line in f:
                doc = json.loads(line)
                self.docs[doc["id"]] = doc
        self.ids = list(self.docs)
        self.types = np.array([self.docs[i]["type"] for i in self.ids])
        self.bm25 = BM25Okapi([tokenize(self.docs[i]["text"]) for i in self.ids])
        self.collection = chromadb.PersistentClient(path=str(d / "chroma")).get_collection("docs")
        self.graph = load_graph(d / "graph.json")

    def get_typed_ids(self, types=None):
        """Return list of document IDs filtered by type(s). Supports comma-separated string or iterable."""
        if not types or types == "all":
            return self.ids
        if isinstance(types, str):
            types_set = set(t.strip() for t in types.split(",") if t.strip())
        else:
            types_set = set(types)
        mask = np.isin(self.types, list(types_set))
        return [self.ids[i] for i in np.where(mask)[0]]

    # ---------- individual retrievers ----------
    def bm25_search(self, query, types, k):
        scores = self.bm25.get_scores(tokenize(query))
        scores = np.where(np.isin(self.types, list(types)), scores, -1)
        top = np.argsort(-scores)[:k]
        return [self.ids[i] for i in top if scores[i] > 0]

    def vector_search(self, query, types, k):
        res = self.collection.query(query_embeddings=[embed_query(query)], n_results=k,
                                    where={"type": {"$in": sorted(types)}})
        return res["ids"][0]

    def direct_refs(self, query, types):
        """Explicit references in the question: #123, a commit sha, a file path."""
        hits = []
        for num in re.findall(r"#(\d+)", query):
            for t in ("pr", "issue"):
                if t in types and f"{t}:{num}" in self.docs:
                    hits.append(f"{t}:{num}")
        if "commit" in types:
            for sha in re.findall(r"\b[0-9a-f]{7,40}\b", query):
                hits += [i for i in self.ids if i.startswith(f"commit:{sha[:10]}")][:1]
        return hits

    def resolve_id(self, doc_id):
        """Accept shortened commit ids like commit:1a2b3c4."""
        if doc_id in self.docs:
            return doc_id
        if doc_id.startswith("commit:"):
            for i in self.ids:
                if i.startswith(doc_id[:17]) or doc_id.startswith(i):
                    return i
        return None

    # ---------- full pipeline ----------
    def retrieve(self, query, types, k=TOP_K_FINAL, expand=True, rerank=USE_RERANKER):
        timings, t0 = {}, time.perf_counter()
        n = TOP_K_CANDIDATES
        lists = [self.bm25_search(query, types, n), self.vector_search(query, types, n)]
        fused = {}
        for lst in lists:
            for rank, doc_id in enumerate(lst):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        top_score = max(fused.values(), default=1.0)
        for doc_id in self.direct_refs(query, types):
            fused[doc_id] = top_score + 1.0
        timings["search_ms"] = (time.perf_counter() - t0) * 1000

        if expand and types & HISTORY_TYPES:
            seeds = sorted(fused, key=fused.get, reverse=True)[:10]
            for s in seeds:
                for nb in linked(self.graph, s, types & HISTORY_TYPES):
                    if nb in self.docs and nb not in fused:
                        fused[nb] = fused[s] * 0.5
        timings["expand_ms"] = (time.perf_counter() - t0) * 1000 - timings["search_ms"]

        candidates = sorted(fused, key=fused.get, reverse=True)[:40]
        scores = {c: fused[c] for c in candidates}
        if rerank and candidates:
            t1 = time.perf_counter()
            pairs = [(query, self.docs[c]["text"][:1500]) for c in candidates]
            for c, s in zip(candidates, _reranker().predict(pairs)):
                scores[c] = float(s)
            candidates.sort(key=scores.get, reverse=True)
            timings["rerank_ms"] = (time.perf_counter() - t1) * 1000

        final = self._apply_quota(candidates, types, k)
        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        return [self._result(c, scores[c]) for c in final], timings

    def _apply_quota(self, ranked, types, k):
        """Make sure history evidence isn't crowded out by code chunks in the full condition."""
        if not (types & HISTORY_TYPES) or not (types - HISTORY_TYPES):
            return ranked[:k]
        need = int(k * HISTORY_QUOTA)
        hist = [c for c in ranked if node_type(c) in HISTORY_TYPES][:need]
        rest = [c for c in ranked if c not in hist][: k - len(hist)]
        return sorted(hist + rest, key=ranked.index)

    def _result(self, doc_id, score):
        d = self.docs[doc_id]
        return {"id": doc_id, "type": d["type"], "title": d.get("title", ""), "date": d.get("date", ""),
                "url": d.get("url", ""), "score": round(float(score), 4), "text": d["text"]}

    def neighbours(self, doc_id):
        return [n for n in linked(self.graph, doc_id, HISTORY_TYPES | {"code"}, limit=6) if n in self.docs]


@lru_cache(maxsize=8)
def get_index(slug: str) -> RepoIndex:
    return RepoIndex(slug)


def list_indexed():
    out = []
    for d in sorted(INDEX_DIR.iterdir()) if INDEX_DIR.exists() else []:
        if (d / "stats.json").exists():
            out.append(json.loads((d / "stats.json").read_text(encoding="utf-8")))
    return out
