"""Agent-to-Agent (A2A) interoperability between Google ADK and CrewAI (requirement 6).

Direction 1 — CrewAI -> ADK
    The ADK History Agent is served as an A2A server with ADK's ``to_a2a()``; the CrewAI
    *History Liaison* agent delegates to it with ``A2AClientConfig`` (CrewAI's native A2A
    client). Used by the hierarchical pattern.

Direction 2 — ADK -> CrewAI
    The CrewAI Code and Discussion specialists are wrapped in an a2a-sdk ``AgentExecutor`` and
    served as A2A servers; ADK consumes them as ``RemoteA2aAgent`` sub-agents inside a
    ``ParallelAgent``. Used by the parallel and reflection patterns.

All servers run in background threads of this process (uvicorn), so their callbacks report
into the same RunMonitor and the provenance ledger covers both sides of every A2A hop.
Agent cards: ``http://127.0.0.1:<port>/.well-known/agent-card.json``.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import uvicorn

from readmind.settings import get_settings

_SERVERS: dict[str, tuple[uvicorn.Server, threading.Thread, str]] = {}
_LOCK = threading.Lock()
CARD_PATH = "/.well-known/agent-card.json"


def _free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _serve(key: str, app, port: int) -> str:
    host = get_settings().host
    srv = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on"))
    th = threading.Thread(target=srv.run, name=f"a2a-{key}", daemon=True)
    th.start()
    deadline = time.time() + 30
    while not srv.started:
        if time.time() > deadline or not th.is_alive():
            raise RuntimeError(f"A2A server {key} failed to start on port {port}")
        time.sleep(0.05)
    url = f"http://{host}:{port}{CARD_PATH}"
    _SERVERS[key] = (srv, th, url)
    return url


def _variant_port(base: int, variant: str, offset: int = 0) -> int:
    from readmind.prompts import VARIANTS
    return base + offset + list(VARIANTS).index(variant) * 2


# --------------------------------------------------------------------------- #
#  Direction 1: ADK history agent as an A2A server                             #
# --------------------------------------------------------------------------- #
def serve_adk_history(variant: str = "default") -> str:
    key = f"adk_history:{variant}"
    with _LOCK:
        if key in _SERVERS:
            return _SERVERS[key][2]
        from google.adk.a2a.utils.agent_to_a2a import to_a2a
        from readmind.agents_adk import history_agent
        port = _variant_port(get_settings().a2a_history_port, variant)
        if not _free(port):
            raise RuntimeError(f"port {port} busy")
        app = to_a2a(history_agent(variant, name="history_agent"), host=get_settings().host, port=port)
        return _serve(key, app, port)


# --------------------------------------------------------------------------- #
#  Direction 2: CrewAI specialists as A2A servers                              #
# --------------------------------------------------------------------------- #
def _crew_executor(role: str, variant: str):
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.utils import new_agent_text_message

    class CrewSpecialistExecutor(AgentExecutor):
        """Runs a one-agent CrewAI crew per A2A request and returns its SpecialistReport JSON."""

        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            text = context.get_user_input() or ""
            result = await asyncio.to_thread(_run_crew_specialist, role, variant, text)
            await event_queue.enqueue_event(new_agent_text_message(result, context_id=context.context_id,
                                                                   task_id=context.task_id))

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            raise NotImplementedError("cancel not supported")

    return CrewSpecialistExecutor()


def _run_crew_specialist(role: str, variant: str, text: str) -> str:
    import re
    from readmind.agents_crew import code_agent, discussion_agent, run_crew, specialist_task
    from readmind.observability.monitor import active_monitor
    from readmind.schemas import SpecialistReport, parse_model
    qm = re.search(r"QUESTION:\s*(.+)", text)
    question = qm.group(1).strip() if qm else text.strip()
    mon = active_monitor()
    if mon:
        mon.event("crewai", role, "a2a_served", {"brief": f"A2A request served by CrewAI {role} specialist"})
    agent = code_agent(variant) if role == "code" else discussion_agent(variant)
    try:
        out, _ = run_crew([agent], [specialist_task(agent, role, question, question, "(none — fan-out)")])
        rep = parse_model(SpecialistReport, out.pydantic or out.raw)
        return (rep or SpecialistReport(agent=role, summary=str(out.raw)[:500])).model_dump_json()
    except Exception as e:
        if mon:
            mon.error("crewai", role, f"A2A specialist failed: {e}")
        return SpecialistReport(agent=role, summary=f"{role} specialist failed: {type(e).__name__}",
                                gaps=[str(e)[:200]]).model_dump_json()


def serve_crew_specialist(role: str, variant: str = "default") -> str:
    key = f"crew_{role}:{variant}"
    with _LOCK:
        if key in _SERVERS:
            return _SERVERS[key][2]
        from a2a.server.apps import A2AStarletteApplication
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.tasks import InMemoryTaskStore
        from a2a.types import AgentCapabilities, AgentCard, AgentSkill
        s = get_settings()
        port = _variant_port(s.a2a_crew_port + 20, variant, 0 if role == "code" else 1)
        desc = {"code": "CrewAI Code Analysis Specialist: what the current code looks like (MCP code tools).",
                "discussion": "CrewAI Issue/PR Specialist: issues, pull requests and reviews behind a change."}[role]
        card = AgentCard(name=f"crew_{role}_specialist", description=desc, url=f"http://{s.host}:{port}/",
                         version="1.0.0", capabilities=AgentCapabilities(streaming=False),
                         defaultInputModes=["text/plain"], defaultOutputModes=["application/json", "text/plain"],
                         skills=[AgentSkill(id=f"{role}_report", name=f"{role} report", description=desc,
                                            tags=["readmind", "crewai", role], examples=["QUESTION: Why was PAE removed?"])])
        handler = DefaultRequestHandler(agent_executor=_crew_executor(role, variant), task_store=InMemoryTaskStore())
        app = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
        return _serve(key, app, port)


def remote_crew_agent(role: str, variant: str = "default"):
    """ADK ``RemoteA2aAgent`` proxy for a CrewAI specialist (ADK -> A2A -> CrewAI)."""
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
    from readmind.observability.callbacks import adk_remote_callbacks
    url = serve_crew_specialist(role, variant)
    name = f"crew_{role}"
    return RemoteA2aAgent(name=name, agent_card=url, description=f"CrewAI {role} specialist over A2A", timeout=900,
                          **adk_remote_callbacks(name))


def agent_cards() -> dict[str, dict]:
    import httpx
    out = {}
    for key, (_, _, url) in _SERVERS.items():
        try:
            out[key] = httpx.get(url, timeout=5).json()
        except Exception as e:
            out[key] = {"error": str(e)}
    return out


def shutdown() -> None:
    for key, (srv, th, _) in list(_SERVERS.items()):
        srv.should_exit = True
        th.join(timeout=5)
    _SERVERS.clear()
