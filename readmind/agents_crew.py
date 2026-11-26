"""CrewAI agents.

    code_agent        Code Analysis Specialist   — MCP repo tools search_code / read_code
    discussion_agent  Issue & PR Specialist      — MCP repo tools search_discussions / get_linked
    reviewer_agent    Reviewer                   — no tools; Task(output_pydantic=FinalAnswer, guardrail=...)
    liaison_agent     History Liaison            — A2A client to the ADK History Agent (CrewAI -> ADK)

Tasks carry ``QUESTION: ...`` and the messaging channel's context; every specialist task
returns a ``SpecialistReport`` (``output_pydantic``) so the hand-off is typed.
"""
from __future__ import annotations

from crewai import Agent, Crew, Process, Task

from readmind import prompts
from readmind.guardrails import crew_final_guardrail
from readmind.llm import crew_llm
from readmind.mcp_servers import crew_mcp
from readmind.observability.callbacks import crew_step_callback, crew_task_callback, install_crewai_hooks
from readmind.schemas import FinalAnswer, SpecialistReport

ROLES = {
    "code": ("Code Analysis Specialist", "Describe what the current code looks like for the question.",
             ["search_code", "read_code"]),
    "discussion": ("Issue and PR Discussion Specialist", "Find the issues, PRs and reviews behind a change.",
                   ["search_discussions", "get_linked"]),
    "reviewer": ("Reviewer", "Write a grounded, fully cited final answer.", []),
    "liaison": ("History Liaison", "Obtain commit-history evidence from the remote ADK History Agent.", []),
}


def _agent(role: str, variant: str, **kw) -> Agent:
    install_crewai_hooks()
    title, goal, tools = ROLES[role]
    args = dict(role=title, goal=goal, backstory=prompts.instruction(role, variant), llm=crew_llm(),
                step_callback=crew_step_callback, max_iter=6, verbose=False, allow_delegation=False)
    if tools:
        args["mcps"] = [crew_mcp("repo", tools)]
    args.update(kw)
    return Agent(**args)


def code_agent(variant: str = "default") -> Agent:
    return _agent("code", variant)


def discussion_agent(variant: str = "default") -> Agent:
    return _agent("discussion", variant)


def reviewer_agent(variant: str = "default") -> Agent:
    return _agent("reviewer", variant)


def liaison_agent(card_url: str, variant: str = "default") -> Agent:
    from crewai.a2a import A2AClientConfig
    return _agent("liaison", variant, a2a=A2AClientConfig(endpoint=card_url, timeout=600, max_turns=3))


def specialist_task(agent: Agent, role: str, question: str, query: str, context: str) -> Task:
    return Task(
        description=(f"QUESTION: {question}\nYour sub-query: {query}\n\nContext shared with you:\n{context}\n\n"
                     f"Use your tools, then return a SpecialistReport JSON object with agent='{role}'."),
        expected_output="A SpecialistReport JSON object (agent, summary, findings, evidence, gaps).",
        agent=agent, output_pydantic=SpecialistReport, callback=crew_task_callback)


def reviewer_task(agent: Agent, question: str, context: str) -> Task:
    return Task(
        description=(f"QUESTION: {question}\n\nEvidence and specialist reports:\n{context}\n\n"
                     "Write the final answer as a FinalAnswer JSON object. Cite only ids that appear above."),
        expected_output="A FinalAnswer JSON object (question, answer, claims, evidence, confidence, gaps).",
        agent=agent, output_pydantic=FinalAnswer, guardrail=crew_final_guardrail, guardrail_max_retries=2,
        callback=crew_task_callback)


def manager_task(question: str) -> Task:
    return Task(
        description=(f"QUESTION: {question}\n\nDelegate to your coworkers: the History Liaison (commit history via "
                     "the remote ADK agent), the Code Analysis Specialist and the Issue and PR Discussion Specialist. "
                     "Then write the final answer as a FinalAnswer JSON object citing only ids your coworkers found."),
        expected_output="A FinalAnswer JSON object (question, answer, claims, evidence, confidence, gaps).",
        output_pydantic=FinalAnswer, guardrail=crew_final_guardrail, guardrail_max_retries=1,
        callback=crew_task_callback)


def run_crew(agents: list[Agent], tasks: list[Task], process: str = "sequential", manager_llm=None):
    install_crewai_hooks()
    kw = dict(agents=agents, tasks=tasks, verbose=False, step_callback=crew_step_callback,
              task_callback=crew_task_callback)
    if process == "hierarchical":
        kw.update(process=Process.hierarchical, manager_llm=manager_llm or crew_llm())
    else:
        kw.update(process=Process.sequential)
    crew = Crew(**kw)
    return crew.kickoff(), crew
