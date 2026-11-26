"""MCP server ``readmind-repo`` — repository history tools.

    python -m readmind.mcp_servers.repo_server --transport streamable-http --port 8201
    python -m readmind.mcp_servers.repo_server --transport stdio
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from readmind.tools import repo_tools as T  # noqa: E402


def build(host: str = "127.0.0.1", port: int = 8201) -> FastMCP:
    mcp = FastMCP("readmind-repo", instructions="Evidence about the history and current code of a git repository. "
                  "Every result carries an evidence id (commit:, pr:, issue:, review:, code:) that answers must cite.",
                  host=host, port=port, log_level="WARNING")

    @mcp.tool()
    def search_commits(query: str, k: int = 6, path: str = "") -> dict:
        """Search commit messages (BM25). Optional `path` keeps commits that touched that file/dir.
        Returns commits with id commit:<sha10>, subject, author, date, files and linked issues/PRs."""
        return T.search_commits(query, k, path)

    @mcp.tool()
    def get_commit(commit_id: str) -> dict:
        """Full commit message, author, date, changed files and linked issues/PRs for a commit id or SHA."""
        return T.get_commit(commit_id)

    @mcp.tool()
    def file_history(path: str, k: int = 8) -> dict:
        """Most recent commits that touched a repository path (newest first)."""
        return T.file_history(path, k)

    @mcp.tool()
    def search_code(query: str, k: int = 5) -> dict:
        """Search the CURRENT code snapshot: file paths, function/macro/struct definitions, keywords.
        Reports files named in the query that no longer exist (code:absent:<path>)."""
        return T.search_code(query, k)

    @mcp.tool()
    def read_code(path: str, start_line: int = 1, end_line: int = 80) -> dict:
        """Numbered source lines of a file in the current snapshot (max 200 lines)."""
        return T.read_code(path, start_line, end_line)

    @mcp.tool()
    def search_discussions(query: str, k: int = 6) -> dict:
        """Search GitHub issues, pull requests and review comments; each hit lists the commits it triggered."""
        return T.search_discussions(query, k)

    @mcp.tool()
    def get_linked(item_id: str) -> dict:
        """Link-graph neighbours of an evidence id (commit <-> PR <-> issue <-> review)."""
        return T.get_linked(item_id)

    return mcp


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8201)
    a = ap.parse_args(argv)
    # warm the index before accepting connections so the first tool call is fast
    T._store()
    build(a.host, a.port).run(transport=a.transport)


if __name__ == "__main__":
    main()
