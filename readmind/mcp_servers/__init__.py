"""MCP servers and the client-side connection helpers used by both frameworks.

Two servers (requirement: at least two external tools via MCP):

    readmind-repo       repository history: commits, file history, code, issues/PRs/reviews, links
    readmind-analytics  calculator, date arithmetic, cost estimator, Wikipedia lookup

Transport ``http`` (default) starts each server once as a background process
(streamable HTTP on 127.0.0.1); ``stdio`` spawns the server per client session.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from readmind.settings import ROOT, get_settings

REPO_TOOLS = ["search_commits", "get_commit", "file_history", "search_code", "read_code",
              "search_discussions", "get_linked"]
ANALYTICS_TOOLS = ["calculate", "date_diff", "estimate_cost", "web_lookup"]
SERVERS = {"repo": "readmind.mcp_servers.repo_server", "analytics": "readmind.mcp_servers.analytics_server"}
_PROCS: dict[str, subprocess.Popen] = {}


def _port(name: str) -> int:
    s = get_settings()
    return s.mcp_repo_port if name == "repo" else s.mcp_analytics_port


def url(name: str) -> str:
    return f"http://{get_settings().host}:{_port(name)}/mcp"


def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def start_servers(timeout: float = 90.0, log_dir: Path | None = None) -> dict[str, str]:
    """Start both MCP servers (HTTP transport) if they are not already running."""
    s = get_settings()
    if s.mcp_transport != "http":
        return {}
    log_dir = log_dir or (s.work_dir / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    for name, module in SERVERS.items():
        if name in _PROCS and _PROCS[name].poll() is None:
            continue
        if _port_open(_port(name)):
            continue      # already served (e.g. by another notebook cell)
        log = open(log_dir / f"mcp_{name}.log", "a")
        _PROCS[name] = subprocess.Popen([sys.executable, "-m", module, "--transport", "streamable-http",
                                         "--port", str(_port(name))], env=_child_env(), stdout=log, stderr=log,
                                        cwd=str(ROOT))
    deadline = time.time() + timeout
    for name in SERVERS:
        while not _port_open(_port(name)):
            if name in _PROCS and _PROCS[name].poll() is not None:
                raise RuntimeError(f"MCP server {name} exited; see {log_dir}/mcp_{name}.log")
            if time.time() > deadline:
                raise TimeoutError(f"MCP server {name} did not start within {timeout}s")
            time.sleep(0.2)
    return {n: url(n) for n in SERVERS}


def stop_servers() -> None:
    for p in _PROCS.values():
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
    _PROCS.clear()


# --------------------------------------------------------------------------- #
#  Client configs                                                              #
# --------------------------------------------------------------------------- #
def adk_toolset(name: str, tools: list[str] | None = None):
    """Google ADK ``McpToolset`` for one server (optionally filtered to *tools*)."""
    from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams, StreamableHTTPConnectionParams
    from mcp import StdioServerParameters
    if get_settings().mcp_transport == "http":
        params = StreamableHTTPConnectionParams(url=url(name), timeout=60, sse_read_timeout=300)
    else:
        params = StdioConnectionParams(server_params=StdioServerParameters(
            command=sys.executable, args=["-m", SERVERS[name], "--transport", "stdio"], env=_child_env()), timeout=60)
    return McpToolset(connection_params=params, tool_filter=tools)


def crew_mcp(name: str, tools: list[str] | None = None):
    """CrewAI native MCP server config (``Agent(mcps=[...])``) for one server."""
    from crewai.mcp import MCPServerHTTP, MCPServerStdio
    from crewai.mcp.filters import create_static_tool_filter
    flt = create_static_tool_filter(allowed_tool_names=tools) if tools else None
    if get_settings().mcp_transport == "http":
        return MCPServerHTTP(url=url(name), streamable=True, tool_filter=flt, cache_tools_list=True)
    return MCPServerStdio(command=sys.executable, args=["-m", SERVERS[name], "--transport", "stdio"],
                          env=_child_env(), tool_filter=flt, cache_tools_list=True)
