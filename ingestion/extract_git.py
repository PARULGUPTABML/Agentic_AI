"""Clone a repository and extract commit history with PyDriller."""
from __future__ import annotations
import subprocess
from pathlib import Path

from pydriller import Repository

MAX_DIFF_CHARS = 3000
MAX_FILES_PER_COMMIT = 50


def clone_or_update(url: str, dest: Path) -> Path:
    if (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "--quiet"], check=False)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    return dest


def extract_commits(repo_path: Path, max_commits: int | None = 10000) -> list[dict]:
    """Newest-first list of commits with message, changed files and a trimmed diff."""
    commits = []
    for c in Repository(str(repo_path), order="reverse").traverse_commits():
        files, diff_parts, diff_len = [], [], 0
        try:
            modified = c.modified_files
        except Exception:  # very large or broken commits
            modified = []
        for m in modified[:MAX_FILES_PER_COMMIT]:
            path = m.new_path or m.old_path
            files.append({"path": path, "change": m.change_type.name,
                          "added": m.added_lines, "deleted": m.deleted_lines})
            if diff_len < MAX_DIFF_CHARS and m.diff:
                part = f"--- {path}\n{m.diff[:1500]}"
                diff_parts.append(part)
                diff_len += len(part)
        commits.append({
            "sha": c.hash,
            "message": c.msg,
            "author": c.author.name,
            "date": c.committer_date.isoformat(),
            "is_merge": c.merge,
            "files": files,
            "diff": "\n".join(diff_parts)[:MAX_DIFF_CHARS],
        })
        n = len(commits)
        if n % 500 == 0:
            print(f"  ... {n} commits extracted", flush=True)
        if max_commits and n >= max_commits:
            break
    return commits
