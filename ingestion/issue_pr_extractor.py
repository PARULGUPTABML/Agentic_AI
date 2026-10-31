"""Issue / PR / review corpus extraction.

Pulls a repository's issues, pull requests, PR reviews and review comments
from the GitHub REST API, normalises them into evidence documents and builds
commit <-> issue / PR links.

Usage::

    export GITHUB_TOKEN=ghp_...            # strongly recommended (5000 req/h)
    python -m ingestion.issue_pr_extractor --repo seL4/seL4 --max-prs 400 --max-issues 400 \\
        --git-repo ../repos/seL4__seL4     # optional local clone, used for commit linking

Output (``data/corpus/<owner>__<repo>/``)::

    issues.jsonl   one issue per line            id = issue:<n>
    prs.jsonl      one pull request per line     id = pr:<n>
    reviews.jsonl  one PR review per line        id = review:<pr>-<review_id>
    links.jsonl    {"src", "dst", "rel"} edges   (commit<->issue/PR, review->PR, ...)
    manifest.json  counts, parameters, timestamp

Every document has the fields the rest of the system expects:
``id, type, title, text, date, url, files`` (plus type-specific extras).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.github_client import GitHubClient, GitHubError
from ingestion.linker import build_links

MAX_TEXT_CHARS = 12_000       # keep individual documents a sane size for embedding
MAX_COMMENTS = 60
BOT_SUFFIXES = ("[bot]",)


# ---------------------------------------------------------------------- #
#  Normalisation helpers                                                  #
# ---------------------------------------------------------------------- #
def _login(user: dict | None) -> str:
    return (user or {}).get("login", "") or "ghost"


def _is_bot(user: dict | None) -> bool:
    login = _login(user)
    return (user or {}).get("type") == "Bot" or login.endswith(BOT_SUFFIXES)


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = (text or "").replace("\r\n", "\n").strip()
    return text if len(text) <= limit else text[:limit] + "\n…[truncated]"


def _thread(comments: Iterable[dict]) -> str:
    lines = []
    for c in list(comments)[:MAX_COMMENTS]:
        if _is_bot(c.get("user")):
            continue
        body = (c.get("body") or "").strip()
        if body:
            lines.append(f"@{_login(c.get('user'))} ({(c.get('created_at') or '')[:10]}): {body}")
    return "\n\n".join(lines)


def normalise_issue(raw: dict, comments: list[dict], repo: str) -> dict:
    n = raw["number"]
    body = raw.get("body") or ""
    discussion = _thread(comments)
    text = f"Issue #{n}: {raw.get('title', '')}\n\n{body}"
    if discussion:
        text += f"\n\n--- Discussion ---\n{discussion}"
    return {
        "id": f"issue:{n}",
        "type": "issue",
        "number": n,
        "title": raw.get("title", ""),
        "body": _clip(body, 4000),
        "text": _clip(text),
        "author": _login(raw.get("user")),
        "state": raw.get("state", ""),
        "state_reason": raw.get("state_reason") or "",
        "labels": [l.get("name", "") for l in raw.get("labels", []) if isinstance(l, dict)],
        "date": raw.get("created_at", ""),
        "closed_at": raw.get("closed_at") or "",
        "comments_count": raw.get("comments", 0),
        "url": raw.get("html_url", f"https://github.com/{repo}/issues/{n}"),
        "files": [],
    }


def normalise_pr(raw: dict, comments: list[dict], commit_shas: list[str],
                 files: list[str], repo: str) -> dict:
    n = raw["number"]
    body = raw.get("body") or ""
    discussion = _thread(comments)
    merged = bool(raw.get("merged_at"))
    text = (f"Pull request #{n}: {raw.get('title', '')}\n"
            f"State: {'merged' if merged else raw.get('state', '')} | "
            f"base: {(raw.get('base') or {}).get('ref', '')}\n\n{body}")
    if files:
        text += "\n\nFiles changed: " + ", ".join(files[:40])
    if discussion:
        text += f"\n\n--- Discussion ---\n{discussion}"
    return {
        "id": f"pr:{n}",
        "type": "pr",
        "number": n,
        "title": raw.get("title", ""),
        "body": _clip(body, 4000),
        "text": _clip(text),
        "author": _login(raw.get("user")),
        "state": raw.get("state", ""),
        "merged": merged,
        "merged_at": raw.get("merged_at") or "",
        "merge_commit_sha": (raw.get("merge_commit_sha") or "") if merged else "",
        "commit_shas": commit_shas,
        "base": (raw.get("base") or {}).get("ref", ""),
        "labels": [l.get("name", "") for l in raw.get("labels", []) if isinstance(l, dict)],
        "date": raw.get("created_at", ""),
        "closed_at": raw.get("closed_at") or "",
        "url": raw.get("html_url", f"https://github.com/{repo}/pull/{n}"),
        "files": files,
    }


def normalise_reviews(pr_number: int, reviews: list[dict], inline: list[dict], repo: str) -> list[dict]:
    """One document per submitted review, with its inline comments attached."""
    by_review: dict[Any, list[dict]] = {}
    for c in inline:
        by_review.setdefault(c.get("pull_request_review_id"), []).append(c)

    docs = []
    for r in reviews:
        if _is_bot(r.get("user")):
            continue
        rid = r.get("id")
        comments = by_review.pop(rid, [])
        body = (r.get("body") or "").strip()
        # Empty "approve" clicks carry no rationale but answer "who approved?" questions.
        parts = [f"Review of PR #{pr_number} by @{_login(r.get('user'))}: {r.get('state', '')}"]
        if body:
            parts.append(body)
        files = []
        for c in comments[:MAX_COMMENTS]:
            path = c.get("path", "")
            files.append(path)
            hunk = (c.get("diff_hunk") or "").splitlines()[-4:]
            parts.append(f"[{path}:{c.get('line') or c.get('original_line') or ''}] "
                         f"{(c.get('body') or '').strip()}\n" + "\n".join(hunk))
        docs.append({
            "id": f"review:{pr_number}-{rid}",
            "type": "review",
            "pr_number": pr_number,
            "review_id": rid,
            "title": f"Review on PR #{pr_number} ({r.get('state', '').lower()}) by {_login(r.get('user'))}",
            "text": _clip("\n\n".join(parts)),
            "author": _login(r.get("user")),
            "state": r.get("state", ""),
            "date": r.get("submitted_at") or "",
            "url": r.get("html_url", f"https://github.com/{repo}/pull/{pr_number}"),
            "files": sorted(set(f for f in files if f)),
        })
    # Inline comments not attached to a review object (older PRs) -> one doc.
    orphan = [c for cs in by_review.values() for c in cs]
    if orphan:
        parts = [f"Inline review comments on PR #{pr_number}"]
        for c in orphan[:MAX_COMMENTS]:
            parts.append(f"[{c.get('path', '')}] @{_login(c.get('user'))}: {(c.get('body') or '').strip()}")
        docs.append({
            "id": f"review:{pr_number}-inline",
            "type": "review",
            "pr_number": pr_number,
            "review_id": None,
            "title": f"Inline review comments on PR #{pr_number}",
            "text": _clip("\n\n".join(parts)),
            "author": "",
            "state": "COMMENTED",
            "date": (orphan[0].get("created_at") or ""),
            "url": f"https://github.com/{repo}/pull/{pr_number}",
            "files": sorted(set(c.get("path", "") for c in orphan if c.get("path"))),
        })
    return docs


# ---------------------------------------------------------------------- #
#  Local commits (for linking only — full commit corpus is a separate stream)
# ---------------------------------------------------------------------- #
def read_local_commits(git_repo: str | Path, max_commits: int | None = None) -> list[dict]:
    """Read ``sha`` + full message from a local clone with ``git log``."""
    sep, end = "\x1f", "\x1e"
    cmd = ["git", "-C", str(git_repo), "log", "--all", f"--format=%H{sep}%aI{sep}%B{end}"]
    if max_commits:
        cmd.insert(4, f"-n{max_commits}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"[warn] could not read commits from {git_repo}: {e}", file=sys.stderr)
        return []
    commits = []
    for rec in out.split(end):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, date, msg = (rec.split(sep, 2) + ["", ""])[:3]
        commits.append({"sha": sha.strip(), "date": date, "message": msg})
    return commits


def read_commits_jsonl(path: str | Path) -> list[dict]:
    """Accept the commit corpus from the History stream (needs ``sha`` + ``message``)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            sha = d.get("sha") or d.get("hash") or str(d.get("id", "")).replace("commit:", "")
            out.append({"sha": sha, "message": d.get("message") or d.get("text", ""),
                        "date": d.get("date", "")})
    return out


# ---------------------------------------------------------------------- #
#  Extraction                                                             #
# ---------------------------------------------------------------------- #
class IssuePRExtractor:
    def __init__(self, client: GitHubClient, repo: str, *, with_comments: bool = True,
                 with_reviews: bool = True, with_pr_details: bool = True, verbose: bool = True):
        self.gh = client
        self.repo = repo
        self.with_comments = with_comments
        self.with_reviews = with_reviews
        self.with_pr_details = with_pr_details
        self.verbose = verbose
        self.errors: list[str] = []

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def _safe_list(self, path: str, max_items: int | None = None) -> list[dict]:
        try:
            return list(self.gh.paginate(path, max_items=max_items))
        except GitHubError as e:
            self.errors.append(str(e))
            return []

    def extract_issues(self, max_issues: int | None, since: str | None = None) -> list[dict]:
        base = f"/repos/{self.repo}"
        docs = []
        params = {"state": "all", "sort": "created", "direction": "desc"}
        if since:
            params["since"] = since
        for raw in self.gh.paginate(f"{base}/issues", **params):
            if "pull_request" in raw:          # the issues endpoint also lists PRs
                continue
            comments = (self._safe_list(f"{base}/issues/{raw['number']}/comments")
                        if self.with_comments and raw.get("comments") else [])
            docs.append(normalise_issue(raw, comments, self.repo))
            if len(docs) % 50 == 0:
                self._log(f"  issues: {len(docs)}")
            if max_issues and len(docs) >= max_issues:
                break
        return docs

    def extract_prs(self, max_prs: int | None) -> tuple[list[dict], list[dict]]:
        base = f"/repos/{self.repo}"
        prs, reviews = [], []
        for raw in self.gh.paginate(f"{base}/pulls", max_items=max_prs,
                                    state="all", sort="created", direction="desc"):
            n = raw["number"]
            comments = self._safe_list(f"{base}/issues/{n}/comments") if self.with_comments else []
            commit_shas, files = [], []
            if self.with_pr_details:
                commit_shas = [c["sha"] for c in self._safe_list(f"{base}/pulls/{n}/commits", 250)]
                files = [f["filename"] for f in self._safe_list(f"{base}/pulls/{n}/files", 300)]
            prs.append(normalise_pr(raw, comments, commit_shas, files, self.repo))
            if self.with_reviews:
                rv = self._safe_list(f"{base}/pulls/{n}/reviews")
                inline = self._safe_list(f"{base}/pulls/{n}/comments")
                reviews.extend(normalise_reviews(n, rv, inline, self.repo))
            if len(prs) % 25 == 0:
                self._log(f"  PRs: {len(prs)} (reviews so far: {len(reviews)})")
        return prs, reviews


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def write_corpus(out_dir: Path, repo: str, issues: list[dict], prs: list[dict],
                 reviews: list[dict], commits: list[dict], params: dict | None = None,
                 errors: list[str] | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    links = build_links(commits, issues, prs, reviews, own_repo=repo)
    counts = {
        "issues": write_jsonl(out_dir / "issues.jsonl", issues),
        "prs": write_jsonl(out_dir / "prs.jsonl", prs),
        "reviews": write_jsonl(out_dir / "reviews.jsonl", reviews),
        "links": write_jsonl(out_dir / "links.jsonl", (l.to_dict() for l in links)),
        "commits_scanned_for_links": len(commits),
    }
    rel_counts: dict[str, int] = {}
    for l in links:
        rel_counts[l.rel] = rel_counts.get(l.rel, 0) + 1
    manifest = {
        "repo": repo,
        "slug": repo.replace("/", "__"),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "link_relations": rel_counts,
        "params": params or {},
        "errors": (errors or [])[:50],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="owner/name, e.g. seL4/seL4")
    ap.add_argument("--out", default=None, help="output dir (default data/corpus/<owner>__<repo>)")
    ap.add_argument("--max-issues", type=int, default=500)
    ap.add_argument("--max-prs", type=int, default=500)
    ap.add_argument("--since", default=None, help="only issues updated after this ISO date")
    ap.add_argument("--git-repo", default=None, help="local clone used to link commits -> issues/PRs")
    ap.add_argument("--commits-jsonl", default=None, help="commit corpus JSONL (alternative to --git-repo)")
    ap.add_argument("--cache-dir", default="data/cache/github")
    ap.add_argument("--offline", action="store_true", help="serve only cached API responses")
    ap.add_argument("--no-comments", action="store_true")
    ap.add_argument("--no-reviews", action="store_true")
    ap.add_argument("--no-pr-details", action="store_true", help="skip per-PR commits/files calls")
    args = ap.parse_args(argv)

    slug = args.repo.replace("/", "__")
    out_dir = Path(args.out or f"data/corpus/{slug}")
    client = GitHubClient(cache_dir=args.cache_dir, offline=args.offline)
    if not client.token and not args.offline:
        print("[warn] GITHUB_TOKEN not set — unauthenticated limit is 60 requests/hour.", file=sys.stderr)

    ex = IssuePRExtractor(client, args.repo, with_comments=not args.no_comments,
                          with_reviews=not args.no_reviews, with_pr_details=not args.no_pr_details)
    print(f"Extracting issues from {args.repo} ...", file=sys.stderr)
    issues = ex.extract_issues(args.max_issues, since=args.since)
    print(f"Extracting PRs + reviews from {args.repo} ...", file=sys.stderr)
    prs, reviews = ex.extract_prs(args.max_prs)

    commits: list[dict] = []
    if args.commits_jsonl:
        commits = read_commits_jsonl(args.commits_jsonl)
    elif args.git_repo:
        commits = read_local_commits(args.git_repo)

    manifest = write_corpus(out_dir, args.repo, issues, prs, reviews, commits,
                            params=vars(args), errors=ex.errors)
    manifest["api_requests"] = client.requests_made
    manifest["cache_hits"] = client.cache_hits
    print(json.dumps(manifest["counts"], indent=2))
    print(f"Corpus written to {out_dir}", file=sys.stderr)
    return manifest


if __name__ == "__main__":
    main()
