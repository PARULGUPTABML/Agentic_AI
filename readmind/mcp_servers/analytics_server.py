"""MCP server ``readmind-analytics`` — calculator, dates, cost estimator, web lookup.

    python -m readmind.mcp_servers.analytics_server --transport streamable-http --port 8202
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from readmind.tools import analytics_tools as T  # noqa: E402


def build(host: str = "127.0.0.1", port: int = 8202) -> FastMCP:
    mcp = FastMCP("readmind-analytics", instructions="Utility tools: arithmetic, date differences, LLM cost "
                  "estimates and external background lookup (not repository evidence).",
                  host=host, port=port, log_level="WARNING")

    @mcp.tool()
    def calculate(expression: str) -> dict:
        """Evaluate an arithmetic expression safely, e.g. '(2017-2009)*12' or 'sqrt(2)'."""
        return T.calculate(expression)

    @mcp.tool()
    def date_diff(start: str, end: str, unit: str = "days") -> dict:
        """Elapsed time between two ISO dates; unit = days | weeks | months | years."""
        return T.date_diff(start, end, unit)

    @mcp.tool()
    def estimate_cost(prompt_tokens: int, completion_tokens: int) -> dict:
        """USD cost of an LLM call for the configured model and price sheet."""
        return T.estimate_cost(prompt_tokens, completion_tokens)

    @mcp.tool()
    def web_lookup(query: str, k: int = 3) -> dict:
        """Background knowledge from Wikipedia (e.g. what PAE or IBRS is). Never cite as repository evidence."""
        return T.web_lookup(query, k)

    return mcp


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8202)
    a = ap.parse_args(argv)
    build(a.host, a.port).run(transport=a.transport)


if __name__ == "__main__":
    main()
