"""Build the repository history graph:

    code chunk ─in_file─► file ◄─modifies─ commit ─fixes/mentions─► issue
                                             ▲                        ▲
                               pr ─contains──┘   pr ─fixes/mentions───┘
                               ▲
                  review ─review_of┘ (and review ─comments_on─► file)

Node ids are the same as the retrieval document ids (commit:<sha10>, issue:<n>, pr:<n>, ...),
so retrieval results can be expanded by walking the graph."""
import json
import re
from pathlib import Path

import networkx as nx

REF_RE = re.compile(
    r"(?:(?P<kw>close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+)?"
    r"(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+))?#(?P<num>\d{1,6})\b",
    re.IGNORECASE,
)
URL_REF_RE = re.compile(r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/(?:issues|pull)/(?P<num>\d+)")


def commit_id(sha: str) -> str:
    return f"commit:{sha[:10]}"


def parse_refs(text: str, owner: str, repo: str) -> list[tuple[int, str]]:
    """Return [(number, 'fixes'|'mentions')] for references to this repository."""
    refs = {}
    for m in REF_RE.finditer(text or ""):
        if m.group("owner") and (m.group("owner").lower(), m.group("repo").lower()) != (owner.lower(), repo.lower()):
            continue
        num = int(m.group("num"))
        kind = "fixes" if m.group("kw") else "mentions"
        if refs.get(num) != "fixes":
            refs[num] = kind
    for m in URL_REF_RE.finditer(text or ""):
        if (m.group("owner").lower(), m.group("repo").lower()) == (owner.lower(), repo.lower()):
            refs.setdefault(int(m.group("num")), "mentions")
    return list(refs.items())


def build_graph(commits, issues, prs, code_chunks, owner, repo) -> nx.DiGraph:
    G = nx.DiGraph()
    issue_nums = {i["number"] for i in issues}
    pr_nums = {p["number"] for p in prs}

    def resolve(num):
        if num in pr_nums:
            return f"pr:{num}"
        if num in issue_nums:
            return f"issue:{num}"
        return None

    def link(src, text):
        for num, kind in parse_refs(text, owner, repo):
            tgt = resolve(num)
            if tgt and tgt != src:
                G.add_edge(src, tgt, rel=kind)

    for i in issues:
        G.add_node(f"issue:{i['number']}", type="issue", date=i["created_at"])
    for p in prs:
        G.add_node(f"pr:{p['number']}", type="pr", date=p.get("merged_at") or p["created_at"])

    commit_ids = set()
    for c in commits:
        cid = commit_id(c["sha"])
        commit_ids.add(cid)
        G.add_node(cid, type="commit", date=c["date"])
        for f in c["files"]:
            G.add_node(f"file:{f['path']}", type="file")
            G.add_edge(cid, f"file:{f['path']}", rel="modifies")
        link(cid, c["message"])

    for p in prs:
        pid = f"pr:{p['number']}"
        for sha in p["commits"] + ([p["merge_commit_sha"]] if p.get("merge_commit_sha") else []):
            if commit_id(sha) in commit_ids:
                G.add_edge(pid, commit_id(sha), rel="contains")
        link(pid, " ".join([p["title"], p["body"]] + [d["body"] for d in p["discussion"]]))
        for path in p["files"]:
            G.add_node(f"file:{path}", type="file")
            G.add_edge(pid, f"file:{path}", rel="touches")
        for rc in p["review_comments"]:
            rid = f"review:{rc['id']}"
            G.add_node(rid, type="review", date=rc["date"])
            G.add_edge(rid, pid, rel="review_of")
            if rc.get("path"):
                G.add_node(f"file:{rc['path']}", type="file")
                G.add_edge(rid, f"file:{rc['path']}", rel="comments_on")
        for rv in p["reviews"]:
            rid = f"review:{rv['id']}"
            G.add_node(rid, type="review", date=rv["date"])
            G.add_edge(rid, pid, rel="review_of")

    for i in issues:
        link(f"issue:{i['number']}", " ".join([i["body"]] + [c["body"] for c in i["comments"]]))

    for ch in code_chunks:
        G.add_node(ch["id"], type="code")
        G.add_node(f"file:{ch['path']}", type="file")
        G.add_edge(ch["id"], f"file:{ch['path']}", rel="in_file")
    return G


def save_graph(G: nx.DiGraph, path: Path):
    data = {"nodes": [{"id": n, **a} for n, a in G.nodes(data=True)],
            "edges": [[u, v, a.get("rel", "")] for u, v, a in G.edges(data=True)]}
    path.write_text(json.dumps(data), encoding="utf-8")


def load_graph(path: Path) -> nx.DiGraph:
    data = json.loads(path.read_text(encoding="utf-8"))
    G = nx.DiGraph()
    for n in data["nodes"]:
        nid = n.pop("id")
        G.add_node(nid, **n)
    for u, v, rel in data["edges"]:
        G.add_edge(u, v, rel=rel)
    return G


def node_type(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def linked(G: nx.DiGraph, node: str, types: set[str], limit: int = 5) -> list[str]:
    """History neighbours of a node (both directions), newest first. Code chunks are expanded
    through their file: code -> file -> commits/reviews that touched it -> their PRs/issues."""
    if node not in G:
        return []
    frontier = {node}
    if node_type(node) == "code":
        frontier = {n for n in G.successors(node) if node_type(n) == "file"}
    found = set()
    for f in frontier:
        for n in set(G.successors(f)) | set(G.predecessors(f)):
            if node_type(n) in types and n != node:
                found.add(n)
    # one extra hop from commits to their PR / issue (the "why")
    if node_type(node) in ("code", "commit"):
        commits = sorted([n for n in found if node_type(n) == "commit"],
                         key=lambda n: G.nodes[n].get("date", ""), reverse=True)[:limit]
        if node_type(node) == "commit":
            commits.append(node)
        for c in commits:
            for n in set(G.successors(c)) | set(G.predecessors(c)):
                if node_type(n) in {"pr", "issue"} & types:
                    found.add(n)
    found.discard(node)
    ranked = sorted(found, key=lambda n: G.nodes[n].get("date", "") or "", reverse=True)
    return ranked[: limit * 2]
