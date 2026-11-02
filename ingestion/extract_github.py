"""Fetch issues, pull requests, review comments and PR discussions from the GitHub REST API.
Handles pagination and rate limits. A token (GITHUB_TOKEN) is strongly recommended: 5000 req/h
instead of 60."""
import time

import requests
from tqdm import tqdm

API = "https://api.github.com"
MAX_BODY = 6000
MAX_COMMENT = 1500


def _trim(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n] + " …[truncated]"


class GitHubClient:
    def __init__(self, token: str = ""):
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def get(self, url, params=None):
        for attempt in range(6):
            r = self.s.get(url, params=params, timeout=30)
            if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - time.time(), 1) + 2
                print(f"  rate limited, sleeping {int(wait)}s")
                time.sleep(wait)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def paginate(self, url, params=None, limit=None, keep=None):
        params = dict(params or {})
        params.setdefault("per_page", 100)
        items = []
        while url:
            r = self.get(url, params)
            for it in r.json():
                if keep is None or keep(it):
                    items.append(it)
                    if limit and len(items) >= limit:
                        return items
            url = r.links.get("next", {}).get("url")
            params = None  # the "next" URL already has the query string
        return items


def _comments(client, url, limit):
    out = []
    for c in client.paginate(url, limit=limit):
        out.append({"author": (c.get("user") or {}).get("login"), "date": c.get("created_at"),
                    "body": _trim(c.get("body"), MAX_COMMENT)})
    return out


def extract_issues(client: GitHubClient, owner: str, repo: str, max_issues=500, max_comments=20):
    raw = client.paginate(f"{API}/repos/{owner}/{repo}/issues",
                          {"state": "all", "sort": "updated", "direction": "desc"},
                          limit=max_issues, keep=lambda x: "pull_request" not in x)
    issues = []
    for it in tqdm(raw, desc="issues"):
        comments = _comments(client, it["comments_url"], max_comments) if it.get("comments") else []
        issues.append({
            "number": it["number"], "title": it["title"], "body": _trim(it.get("body"), MAX_BODY),
            "state": it["state"], "labels": [l["name"] for l in it.get("labels", [])],
            "author": (it.get("user") or {}).get("login"), "created_at": it["created_at"],
            "closed_at": it.get("closed_at"), "url": it["html_url"], "comments": comments,
        })
    return issues


def extract_prs(client: GitHubClient, owner: str, repo: str, max_prs=400):
    base = f"{API}/repos/{owner}/{repo}"
    pulls = client.paginate(f"{base}/pulls", {"state": "all", "sort": "updated", "direction": "desc"},
                            limit=max_prs)
    prs = []
    for p in tqdm(pulls, desc="pull requests"):
        n = p["number"]
        commits = [c["sha"] for c in client.paginate(f"{base}/pulls/{n}/commits", limit=100)]
        files = [f["filename"] for f in client.paginate(f"{base}/pulls/{n}/files", limit=100)]
        review_comments = [{
            "id": c["id"], "author": (c.get("user") or {}).get("login"), "date": c.get("created_at"),
            "path": c.get("path"), "line": c.get("line") or c.get("original_line"),
            "body": _trim(c.get("body"), MAX_COMMENT), "diff_hunk": _trim(c.get("diff_hunk"), 600),
            "url": c.get("html_url"),
        } for c in client.paginate(f"{base}/pulls/{n}/comments", limit=100)]
        reviews = [{
            "id": r["id"], "author": (r.get("user") or {}).get("login"), "state": r.get("state"),
            "date": r.get("submitted_at"), "body": _trim(r.get("body"), MAX_COMMENT),
            "url": r.get("html_url"),
        } for r in client.paginate(f"{base}/pulls/{n}/reviews", limit=50) if (r.get("body") or "").strip()]
        discussion = _comments(client, f"{base}/issues/{n}/comments", 30) if p.get("comments", 1) else []
        prs.append({
            "number": n, "title": p["title"], "body": _trim(p.get("body"), MAX_BODY),
            "state": p["state"], "merged_at": p.get("merged_at"), "created_at": p["created_at"],
            "author": (p.get("user") or {}).get("login"), "url": p["html_url"],
            "merge_commit_sha": p.get("merge_commit_sha"), "commits": commits, "files": files,
            "review_comments": review_comments, "reviews": reviews, "discussion": discussion,
        })
    return prs
