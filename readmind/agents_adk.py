"""Google ADK agents.

    planner_agent        Orchestrator — output_schema=Plan, output_key="plan"
    history_agent        Repository History Agent — MCP tools (repo + analytics), output_key="history_report"
    refiner_agent        targeted re-search driven by state["verdict"]
    verifier_agent       Evidence Verification — output_schema=VerificationVerdict, reads {shared_context}
    single_agent         monolithic ReAct agent with every tool (baseline pattern)
    StopIfSufficient     custom BaseAgent that escalates out of a LoopAgent when the verdict is sufficient

``run_adk(agent, text, state)`` runs any agent / workflow agent with a fresh in-memory
session, returns (final_text, session_state, events) and closes its MCP sessions.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from readmind import prompts
from readmind.llm import adk_model
from readmind.mcp_servers import adk_toolset
from readmind.observability.callbacks import adk_callbacks
from readmind.schemas import Plan, VerificationVerdict

HISTORY_REPO_TOOLS = ["search_commits", "get_commit", "file_history", "get_linked"]
HISTORY_ANALYTICS_TOOLS = ["date_diff", "calculate", "web_lookup"]
_CFG = types.GenerateContentConfig(temperature=0.0)


def _agent(name: str, role: str, variant: str, *, description: str, repo_tools: list[str] | None = None,
           analytics_tools: list[str] | None = None, output_schema=None, output_key: str | None = None,
           context: bool = False) -> LlmAgent:
    instr = prompts.instruction(role, variant)
    if context:
        instr += "\n\nShared context (state 'shared_context'):\n{shared_context?}"
    tools = []
    if repo_tools:
        tools.append(adk_toolset("repo", repo_tools))
    if analytics_tools:
        tools.append(adk_toolset("analytics", analytics_tools))
    kw: dict[str, Any] = dict(name=name, model=adk_model(), instruction=instr, description=description,
                              tools=tools, generate_content_config=_CFG, **adk_callbacks(role))
    if output_schema is not None:
        kw["output_schema"] = output_schema
    if output_key:
        kw["output_key"] = output_key
    return LlmAgent(**kw)


def planner_agent(variant: str = "default") -> LlmAgent:
    return _agent("planner", "planner", variant, description="Classifies intent and decomposes the question.",
                  output_schema=Plan, output_key="plan")


def history_agent(variant: str = "default", name: str = "history_agent") -> LlmAgent:
    return _agent(name, "history", variant,
                  description="Answers repository-history questions from git commits (who, when, why).",
                  repo_tools=HISTORY_REPO_TOOLS, analytics_tools=HISTORY_ANALYTICS_TOOLS,
                  output_key="history_report", context=False)


def refiner_agent(variant: str = "default") -> LlmAgent:
    a = _agent("history_refiner", "refiner", variant, description="Targeted re-search for verifier gaps.",
               repo_tools=["search_commits", "get_commit"], output_key="history_refined")
    a.instruction += "\n\nVerifier verdict (state 'verdict'):\n{verdict?}"
    return a


def verifier_agent(variant: str = "default", name: str = "verifier") -> LlmAgent:
    return _agent(name, "verifier", variant, description="Checks evidence sufficiency and finds gaps.",
                  output_schema=VerificationVerdict, output_key="verdict", context=True)


def single_agent(variant: str = "default") -> LlmAgent:
    return _agent("single_react_agent", "single", variant, description="Monolithic ReAct agent with all tools.",
                  repo_tools=["search_commits", "get_commit", "file_history", "search_code", "read_code",
                              "search_discussions", "get_linked"],
                  analytics_tools=["date_diff", "calculate", "web_lookup"], output_key="final_json")


class StopIfSufficient(BaseAgent):
    """Escalates (exits the enclosing LoopAgent) once state['verdict'].sufficient is true."""

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        v = ctx.session.state.get("verdict")
        if isinstance(v, str):
            from readmind.schemas import parse_model
            v = parse_model(VerificationVerdict, v)
            v = v.model_dump() if v else {}
        ok = bool((v or {}).get("sufficient"))
        yield Event(author=self.name, invocation_id=ctx.invocation_id, actions=EventActions(escalate=ok),
                    content=types.Content(role="model", parts=[types.Part(text=f"sufficient={ok}")]))


def reflection_loop(variant: str = "default", max_iterations: int = 2) -> LoopAgent:
    """ADK-native critic loop: verify -> stop if sufficient -> refine -> (repeat)."""
    return LoopAgent(name="reflection_loop", max_iterations=max_iterations,
                     sub_agents=[verifier_agent(variant, name="critic"), StopIfSufficient(name="stop_check"),
                                 refiner_agent(variant)])


def parallel_gather(sub_agents: list[BaseAgent]) -> ParallelAgent:
    return ParallelAgent(name="parallel_gather", sub_agents=sub_agents)


def sequential(name: str, sub_agents: list[BaseAgent]) -> SequentialAgent:
    return SequentialAgent(name=name, sub_agents=sub_agents)


# --------------------------------------------------------------------------- #
#  Running                                                                     #
# --------------------------------------------------------------------------- #
def _toolsets(agent: BaseAgent) -> list:
    out = []
    for t in getattr(agent, "tools", []) or []:
        if hasattr(t, "close") and hasattr(t, "get_tools"):
            out.append(t)
    for sub in getattr(agent, "sub_agents", []) or []:
        out += _toolsets(sub)
    return out


async def run_adk_async(agent: BaseAgent, text: str, state: dict | None = None,
                        app: str = "readmind") -> tuple[str, dict, list[Event]]:
    svc = InMemorySessionService()
    runner = Runner(app_name=app, agent=agent, session_service=svc)
    session = await svc.create_session(app_name=app, user_id="readmind", state=dict(state or {}))
    events: list[Event] = []
    final = ""
    try:
        async for ev in runner.run_async(user_id="readmind", session_id=session.id,
                                         new_message=types.Content(role="user", parts=[types.Part(text=text)])):
            events.append(ev)
            if ev.content and ev.content.parts:
                t = "".join(p.text or "" for p in ev.content.parts if getattr(p, "text", None))
                if t and not ev.partial:
                    final = t
        sess = await svc.get_session(app_name=app, user_id="readmind", session_id=session.id)
        return final, dict(sess.state), events
    finally:
        for ts in _toolsets(agent):
            try:
                await ts.close()
            except Exception:
                pass


def run_sync(coro):
    """Run a coroutine from sync code, also inside Jupyter/Kaggle (which already has a running loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def run_adk(agent: BaseAgent, text: str, state: dict | None = None) -> tuple[str, dict, list[Event]]:
    return run_sync(run_adk_async(agent, text, state))


def events_by_author(events: list[Event]) -> dict[str, str]:
    """Last text each agent produced (ParallelAgent / RemoteA2aAgent outputs)."""
    out: dict[str, str] = {}
    for ev in events:
        if ev.content and ev.content.parts and not ev.partial:
            t = "".join(p.text or "" for p in ev.content.parts if getattr(p, "text", None))
            if t:
                out[ev.author] = t
    return out
