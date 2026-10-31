"""Issue / PR Agent — discussion-level evidence linked to the changes it triggered.

Searches GitHub issues, pull requests and review comments, then follows the
commit <-> issue / PR link graph so every discussion it reports carries the
commits it led to.  Like the Code Analysis Agent it is tool-driven and
deterministic (no LLM call).

Tools
-----
    lookup(ref)              direct fetch of explicitly referenced items
                             ("PR #420", "#88", "SELFOUR-501", "pull request 77")
    search_prs(query)        BM25 over PR titles, bodies, discussion
    search_issues(query)     BM25 over issue titles, bodies, comments
    search_reviews(query)    BM25 over review summaries + inline review comments
    link_to_changes(id)      graph walk: discussion -> PR -> commits (two hops max)

Results of the three searches are fused with Reciprocal Rank Fusion; explicit
references always rank first.

Evidence source (first available wins)
--------------------------------------
    1. ``store=`` an :class:`ingestion.corpus_store.IssuePRStore`
    2. ``data/corpus/<slug>/`` written by ``ingestion.issue_pr_extractor``
    3. ``index=`` / the shared ``rag.retriever`` index (types issue, pr, review)

Interface — compatible with every architecture variant::

    agent.run("Who approved PR #420?", repo_slug="seL4__seL4") -> list[EvidenceFragment]
    agent.run(blackboard)
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from agents.blackboard import Blackboard, EvidenceFragment
from ingestion.corpus_store import IssuePRStore, tokenize
from ingestion.linker import extract_refs

AGENT_NAME = "issue_pr"
RRF_K = 60
CHANGE_RELS = {"merged_as", "contains", "discussed_in", "merges", "closes", "part_of"}
TEMPLATE_WORDS = {
    "find", "pr", "prs", "debates", "issue", "issues", "discussions", "discussion",
    "architectural", "reviews", "review", "pull", "request", "requests", "author", "reviewers",
    "approvers", "retrieve", "design", "search", "targeted", "addressing", "gap",
}


def _strip_template(query: str) -> str:
    if " for: " in query:
        return query.split(" for: ", 1)[1]
    if query.lower().startswith("targeted search addressing gap:"):
        return query.split(":", 1)[1]
    return query


class _IndexAdapter:
    """Wrap the shared RepoIndex so it looks like an IssuePRStore."""

    def __init__(self, index: Any):
        self.index = index
        self.docs = index.docs

    def search(self, query, types=None, k=6):
        hits, _ = self.index.retrieve(query, types=types, k=k, expand=False, rerank=False)
        return hits

    def resolve_id(self, ref):
        fn = getattr(self.index, "resolve_id", None)
        try:
            rid = fn(ref) if fn else None
        except Exception:
            rid = None
        return rid if rid in self.docs else (ref if ref in self.docs else None)

    def get(self, ref):
        rid = self.resolve_id(ref)
        return self.docs.get(rid) if rid else None

    def linked(self, doc_id):
        try:
            return [(nb, "linked") for nb in self.index.neighbours(doc_id)]
        except Exception:
            return []


class IssuePRAgent:
    agent_name = AGENT_NAME

    def __init__(self, repo_slug: str = "seL4__seL4", store: IssuePRStore | None = None,
                 index: Any = None, corpus_dir: str | os.PathLike | None = None,
                 k: int = 6, max_linked_commits: int = 4):
        self.repo_slug = repo_slug
        self.k = k
        self.max_linked_commits = max_linked_commits
        self.last_trace: list[dict[str, Any]] = []
        self.source_name = "none"
        self.source = self._resolve_source(store, index, corpus_dir)

    # ------------------------------------------------------------------ #
    def _resolve_source(self, store, index, corpus_dir):
        if store is not None:
            self.source_name = "store"
            return store
        for c in (corpus_dir, os.environ.get("ISSUE_PR_CORPUS"), f"data/corpus/{self.repo_slug}"):
            if c and any((Path(c) / n).exists() for n in ("prs.jsonl", "issues.jsonl")):
                self.source_name = f"corpus:{c}"
                return IssuePRStore.load(c)
        if index is None:
            try:
                from rag.retriever import get_index
                index = get_index(self.repo_slug)
            except Exception:
                index = None
        if index is not None and isinstance(getattr(index, "docs", None), dict):
            self.source_name = "rag_index"
            return _IndexAdapter(index)
        return None

    def _tool(self, name: str, arg: Any, fn, bb: Blackboard | None):
        t0 = time.perf_counter()
        try:
            result = fn()
            err = ""
        except Exception as e:      # a failing tool must not kill the agent
            result, err = [], f"{type(e).__name__}: {e}"
        ms = (time.perf_counter() - t0) * 1000
        n = len(result) if hasattr(result, "__len__") else 0
        self.last_trace.append({"tool": name, "arg": arg, "results": n, "ms": round(ms, 2),
                                **({"error": err} if err else {})})
        if bb is not None:
            bb.log(self.agent_name, f"tool_{name}", f"{arg!r} -> {n} results{' ERROR ' + err if err else ''}",
                   duration_ms=ms)
        return result

    # ------------------------------------------------------------------ #
    #  Tools                                                              #
    # ------------------------------------------------------------------ #
    def explicit_refs(self, text: str) -> list[str]:
        refs = []
        for ref, _rel in extract_refs(text):
            if ref.startswith("num:"):
                refs += [f"pr:{ref[4:]}", f"issue:{ref[4:]}"]
            else:
                refs.append(ref)
        return list(dict.fromkeys(refs))

    def lookup(self, refs: list[str]) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for r in refs:
            rid = self.source.resolve_id(r)
            targets = [rid] if rid else []
            if not rid:
                # External tracker keys (e.g. Jira SELFOUR-501) have no document of their own;
                # resolve them to the PRs / issues that cite them.
                targets = [nb for nb, rel in self.source.linked(r)
                           if rel in ("referenced_by", "discussed_in") and nb in self.source.docs]
            for t in targets:
                d = self.source.docs[t]
                if t not in seen and d.get("type") in ("issue", "pr", "review"):
                    seen.add(t)
                    out.append({**d, "id": t, "score": 1.0, "via_ref": r})
        return out

    def link_to_changes(self, doc_id: str) -> dict[str, list]:
        """Two-hop walk: discussion -> (PR) -> commits.  Returns commits + related discussions."""
        commits: list[tuple[str, str]] = []
        related: list[tuple[str, str]] = []
        frontier = [(doc_id, "self")]
        seen = {doc_id}
        for hop in range(2):
            nxt = []
            for node, _ in frontier:
                for nb, rel in self.source.linked(node):
                    if nb in seen:
                        continue
                    seen.add(nb)
                    if nb.startswith("commit:"):
                        commits.append((nb, rel if hop == 0 else f"via {node}"))
                    elif nb.startswith(("pr:", "issue:")):
                        related.append((nb, rel))
                        if hop == 0 and (rel in CHANGE_RELS or rel in ("references", "linked", "reviews")):
                            nxt.append((nb, rel))
                    elif nb.startswith("review:"):
                        related.append((nb, rel))
            frontier = nxt
        return {"commits": commits, "related": related}

    # ------------------------------------------------------------------ #
    def _coverage(self, query_tokens: set[str], doc: dict) -> float:
        if not query_tokens:
            return 0.0
        dt = set(tokenize(f"{doc.get('title', '')} {doc.get('text', '')[:6000]}"))
        return len(query_tokens & dt) / len(query_tokens)

    def _fragment(self, hit: dict, confidence: float, links: dict, reason: str) -> EvidenceFragment:
        doc = self.source.docs.get(hit["id"], hit)
        commits = [c for c, _ in links["commits"]][: self.max_linked_commits * 3]
        link_lines = [f"{c} ({rel})" for c, rel in links["commits"][: self.max_linked_commits * 3]]
        rel_lines = [f"{r} ({rel})" for r, rel in links["related"][:8]]
        content = doc.get("text", hit.get("text", ""))
        if link_lines:
            content += "\n\nLinked changes: " + ", ".join(link_lines)
        if rel_lines:
            content += "\nRelated discussions: " + ", ".join(rel_lines)
        return EvidenceFragment(
            id=hit["id"],
            source_agent=self.agent_name,
            evidence_type=doc.get("type", hit.get("type", "pr")),
            title=doc.get("title", hit.get("title", "")),
            content=content,
            confidence=round(confidence, 4),
            url=doc.get("url", hit.get("url", "")),
            date=doc.get("merged_at") or doc.get("date", hit.get("date", "")),
            files=list(doc.get("files", []) or [])[:40],
            metadata={
                "author": doc.get("author", ""),
                "state": doc.get("state", ""),
                "merged": doc.get("merged"),
                "triggered_changes": commits,
                "related_discussions": [r for r, _ in links["related"][:8]],
                "match": reason,
            },
        )

    # ------------------------------------------------------------------ #
    def run(self, query: "str | Blackboard", repo_slug: str | None = None) -> list[EvidenceFragment]:
        bb = query if isinstance(query, Blackboard) else None
        text = bb.question if bb is not None else str(query)
        if bb is not None and bb.retry_queries:
            text = f"{text} {' '.join(bb.retry_queries)}"
        self.last_trace = []
        t0 = time.perf_counter()
        if bb is not None:
            bb.log(self.agent_name, "start", f"Issue/PR search for: {text[:200]} (source={self.source_name})")
        if self.source is None:
            if bb is not None:
                bb.log(self.agent_name, "no_source", "No issue/PR corpus or index available")
            return []

        q = _strip_template(text)
        qt = {t for t in tokenize(q) if t not in TEMPLATE_WORDS}
        search_q = " ".join(sorted(qt)) or q

        explicit = self._tool("lookup", self.explicit_refs(q), lambda: self.lookup(self.explicit_refs(q)), bb)
        lists = {
            "search_prs": self._tool("search_prs", search_q, lambda: self.source.search(search_q, {"pr"}, self.k), bb),
            "search_issues": self._tool("search_issues", search_q, lambda: self.source.search(search_q, {"issue"}, self.k), bb),
            "search_reviews": self._tool("search_reviews", search_q, lambda: self.source.search(search_q, {"review"}, self.k), bb),
        }

        fused: dict[str, float] = {}
        hits: dict[str, dict] = {}
        why: dict[str, str] = {}
        for name, res in lists.items():
            for rank, h in enumerate(res):
                fused[h["id"]] = fused.get(h["id"], 0.0) + 1.0 / (RRF_K + rank + 1) + 0.002 * float(h.get("score", 0))
                hits.setdefault(h["id"], h)
                why.setdefault(h["id"], name)
        ranked = sorted(fused, key=lambda i: -fused[i])
        best = fused[ranked[0]] if ranked else 1.0

        chosen: list[tuple[dict, float, str]] = [(h, 0.95, "explicit reference") for h in explicit]
        taken = {h["id"] for h in explicit}
        for did in ranked:
            if len(chosen) >= self.k:
                break
            if did in taken:
                continue
            doc = self.source.docs.get(did, hits[did])
            cov = self._coverage(qt, doc)
            if cov < 0.2:
                continue          # lexical match on one incidental word — not real evidence
            conf = 0.25 + 0.65 * (cov ** 0.7) * (fused[did] / best) ** 0.5
            chosen.append((hits[did], min(conf, 0.9), why[did]))
            taken.add(did)

        frags: list[EvidenceFragment] = []
        for h, conf, reason in chosen:
            links = self._tool("link_to_changes", h["id"], lambda h=h: self.link_to_changes(h["id"]), bb)
            if not isinstance(links, dict):
                links = {"commits": [], "related": []}
            frag = self._fragment(h, conf, links, reason)
            frags.append(frag)
            # Surface linked commits that the source actually holds (e.g. commits.jsonl / shared index).
            for cid, rel in links["commits"][: self.max_linked_commits]:
                cdoc = self.source.docs.get(cid)
                if cdoc and cid not in {f.id for f in frags}:
                    frags.append(EvidenceFragment(
                        id=cid, source_agent=self.agent_name, evidence_type="commit",
                        title=cdoc.get("title", cid), content=cdoc.get("text", ""),
                        confidence=round(conf * 0.8, 4), url=cdoc.get("url", ""),
                        date=cdoc.get("date", ""), files=list(cdoc.get("files", []) or []),
                        metadata={"linked_from": h["id"], "relation": rel}))

        ms = (time.perf_counter() - t0) * 1000
        if bb is not None:
            posted = bb.post_evidence(self.agent_name, frags)
            bb.log(self.agent_name, "complete", f"Posted {posted} issue/PR fragments in {ms:.0f}ms", duration_ms=ms)
        return frags
