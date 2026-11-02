"""Turn extracted artefacts into retrieval documents and index them (Chroma vectors + graph).
BM25 is rebuilt from docs.jsonl at load time, so it needs no separate index."""
import json
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from tqdm import tqdm

from config import INDEX_DIR
from ingestion.link_graph import commit_id, save_graph
from rag.embeddings import embed_documents

HISTORY_TYPES = {"commit", "issue", "pr", "review"}


def _gh(owner, repo):
    return f"https://github.com/{owner}/{repo}"


def commit_doc(c, owner, repo):
    paths = [f["path"] for f in c["files"]]
    text = (f"Commit {c['sha'][:10]} by {c['author']} on {c['date'][:10]}\n\n{c['message']}\n\n"
            f"Files changed: {', '.join(paths[:20])}\n\nDiff excerpt:\n{c['diff']}")
    return {"id": commit_id(c["sha"]), "type": "commit", "title": c["message"].splitlines()[0][:140],
            "date": c["date"], "files": paths, "url": f"{_gh(owner, repo)}/commit/{c['sha']}", "text": text}


def issue_doc(i, owner, repo):
    comments = "\n".join(f"- {c['author']} ({(c['date'] or '')[:10]}): {c['body']}" for c in i["comments"])
    text = (f"Issue #{i['number']}: {i['title']} [{i['state']}]\nLabels: {', '.join(i['labels']) or 'none'}\n"
            f"Opened {i['created_at'][:10]} by {i['author']}\n\n{i['body']}\n\nComments:\n{comments}")
    return {"id": f"issue:{i['number']}", "type": "issue", "title": i["title"], "date": i["created_at"],
            "files": [], "url": i["url"], "text": text[:9000]}


def pr_docs(p, owner, repo):
    status = "merged" if p.get("merged_at") else p["state"]
    discussion = "\n".join(f"- {d['author']}: {d['body']}" for d in p["discussion"])
    text = (f"Pull request #{p['number']}: {p['title']} [{status}]\nOpened {p['created_at'][:10]} by {p['author']}\n\n"
            f"{p['body']}\n\nFiles: {', '.join(p['files'][:30])}\n\nDiscussion:\n{discussion}")
    docs = [{"id": f"pr:{p['number']}", "type": "pr", "title": p["title"],
             "date": p.get("merged_at") or p["created_at"], "files": p["files"], "url": p["url"],
             "text": text[:9000]}]
    for rc in p["review_comments"]:
        docs.append({
            "id": f"review:{rc['id']}", "type": "review",
            "title": f"Review on PR #{p['number']} · {rc.get('path') or ''}", "date": rc["date"],
            "files": [rc["path"]] if rc.get("path") else [], "url": rc.get("url") or p["url"],
            "text": (f"Review comment on PR #{p['number']} ({p['title']}) by {rc['author']}, "
                     f"file {rc.get('path')} line {rc.get('line')}:\n{rc['body']}\n\n"
                     f"Code under review:\n{rc.get('diff_hunk') or ''}")})
    for rv in p["reviews"]:
        docs.append({
            "id": f"review:{rv['id']}", "type": "review", "title": f"Review ({rv['state']}) on PR #{p['number']}",
            "date": rv["date"], "files": [], "url": rv.get("url") or p["url"],
            "text": f"Review ({rv['state']}) of PR #{p['number']} ({p['title']}) by {rv['author']}:\n{rv['body']}"})
    return docs


def code_doc(ch, owner, repo):
    return {**ch, "title": f"{ch['path']} · {ch['symbol']}", "date": "", "files": [ch["path"]],
            "url": f"{_gh(owner, repo)}/blob/HEAD/{ch['path']}#L{ch['start']}-L{ch['end']}"}


def doc_doc(ch, owner, repo):
    return {**ch, "date": "", "files": [ch["path"]], "url": f"{_gh(owner, repo)}/blob/HEAD/{ch['path']}"}


def build_documents(commits, issues, prs, code_chunks, doc_chunks, owner, repo):
    docs = []
    docs += [code_doc(c, owner, repo) for c in code_chunks]
    docs += [doc_doc(c, owner, repo) for c in doc_chunks]
    docs += [commit_doc(c, owner, repo) for c in commits]
    docs += [issue_doc(i, owner, repo) for i in issues]
    for p in prs:
        docs += pr_docs(p, owner, repo)
    seen, unique = set(), []
    for d in docs:
        if d["id"] not in seen:
            seen.add(d["id"])
            unique.append(d)
    return unique


def build_index(slug, owner, repo, docs, graph, batch_size=64) -> dict:
    out = INDEX_DIR / slug
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "docs.jsonl", "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    save_graph(graph, out / "graph.json")

    client = chromadb.PersistentClient(path=str(out / "chroma"))
    try:
        client.delete_collection("docs")
    except Exception:
        pass
    col = client.create_collection("docs", metadata={"hnsw:space": "cosine"})
    for i in tqdm(range(0, len(docs), batch_size), desc="embedding"):
        batch = docs[i:i + batch_size]
        col.add(ids=[d["id"] for d in batch],
                embeddings=embed_documents([d["text"] for d in batch]),
                metadatas=[{"type": d["type"], "date": d.get("date") or ""} for d in batch])

    counts = {}
    for d in docs:
        counts[d["type"]] = counts.get(d["type"], 0) + 1
    stats = {"slug": slug, "github": f"{owner}/{repo}", "counts": counts, "total": len(docs),
             "graph_nodes": graph.number_of_nodes(), "graph_edges": graph.number_of_edges(),
             "indexed_at": datetime.now(timezone.utc).isoformat()}
    (out / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats
