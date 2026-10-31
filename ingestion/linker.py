"""Commit <-> issue / PR linking.

Turns free-text references found in commit messages, PR bodies, issue bodies
and review comments into typed edges of a history graph.  The Issue/PR Agent
follows these edges to connect a discussion to the change it triggered.

Edge relations
--------------
    closes        PR / commit  -> issue   ("fixes #12", "closes #12", "resolves owner/repo#12")
    references    any          -> issue/pr ("see #12", "#12", "GH-12", "SELFOUR-501")
    merged_as     pr           -> commit  (PR merge_commit_sha)
    contains      pr           -> commit  (commits listed on the PR)
    merges        commit       -> pr      ("Merge pull request #420", squash suffix "(#420)")
    reviews       review       -> pr
    discussed_in  issue/commit -> pr      (reverse of closes / merges, added for traversal)
    part_of       commit       -> pr      (reverse of merged_as / contains)
    reviewed_by   pr           -> review  (reverse of reviews)
    referenced_by issue/pr     -> any     (reverse of references; lets external tracker keys
                                           such as SELFOUR-501 resolve to the PRs that cite them)

Evidence-ID conventions match the History/RAG Agent:
    commit:<sha[:SHA_LEN]>   issue:<n> | issue:<JIRA-KEY>   pr:<n>   review:<pr>-<review_id>
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Iterable

SHA_LEN = 10   # same short-SHA length the History/RAG Agent uses (commit:1a2b3c4d5e)

_CLOSING = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+"
    r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<num>\d+)",
    re.IGNORECASE,
)
_HASH_REF = re.compile(r"(?<![\w/&])(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<num>\d{1,6})\b")
_GH_REF = re.compile(r"\bGH-(?P<num>\d+)\b")
_MERGE_PR = re.compile(r"\bMerge pull request #(?P<num>\d+)\b", re.IGNORECASE)
_SQUASH_PR = re.compile(r"\(#(?P<num>\d+)\)\s*$", re.MULTILINE)
_PR_WORD = re.compile(r"\b(?:PR|pull request)\s*[:#]?\s*(?P<num>\d+)\b", re.IGNORECASE)
# Jira-style tracker keys (seL4 uses SELFOUR-123, VER-123, ...)
_JIRA = re.compile(r"\b(?P<key>[A-Z][A-Z0-9]{1,15}-\d{1,6})\b")
_JIRA_DENY = {"UTF-8", "SHA-1", "SHA-256", "ISO-8859", "X86-64", "AARCH-64", "RISCV-64", "CVE"}


@dataclass(frozen=True)
class Link:
    src: str
    dst: str
    rel: str

    def to_dict(self) -> dict:
        return asdict(self)


def commit_id(sha: str) -> str:
    return f"commit:{sha[:SHA_LEN].lower()}"


def extract_refs(text: str, own_repo: str | None = None) -> list[tuple[str, str]]:
    """Return ``[(target_id, relation), ...]`` for references found in *text*.

    ``#n`` refers to an issue *or* a PR on GitHub (they share a number space);
    the caller resolves the kind with :func:`resolve_number`.  Here such refs are
    emitted as ``num:<n>``.  Cross-repo refs to a different repository are dropped.
    """
    if not text:
        return []
    out: dict[str, str] = {}

    def own(repo: str | None) -> bool:
        return not repo or not own_repo or repo.lower() == own_repo.lower()

    for m in _MERGE_PR.finditer(text):
        out[f"pr:{m['num']}"] = "merges"
    for m in _SQUASH_PR.finditer(text):
        out.setdefault(f"pr:{m['num']}", "merges")
    for m in _CLOSING.finditer(text):
        if own(m["repo"]):
            out[f"num:{m['num']}"] = "closes"
    for m in _PR_WORD.finditer(text):
        out.setdefault(f"pr:{m['num']}", "references")
    for m in _HASH_REF.finditer(text):
        if own(m["repo"]):
            out.setdefault(f"num:{m['num']}", "references")
    for m in _GH_REF.finditer(text):
        out.setdefault(f"num:{m['num']}", "references")
    for m in _JIRA.finditer(text):
        key = m["key"]
        if key.upper() in _JIRA_DENY or key.split("-")[0] in _JIRA_DENY:
            continue
        out.setdefault(f"issue:{key}", "references")
    # A merged/squashed PR number is not also an issue reference.
    for k in [k for k in out if k.startswith("pr:")]:
        out.pop(f"num:{k[3:]}", None)
    return list(out.items())


def resolve_number(ref: str, pr_numbers: set[int], issue_numbers: set[int]) -> str:
    """Map ``num:<n>`` to ``pr:<n>`` or ``issue:<n>``; other refs pass through."""
    if not ref.startswith("num:"):
        return ref
    n = int(ref[4:])
    if n in pr_numbers:
        return f"pr:{n}"
    return f"issue:{n}"


def build_links(
    commits: Iterable[dict],
    issues: Iterable[dict],
    prs: Iterable[dict],
    reviews: Iterable[dict],
    own_repo: str | None = None,
) -> list[Link]:
    """Build the link graph from normalised corpus documents.

    Each doc is a dict produced by ``issue_pr_extractor`` (or a commit dict with
    ``sha`` and ``message``).  Returns de-duplicated :class:`Link` objects,
    including reverse ``discussed_in`` edges for traversal.
    """
    commits, issues, prs, reviews = list(commits), list(issues), list(prs), list(reviews)
    pr_numbers = {int(p["number"]) for p in prs if str(p.get("number", "")).isdigit()}
    issue_numbers = {int(i["number"]) for i in issues if str(i.get("number", "")).isdigit()}
    links: set[Link] = set()

    def add(src: str, dst: str, rel: str) -> None:
        if src != dst:
            links.add(Link(src, dst, rel))

    for c in commits:
        cid = commit_id(c["sha"])
        for ref, rel in extract_refs(c.get("message", ""), own_repo):
            add(cid, resolve_number(ref, pr_numbers, issue_numbers), rel)

    for p in prs:
        pid = p["id"]
        for ref, rel in extract_refs(f"{p.get('title', '')}\n{p.get('body', '')}", own_repo):
            add(pid, resolve_number(ref, pr_numbers, issue_numbers), rel)
        if p.get("merge_commit_sha"):
            add(pid, commit_id(p["merge_commit_sha"]), "merged_as")
        for sha in p.get("commit_shas", []):
            add(pid, commit_id(sha), "contains")

    for i in issues:
        for ref, rel in extract_refs(i.get("body", ""), own_repo):
            target = resolve_number(ref, pr_numbers, issue_numbers)
            add(i["id"], target, "references")

    for r in reviews:
        add(r["id"], f"pr:{r['pr_number']}", "reviews")

    # Reverse edges so traversal works from either end.
    reverse = set()
    for l in links:
        if l.rel in ("closes", "merges"):
            reverse.add(Link(l.dst, l.src, "discussed_in"))
        elif l.rel in ("merged_as", "contains"):
            reverse.add(Link(l.dst, l.src, "part_of"))
        elif l.rel == "reviews":
            reverse.add(Link(l.dst, l.src, "reviewed_by"))
        elif l.rel == "references":
            reverse.add(Link(l.dst, l.src, "referenced_by"))
    links |= reverse
    return sorted(links, key=lambda l: (l.src, l.rel, l.dst))


def adjacency(links: Iterable[Link | dict]) -> dict[str, list[tuple[str, str]]]:
    """``{src: [(dst, rel), ...]}`` adjacency map."""
    adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for l in links:
        src, dst, rel = (l.src, l.dst, l.rel) if isinstance(l, Link) else (l["src"], l["dst"], l["rel"])
        adj[src].append((dst, rel))
    return dict(adj)
