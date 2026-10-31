"""In-memory store over the issue / PR / review corpus.

Provides what the Issue/PR Agent needs without depending on the shared
vector index: BM25 lexical search restricted by document type, direct
lookup by ID (``pr:420``, ``issue:SELFOUR-501``), and link-graph traversal.

It also exposes a small ``RepoIndex``-compatible surface
(``docs``, ``retrieve``, ``neighbours``, ``resolve_id``, ``stats``) so the
same corpus can be plugged into code that expects the shared retriever.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from ingestion.linker import adjacency

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "is", "was",
    "were", "be", "been", "are", "it", "its", "this", "that", "these", "those", "as", "at",
    "from", "into", "what", "why", "who", "when", "where", "which", "how", "did", "does", "do",
    "has", "have", "had", "not", "no", "can", "could", "would", "should", "will", "there",
    "their", "they", "them", "we", "our", "you", "your", "i", "me", "my", "but", "if", "then",
    "so", "than", "also", "about", "under", "over", "after", "before", "via", "any", "all",
    "find", "explain", "describe", "please", "analyze", "search", "retrieve", "check",
}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    """Lower-cased tokens; snake_case and camelCase identifiers are also split."""
    out: list[str] = []
    for tok in _TOKEN.findall(text or ""):
        low = tok.lower()
        parts = {low}
        if "_" in tok or _CAMEL.search(tok):
            for seg in re.split(r"_+", tok):
                if len(seg) > 1:
                    parts.add(seg.lower())          # seL4_TCB_SetPriority -> sel4, tcb, setpriority
                for p in re.split(r"_+", _CAMEL.sub("_", seg)):
                    if len(p) > 2:
                        parts.add(p.lower())        # ... -> set, priority
        for p in parts:
            if keep_stopwords or (p not in STOPWORDS and len(p) > 1):
                out.append(p)
    return out


class BM25:
    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(t) for t in docs_tokens]
        self.len = [len(t) for t in docs_tokens]
        self.avgdl = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter = Counter()
        for t in docs_tokens:
            df.update(set(t))
        n = len(docs_tokens)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def score(self, query_tokens: list[str], i: int) -> float:
        tf, dl = self.tf[i], self.len[i]
        s = 0.0
        for q in query_tokens:
            f = tf.get(q)
            if not f:
                continue
            idf = self.idf.get(q, 0.0)
            s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
        return s


class IssuePRStore:
    """Searchable view of ``data/corpus/<slug>/``."""

    def __init__(self, docs: Iterable[dict], links: Iterable[dict] = (), slug: str = "", github: str = ""):
        self.slug = slug
        self.docs: dict[str, dict] = {}
        for d in docs:
            self.docs[d["id"]] = d
        self._ids = list(self.docs)
        # Title is repeated so it is weighted above long discussion threads.
        self._bm25 = BM25([tokenize(f"{self.docs[i].get('title', '')} " * 3 + self.docs[i].get("text", ""))
                           for i in self._ids])
        self.links = [l if isinstance(l, dict) else l.to_dict() for l in links]
        self._adj = adjacency(self.links)
        self.stats = {"github": github or slug.replace("__", "/"), "docs": len(self.docs),
                      "links": len(self.links)}

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, corpus_dir: str | Path, extra_docs: Iterable[dict] = ()) -> "IssuePRStore":
        corpus_dir = Path(corpus_dir)
        docs: list[dict] = list(extra_docs)
        for name in ("issues.jsonl", "prs.jsonl", "reviews.jsonl", "commits.jsonl"):
            p = corpus_dir / name
            if p.exists():
                docs.extend(json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip())
        links = []
        lp = corpus_dir / "links.jsonl"
        if lp.exists():
            links = [json.loads(l) for l in lp.read_text(encoding="utf-8").splitlines() if l.strip()]
        github = ""
        mp = corpus_dir / "manifest.json"
        if mp.exists():
            github = json.loads(mp.read_text(encoding="utf-8")).get("repo", "")
        return cls(docs, links, slug=corpus_dir.name, github=github)

    # ------------------------------------------------------------------ #
    def resolve_id(self, ref: str) -> str | None:
        """Resolve loose references: ``PR:420``, ``#420``, ``pr 420``, short SHAs, Jira keys."""
        if not ref:
            return None
        r = ref.strip()
        if r in self.docs:
            return r
        low = r.lower()
        if low in self.docs:
            return low
        m = re.fullmatch(r"(?:(pr|pull|issue|review|commit)\s*[:#]?\s*)?#?([\w.-]+)", r, re.IGNORECASE)
        if m:
            kind, key = (m.group(1) or "").lower(), m.group(2)
            kind = {"pull": "pr"}.get(kind, kind)
            candidates = [f"{kind}:{key}"] if kind else [f"pr:{key}", f"issue:{key}"]
            for c in candidates:
                for variant in (c, c.lower(), c.split(":")[0] + ":" + key.upper()):
                    if variant in self.docs:
                        return variant
        if low.startswith("commit:"):
            sha = low[7:]
            for did in self._ids:
                if did.startswith("commit:") and (did[7:].startswith(sha) or sha.startswith(did[7:])):
                    return did
        return None

    def get(self, doc_id: str) -> dict | None:
        rid = self.resolve_id(doc_id)
        return self.docs.get(rid) if rid else None

    def neighbours(self, doc_id: str) -> list[str]:
        return [dst for dst, _ in self._adj.get(doc_id, [])]

    def linked(self, doc_id: str) -> list[tuple[str, str]]:
        """``[(neighbour_id, relation), ...]`` — includes IDs not present in the store."""
        return list(self._adj.get(doc_id, []))

    def search(self, query: str, types: set[str] | None = None, k: int = 6) -> list[dict]:
        qt = tokenize(query)
        if not qt:
            return []
        scored = []
        for i, did in enumerate(self._ids):
            d = self.docs[did]
            if types and d.get("type") not in types:
                continue
            s = self._bm25.score(qt, i)
            if s > 0:
                scored.append((s, did))
        scored.sort(reverse=True)
        top = scored[:k]
        if not top:
            return []
        best = top[0][0]
        return [self._as_hit(did, s / best) for s, did in top]

    def _as_hit(self, did: str, score: float) -> dict[str, Any]:
        d = self.docs[did]
        return {
            "id": did, "type": d.get("type", ""), "title": d.get("title", ""),
            "text": d.get("text", ""), "date": d.get("date", ""), "url": d.get("url", ""),
            "files": d.get("files", []), "author": d.get("author", ""),
            "score": round(float(score), 4),
        }

    # RepoIndex-compatible surface ----------------------------------------
    def retrieve(self, query: str, types: set[str] | None = None, k: int = 6,
                 expand: bool = False, rerank: bool = False):
        hits = self.search(query, types=types, k=k)
        if expand:
            seen = {h["id"] for h in hits}
            for h in list(hits):
                for nb in self.neighbours(h["id"]):
                    if nb in self.docs and nb not in seen and (not types or self.docs[nb]["type"] in types):
                        seen.add(nb)
                        hits.append(self._as_hit(nb, h["score"] * 0.5))
        return hits, {}

    def by_type(self) -> dict[str, int]:
        c: dict[str, int] = defaultdict(int)
        for d in self.docs.values():
            c[d.get("type", "?")] += 1
        return dict(c)
