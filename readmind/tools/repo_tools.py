"""Repository-history tools (pure Python, deterministic, typed results).

Exposed to the agents ONLY through the ``readmind-repo`` MCP server. Every function
returns a ``ToolResponse``-shaped dict (validated with Pydantic before it leaves),
so the guardrail layer can type-check what comes back from a tool call.

Tools
-----
    search_commits(query, k, path)   BM25 over commit messages (+ optional path filter)
    get_commit(commit_id)            full message, author, date, files, linked issues/PRs
    file_history(path, k)            commits that touched a path (``git log -- path``)
    search_code(query, k)            current-snapshot code search (Code Analysis Agent)
    read_code(path, start, end)      numbered source lines (path-traversal safe)
    search_discussions(query, k)     issues / PRs / reviews (Issue/PR Agent) + linked commits
    get_linked(item_id)              link-graph neighbours of any evidence id
"""
from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from readmind.schemas import ToolHit, ToolResponse
from readmind.settings import get_settings
from readmind.tools.store import get_store

SERVER = "readmind-repo"
_LOCK = threading.Lock()
_CODE_AGENT = None
_ISSUE_AGENT = None
MAX_SNIPPET = 900


def _resp(tool: str, query: str = "", results: list[dict] | None = None, **kw) -> dict:
    hits = [ToolHit.model_validate(r) for r in (results or [])]
    return ToolResponse(tool=tool, server=SERVER, query=query, results=hits, **kw).model_dump()


def _err(tool: str, query: str, msg: str) -> dict:
    return ToolResponse(ok=False, tool=tool, server=SERVER, query=query, error=msg).model_dump()


def _store():
    s = get_settings()
    return get_store(s.repo_path, s.corpus_dir, s.max_commits, s.repo_github)


def _code_agent():
    global _CODE_AGENT
    with _LOCK:
        if _CODE_AGENT is None:
            from agents.code_analysis_agent import CodeAnalysisAgent
            s = get_settings()
            _CODE_AGENT = CodeAnalysisAgent(s.repo_slug, repo_path=s.repo_path if s.repo_path.exists() else None,
                                            k=s.tool_result_limit, github=s.repo_github)
        return _CODE_AGENT


def _issue_agent():
    global _ISSUE_AGENT
    with _LOCK:
        if _ISSUE_AGENT is None:
            from agents.issue_pr_agent import IssuePRAgent
            _ISSUE_AGENT = IssuePRAgent(get_settings().repo_slug, store=_store(), k=get_settings().tool_result_limit)
        return _ISSUE_AGENT


def _clip(text: str, n: int = MAX_SNIPPET) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …"


def _k(k: Any, default: int = 6) -> int:
    try:
        return max(1, min(10, int(k)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
def search_commits(query: str, k: int = 6, path: str = "") -> dict:
    tool = "search_commits"
    if not query or not query.strip():
        return _err(tool, query, "empty query")
    st = _store()
    hits = st.search(query, types={"commit"}, k=_k(k) * (4 if path else 1))
    if path:
        p = path.strip().lstrip("./")
        hits = [h for h in hits if any(p in f for f in h.get("files", []))]
    out = []
    for h in hits[:_k(k)]:
        d = st.docs[h["id"]]
        out.append({"id": h["id"], "type": "commit", "title": d.get("title", ""),
                    "snippet": _clip(f"{d.get('message', '')}\nAuthor: {d.get('author', '')} | Date: {d.get('date', '')}"),
                    "url": d.get("url", ""), "date": d.get("date", ""), "score": h["score"],
                    "files": d.get("files", [])[:12], "links": [n for n, _ in st.linked(h["id"])][:6]})
    warn = [] if st.stats.get("commits") else ["no commit history loaded (clone the repo with history)"]
    return _resp(tool, query, out, warnings=warn)


def get_commit(commit_id: str) -> dict:
    tool = "get_commit"
    st = _store()
    ref = commit_id.strip()
    if not ref.lower().startswith("commit:"):
        ref = f"commit:{ref}"
    rid = st.resolve_id(ref.lower())
    if not rid or rid not in st.docs:
        return _err(tool, commit_id, f"unknown commit {commit_id}")
    d = st.docs[rid]
    links = [f"{n} ({rel})" for n, rel in st.linked(rid)]
    snippet = _clip(f"{d.get('message', '')}\n\nAuthor: {d.get('author', '')}\nDate: {d.get('date', '')}\n"
                    f"Files: {', '.join(d.get('files', [])[:30])}\nLinked: {', '.join(links[:10]) or 'none'}", 2500)
    return _resp(tool, commit_id, [{"id": rid, "type": "commit", "title": d.get("title", ""), "snippet": snippet,
                                    "url": d.get("url", ""), "date": d.get("date", ""), "score": 1.0,
                                    "files": d.get("files", [])[:30], "links": [n for n, _ in st.linked(rid)][:10]}])


def file_history(path: str, k: int = 8) -> dict:
    tool = "file_history"
    s = get_settings()
    p = (path or "").strip().lstrip("./")
    if not p or ".." in p.split("/"):
        return _err(tool, path, "invalid path")
    if not (s.repo_path / ".git").exists():
        return _err(tool, path, "no local git history available")
    try:
        out = subprocess.run(["git", "-C", str(s.repo_path), "log", "--diff-merges=off", f"-n{_k(k, 8)}",
                              "--format=%H", "--", p], capture_output=True, text=True, timeout=60).stdout
    except Exception as e:  # pragma: no cover - environment specific
        return _err(tool, path, f"git error: {e}")
    st = _store()
    res = []
    for sha in out.split():
        rid = st.resolve_id(f"commit:{sha[:10]}")
        d = st.docs.get(rid or "", {})
        res.append({"id": rid or f"commit:{sha[:10]}", "type": "commit", "title": d.get("title", ""),
                    "snippet": _clip(d.get("message", ""), 400), "url": d.get("url", ""), "date": d.get("date", ""),
                    "files": [p]})
    if not res:
        return _resp(tool, path, [], warnings=[f"no commits touch {p} in the current history (renamed or removed?)"])
    return _resp(tool, path, res)


def search_code(query: str, k: int = 5) -> dict:
    tool = "search_code"
    if not query or not query.strip():
        return _err(tool, query, "empty query")
    frags = _code_agent().run(query)
    out = [{"id": f.id, "type": "code", "title": f.title, "snippet": _clip(f.content, 1400), "url": f.url,
            "date": f.date, "score": f.confidence, "files": f.files} for f in frags[:_k(k, 5)]]
    return _resp(tool, query, out)


def read_code(path: str, start_line: int = 1, end_line: int = 80) -> dict:
    tool = "read_code"
    s = get_settings()
    p = (path or "").replace("code:", "").strip().lstrip("./")
    root = s.repo_path.resolve()
    target = (root / p).resolve()
    if not p or root not in target.parents or not target.is_file():
        return _err(tool, path, "file not found in repository snapshot")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    a = max(1, int(start_line or 1))
    b = min(len(lines), max(a, int(end_line or a + 79)), a + 199)
    body = "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(a, b + 1))
    return _resp(tool, path, [{"id": f"code:{p}", "type": "code", "title": f"{p} lines {a}-{b}", "snippet": body,
                               "url": f"https://github.com/{s.repo_github}/blob/HEAD/{p}#L{a}-L{b}", "files": [p]}])


def search_discussions(query: str, k: int = 6) -> dict:
    tool = "search_discussions"
    if not query or not query.strip():
        return _err(tool, query, "empty query")
    st = _store()
    frags = _issue_agent().run(query)
    out = []
    for f in frags[:_k(k)]:
        links = list(f.metadata.get("triggered_changes", []) or []) + list(f.metadata.get("related_discussions", []) or [])
        out.append({"id": f.id, "type": f.evidence_type, "title": f.title, "snippet": _clip(f.content), "url": f.url,
                    "date": f.date, "score": f.confidence, "files": f.files[:10], "links": links[:8]})
    warn = []
    if not (st.stats.get("issues") or st.stats.get("prs")):
        warn.append("no issue/PR corpus loaded — run ingestion.issue_pr_extractor")
    return _resp(tool, query, out, warnings=warn)


def get_linked(item_id: str) -> dict:
    tool = "get_linked"
    st = _store()
    rid = st.resolve_id(item_id) or item_id.strip()
    out = []
    for nb, rel in st.linked(rid)[:12]:
        d = st.docs.get(nb, {})
        out.append({"id": nb, "type": nb.split(":", 1)[0], "title": d.get("title", ""), "snippet": f"relation: {rel}",
                    "url": d.get("url", ""), "date": d.get("date", "")})
    return _resp(tool, item_id, out, warnings=[] if out else [f"no links recorded for {item_id}"])


REPO_TOOLS = {
    "search_commits": search_commits, "get_commit": get_commit, "file_history": file_history,
    "search_code": search_code, "read_code": read_code, "search_discussions": search_discussions,
    "get_linked": get_linked,
}

_ID = re.compile(r"\b(commit|pr|issue|review|code):[A-Za-z0-9_\-./]+")
