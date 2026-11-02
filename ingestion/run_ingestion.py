"""Ingestion CLI.

    python -m ingestion.run_ingestion --repo all
    python -m ingestion.run_ingestion --repo seL4__seL4 --refresh
    python -m ingestion.run_ingestion --repo all --skip-github      # git history + code only

Raw extraction is cached in data/raw/<slug>/ so GitHub is only queried once;
use --refresh to fetch again (e.g. from the scheduled CI job for incremental updates)."""
import argparse
import json
import time
from pathlib import Path

from config import GITHUB_TOKEN, RAW_DIR, REPOS_DIR, load_repos
from ingestion.build_index import build_documents, build_index
from ingestion.extract_code import extract_code_chunks, extract_doc_chunks
from ingestion.extract_git import clone_or_update, extract_commits
from ingestion.extract_github import GitHubClient, extract_issues, extract_prs
from ingestion.link_graph import build_graph


def cached(path: Path, fn, refresh: bool):
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    data = fn()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def ingest(repo: dict, refresh=False, skip_github=False):
    slug, owner, name = repo["slug"], repo["owner"], repo["name"]
    raw = RAW_DIR / slug
    raw.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print(f"\n=== {owner}/{name} ===")

    path = Path(repo["local_path"]) if repo.get("local_path") else clone_or_update(repo["url"], REPOS_DIR / slug)
    print("[1/5] commits")
    commits = cached(raw / "commits.json", lambda: extract_commits(path, repo.get("max_commits")), refresh)

    if skip_github:
        issues, prs = [], []
    else:
        gh = GitHubClient(GITHUB_TOKEN)
        print("[2/5] issues")
        issues = cached(raw / "issues.json", lambda: extract_issues(gh, owner, name, repo.get("max_issues", 500)), refresh)
        print("[3/5] pull requests + reviews")
        prs = cached(raw / "prs.json", lambda: extract_prs(gh, owner, name, repo.get("max_prs", 400)), refresh)

    print("[4/5] code + docs chunks, history graph")
    code = extract_code_chunks(path)
    docs_md = extract_doc_chunks(path)
    graph = build_graph(commits, issues, prs, code, owner, name)

    print("[5/5] embedding + indexing")
    documents = build_documents(commits, issues, prs, code, docs_md, owner, name)
    stats = build_index(slug, owner, name, documents, graph)
    print(json.dumps(stats["counts"]), f"| edges={stats['graph_edges']} | {time.time() - t0:.0f}s")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="all", help="slug (owner__name) or 'all'")
    ap.add_argument("--refresh", action="store_true", help="re-fetch commits/issues/PRs")
    ap.add_argument("--skip-github", action="store_true", help="don't call the GitHub API")
    args = ap.parse_args()
    repos = load_repos()
    if not repos:
        raise SystemExit("No repositories configured. Edit backend/repos.yaml first.")
    for r in repos:
        if args.repo in ("all", r["slug"]):
            ingest(r, args.refresh, args.skip_github)


if __name__ == "__main__":
    main()
