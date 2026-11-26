"""Agentic design patterns (requirement 4), each built from ADK + CrewAI agents.

    pattern        design pattern                         frameworks / mechanics
    -------------  -------------------------------------  ------------------------------------------------------
    single_react   ReAct, single agent (baseline)         one ADK LlmAgent with every MCP tool
    sequential     prompt chaining / pipeline             ADK planner -> ADK history -> CrewAI code -> CrewAI
                                                          discussion -> ADK verifier (-> re-gather) -> CrewAI reviewer
    parallel       parallelization (fan-out / fan-in)     ADK ParallelAgent[ADK history, RemoteA2aAgent(CrewAI code),
                                                          RemoteA2aAgent(CrewAI discussion)] -> CrewAI reviewer
    reflection     reflection / evaluator-optimizer       parallel gather -> CrewAI draft -> ADK LoopAgent[critic,
                                                          StopIfSufficient, refiner] -> CrewAI revised answer
    hierarchical   orchestrator-workers (delegation)      CrewAI hierarchical crew; manager delegates to CrewAI code
                                                          + discussion agents and to the ADK history agent over A2A

``messaging`` (blackboard | p2p | broadcast) decides how specialist outputs reach the next agent
in sequential / parallel / reflection; hierarchical uses manager-mediated delegation and
single_react has no inter-agent messaging.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from readmind import agents_adk as ADK
from readmind.messaging import Channel
from readmind.observability.monitor import RunMonitor
from readmind.schemas import (EvidenceItem, FinalAnswer, Plan, SpecialistReport, SubTask, VerificationVerdict,
                              parse_model)

PARTICIPANTS = ["history", "code", "discussion", "reviewer"]


@dataclass
class RunCtx:
    question: str
    variant: str
    messaging: str
    channel: Channel | None
    mon: RunMonitor
    plan: Plan | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def q(self) -> str:
        return f"QUESTION: {self.question}"


# --------------------------------------------------------------------------- #
#  Grounding helpers                                                           #
# --------------------------------------------------------------------------- #
def _ground(items: list[EvidenceItem], mon: RunMonitor) -> list[EvidenceItem]:
    """Keep only evidence that a tool actually returned in this run; enrich it from the provenance ledger."""
    prov = mon.retrieved
    out, seen = [], set()
    for e in items:
        match = prov.get(e.id) or next((p for rid, p in prov.items() if e.id.startswith("commit:") and
                                        rid.startswith("commit:") and (rid[7:].startswith(e.id[7:]) or
                                                                       e.id[7:].startswith(rid[7:]))), None)
        if not match or e.id in seen:
            continue
        seen.add(match["evidence_id"])
        out.append(EvidenceItem(id=match["evidence_id"], type=match.get("type") or e.type, title=e.title or match["title"],
                                snippet=e.snippet, source=f"{match['server']}/{match['tool']}",
                                url=e.url or match.get("url", ""), date=e.date or match.get("date", "")))
    return out


def _from_provenance(mon: RunMonitor, agents: tuple[str, ...], limit: int = 8) -> list[EvidenceItem]:
    items = [p for p in mon.retrieved.values() if p["agent"] in agents]
    return [EvidenceItem(id=p["evidence_id"], type=p["type"], title=p["title"], source=f"{p['server']}/{p['tool']}",
                         url=p["url"], date=p["date"]) for p in items[:limit]]


def to_report(raw, role: str, mon: RunMonitor, agent_names: tuple[str, ...]) -> SpecialistReport:
    rep = parse_model(SpecialistReport, raw)
    if rep is None:
        text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        rep = SpecialistReport(agent=role, summary=(text or "")[:600], gaps=["report was not valid JSON"])
    rep.agent = role
    grounded = _ground(rep.evidence, mon)
    dropped = len(rep.evidence) - len(grounded)
    if not grounded:
        grounded = _from_provenance(mon, agent_names)
    if dropped:
        mon.event("system", role, "grounding", {"brief": f"dropped {dropped} evidence item(s) no tool returned"})
    rep.evidence = grounded
    return rep


def extractive_answer(question: str, evidence: list[EvidenceItem], why: str) -> FinalAnswer:
    if not evidence:
        return FinalAnswer(question=question, answer="The available evidence does not explain this.",
                           confidence="low", gaps=[why])
    lead = evidence[0]
    return FinalAnswer(question=question, answer=f"{lead.title} [{lead.id}].",
                       claims=[{"text": e.title or e.id, "kind": "fact", "evidence_ids": [e.id]} for e in evidence[:4]],
                       evidence=evidence[:6], confidence="low", gaps=[why])


# --------------------------------------------------------------------------- #
#  Agent steps                                                                 #
# --------------------------------------------------------------------------- #
def do_plan(ctx: RunCtx) -> Plan:
    text, state, _ = ADK.run_adk(ADK.planner_agent(ctx.variant), ctx.q)
    plan = parse_model(Plan, state.get("plan") or text)
    if plan is None or not plan.subtasks:
        plan = Plan(subtasks=[SubTask(agent=a, query=ctx.question) for a in ("history", "code", "discussion")])
    ctx.plan = plan
    ctx.mon.event("adk", "planner", "plan", {"intent": plan.intent, "subtasks": [s.model_dump() for s in plan.subtasks],
                                             "brief": f"intent={plan.intent}, {len(plan.subtasks)} subtasks"})
    return plan


def _query(ctx: RunCtx, role: str) -> str:
    if ctx.plan:
        for st in ctx.plan.subtasks:
            if st.agent == role:
                return st.query
    return ctx.question


def do_history(ctx: RunCtx, query: str | None = None, extra: str = "") -> SpecialistReport:
    context = ctx.channel.context_for("history") if ctx.channel else ""
    msg = f"{ctx.q}\nSub-query: {query or _query(ctx, 'history')}\n{extra}\nContext:\n{context}"
    text, state, _ = ADK.run_adk(ADK.history_agent(ctx.variant), msg)
    return to_report(state.get("history_report") or text, "history", ctx.mon, ("history_agent",))


def do_crew_specialist(ctx: RunCtx, role: str) -> SpecialistReport:
    from readmind.agents_crew import code_agent, discussion_agent, run_crew, specialist_task
    agent = code_agent(ctx.variant) if role == "code" else discussion_agent(ctx.variant)
    context = ctx.channel.context_for(role) if ctx.channel else ""
    out, _ = run_crew([agent], [specialist_task(agent, role, ctx.question, _query(ctx, role), context)])
    return to_report(out.pydantic or out.raw, role, ctx.mon, (role,))


def do_verify(ctx: RunCtx) -> VerificationVerdict:
    state = ctx.channel.as_state("verifier") if ctx.channel else {}
    text, st, _ = ADK.run_adk(ADK.verifier_agent(ctx.variant), ctx.q, state)
    v = parse_model(VerificationVerdict, st.get("verdict") or text) or VerificationVerdict(
        sufficient=True, confidence=0.0, gaps=["verifier output unparseable"])
    ctx.mon.event("adk", "verifier", "verdict", {**v.model_dump(), "brief": f"sufficient={v.sufficient} "
                                                                           f"conf={v.confidence}"})
    return v


def do_review(ctx: RunCtx, draft: FinalAnswer | None = None) -> FinalAnswer:
    from readmind.agents_crew import reviewer_agent, reviewer_task, run_crew
    context = ctx.channel.context_for("reviewer") if ctx.channel else ""
    if draft is not None:
        context += "\n\nYOUR PREVIOUS DRAFT (revise it with the new evidence):\n" + draft.model_dump_json()
    agent = reviewer_agent(ctx.variant)
    try:
        out, _ = run_crew([agent], [reviewer_task(agent, ctx.question, context)])
        fa = parse_model(FinalAnswer, out.pydantic or out.raw)
    except Exception as e:
        ctx.mon.error("crewai", "reviewer", f"reviewer failed: {e}")
        fa = None
    if fa is None:
        ev = ctx.channel.all_evidence() if ctx.channel else []
        fa = extractive_answer(ctx.question, ev, "reviewer output unavailable — extractive fallback")
        ctx.notes.append("reviewer_fallback")
    fa.question = ctx.question
    return fa


def _publish(ctx: RunCtx, role: str, rep: SpecialistReport) -> None:
    if ctx.channel:
        ctx.channel.publish(role, rep)


# --------------------------------------------------------------------------- #
#  Patterns                                                                    #
# --------------------------------------------------------------------------- #
def single_react(ctx: RunCtx) -> FinalAnswer:
    text, state, _ = ADK.run_adk(ADK.single_agent(ctx.variant), ctx.q)
    fa = parse_model(FinalAnswer, state.get("final_json") or text)
    if fa is None:
        fa = extractive_answer(ctx.question, _from_provenance(ctx.mon, ("single_react_agent",)),
                               "agent did not return FinalAnswer JSON — extractive fallback")
        ctx.notes.append("single_fallback")
    fa.question = ctx.question
    return fa


def sequential(ctx: RunCtx) -> FinalAnswer:
    do_plan(ctx)
    _publish(ctx, "history", do_history(ctx))
    _publish(ctx, "code", do_crew_specialist(ctx, "code"))
    _publish(ctx, "discussion", do_crew_specialist(ctx, "discussion"))
    verdict = do_verify(ctx)
    if not verdict.sufficient and verdict.follow_up_queries:
        ctx.mon.counters["retrieval_rounds"] += 1
        rep = do_history(ctx, query=" ; ".join(verdict.follow_up_queries[:3]),
                         extra="Gaps to close: " + "; ".join(verdict.gaps[:3]))
        _publish(ctx, "history", rep)
    return do_review(ctx)


def _parallel_gather(ctx: RunCtx) -> None:
    from readmind.a2a_bridge import remote_crew_agent
    par = ADK.parallel_gather([ADK.history_agent(ctx.variant), remote_crew_agent("code", ctx.variant),
                               remote_crew_agent("discussion", ctx.variant)])
    text, state, events = ADK.run_adk(par, f"{ctx.q}\nSub-query: {_query(ctx, 'history')}")
    outs = ADK.events_by_author(events)
    _publish(ctx, "history", to_report(state.get("history_report") or outs.get("history_agent", ""), "history",
                                       ctx.mon, ("history_agent",)))
    _publish(ctx, "code", to_report(outs.get("crew_code", ""), "code", ctx.mon, ("code",)))
    _publish(ctx, "discussion", to_report(outs.get("crew_discussion", ""), "discussion", ctx.mon, ("discussion",)))


def parallel(ctx: RunCtx) -> FinalAnswer:
    do_plan(ctx)
    _parallel_gather(ctx)
    return do_review(ctx)


def reflection(ctx: RunCtx) -> FinalAnswer:
    do_plan(ctx)
    _parallel_gather(ctx)
    draft = do_review(ctx)
    state = ctx.channel.as_state("verifier") if ctx.channel else {}
    state["shared_context"] = state.get("shared_context", "") + "\n\nDRAFT ANSWER:\n" + draft.model_dump_json()
    text, st, events = ADK.run_adk(ADK.reflection_loop(ctx.variant), ctx.q, state)
    verdict = parse_model(VerificationVerdict, st.get("verdict"))
    ctx.mon.event("adk", "reflection_loop", "verdict", {"brief": f"loop verdict sufficient="
                                                                 f"{getattr(verdict, 'sufficient', None)}"})
    refined = st.get("history_refined")
    if refined:
        ctx.mon.counters["retrieval_rounds"] += 1
        _publish(ctx, "history", to_report(refined, "history", ctx.mon, ("history_refiner",)))
        return do_review(ctx, draft=draft)
    return draft


def hierarchical(ctx: RunCtx) -> FinalAnswer:
    from readmind.a2a_bridge import serve_adk_history
    from readmind.agents_crew import code_agent, discussion_agent, liaison_agent, manager_task, run_crew
    card = serve_adk_history(ctx.variant)
    agents = [liaison_agent(card, ctx.variant), code_agent(ctx.variant), discussion_agent(ctx.variant)]
    try:
        out, _ = run_crew(agents, [manager_task(ctx.question)], process="hierarchical")
        fa = parse_model(FinalAnswer, out.pydantic or out.raw)
    except Exception as e:
        ctx.mon.error("crewai", "manager", f"hierarchical crew failed: {e}")
        fa = None
    if fa is None:
        fa = extractive_answer(ctx.question, _from_provenance(ctx.mon, ("history_agent", "code", "discussion")),
                               "manager output unavailable — extractive fallback")
        ctx.notes.append("manager_fallback")
    fa.question = ctx.question
    return fa


PATTERNS: dict[str, Callable[[RunCtx], FinalAnswer]] = {
    "single_react": single_react, "sequential": sequential, "parallel": parallel,
    "reflection": reflection, "hierarchical": hierarchical,
}
PATTERN_INFO = {
    "single_react": {"design_pattern": "ReAct (single agent)", "frameworks": "ADK", "messaging": "none"},
    "sequential": {"design_pattern": "Prompt chaining / pipeline + verification gate", "frameworks": "ADK + CrewAI",
                   "messaging": "channel"},
    "parallel": {"design_pattern": "Parallelization (fan-out / fan-in)", "frameworks": "ADK ParallelAgent + A2A -> CrewAI",
                 "messaging": "channel"},
    "reflection": {"design_pattern": "Reflection (evaluator-optimizer loop)", "frameworks": "ADK LoopAgent + CrewAI",
                   "messaging": "channel"},
    "hierarchical": {"design_pattern": "Orchestrator-workers (hierarchical delegation)",
                     "frameworks": "CrewAI hierarchical + A2A -> ADK", "messaging": "manager"},
}
