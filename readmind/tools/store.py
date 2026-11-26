"""Evidence store behind the repository-history MCP server.

* Commits come straight from the local git clone (``git log``), cached as
  ``<corpus_dir>/commits.jsonl`` so a restart does not re-scan the repository.
* Issues / PRs / reviews come from ``ingestion.issue_pr_extractor`` output in the
  same corpus directory (optional — the store works with commits only).
* Everything is indexed in one :class:`ingestion.corpus_store.IssuePRStore`
  (BM25 + link graph), so a commit message that says "Fixes #88" links the
  commit to issue 88 and the PR that merged it.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from ingestion.corpus_store import IssuePRStore
from ingestion.linker import build_links, commit_id

SEP, END = "\x1f", "\x1e"
_LOCK = threading.Lock()
_STORES: dict[str, IssuePRStore] = {}


def git(repo: Path, *args: str, timeout: int = 120) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def read_commits(repo: Path, max_commits: int = 20000, github: str = "") -> list[dict]:
    """Commit documents from ``git log`` (merge diffs off: fast on a blobless clone).

    Each record starts with END and its fields are SEP-separated; git appends the
    ``--name-only`` file list after the last SEP, so the tail of a record is its files.
    """
    fmt = END + SEP.join(["%H", "%an", "%aI", "%P", "%B"]) + SEP
    out = git(repo, "log", "--diff-merges=off", f"-n{max_commits}", f"--format={fmt}", "--name-only", "HEAD",
              timeout=600)
    docs = []
    for rec in out.split(END):
        if SEP not in rec:
            continue
        parts = rec.split(SEP)
        if len(parts) < 6:
            continue
        sha, author, date, parents = parts[0].strip(), parts[1], parts[2], parts[3].split()
        msg = SEP.join(parts[4:-1]).strip()
        files = [l.strip() for l in parts[-1].splitlines() if l.strip()][:60]
        subject = msg.splitlines()[0] if msg else ""
        text = msg + ("\n\nFiles: " + ", ".join(files[:40]) if files else "")
        docs.append({
            "id": commit_id(sha), "type": "commit", "sha": sha, "title": subject,
            "message": msg, "text": text, "author": author, "date": date,
            "files": files, "merge": len(parents) > 1,
            "url": f"https://github.com/{github}/commit/{sha}" if github else "",
        })
    return docs


def load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def ensure_commit_cache(repo: Path, corpus_dir: Path, max_commits: int, github: str) -> Path:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    cache = corpus_dir / "commits.jsonl"
    head = ""
    try:
        head = git(repo, "rev-parse", "HEAD").strip()
    except Exception:
        pass
    meta = corpus_dir / "commits.meta.json"
    if cache.exists() and meta.exists():
        m = json.loads(meta.read_text())
        if m.get("head") == head and m.get("max_commits") == max_commits:
            return cache
    docs = read_commits(repo, max_commits, github) if (repo / ".git").exists() else []
    with cache.open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    meta.write_text(json.dumps({"head": head, "max_commits": max_commits, "count": len(docs)}))
    return cache


def get_store(repo_path: Path, corpus_dir: Path, max_commits: int = 20000, github: str = "seL4/seL4") -> IssuePRStore:
    key = f"{repo_path}|{corpus_dir}|{max_commits}"
    with _LOCK:
        if key in _STORES:
            return _STORES[key]
        commits: list[dict] = []
        if (Path(repo_path) / ".git").exists():
            commits = load_jsonl(ensure_commit_cache(Path(repo_path), Path(corpus_dir), max_commits, github))
        else:
            commits = load_jsonl(Path(corpus_dir) / "commits.jsonl")
        issues = load_jsonl(Path(corpus_dir) / "issues.jsonl")
        prs = load_jsonl(Path(corpus_dir) / "prs.jsonl")
        reviews = load_jsonl(Path(corpus_dir) / "reviews.jsonl")
        links = load_jsonl(Path(corpus_dir) / "links.jsonl")
        if not links:
            links = [l.to_dict() for l in build_links(
                [{"sha": c["sha"], "message": c.get("message", "")} for c in commits], issues, prs, reviews,
                own_repo=github)]
        store = IssuePRStore(commits + issues + prs + reviews, links, slug=Path(corpus_dir).name, github=github)
        store.stats.update({"commits": len(commits), "issues": len(issues), "prs": len(prs), "reviews": len(reviews)})
        _STORES[key] = store
        return store


def clear_cache() -> None:
    with _LOCK:
        _STORES.clear()
