"""Variant 3: Event-Driven Coordination Architecture.

Agents react asynchronously to events published to a shared event bus:
'question_received' -> Orchestrator publishes 'plan_ready'
Retrieval agents subscribe to 'plan_ready' -> publish 'evidence_posted'
Verification subscribes to 'evidence_posted' -> publishes 'evidence_approved' or 'evidence_insufficient'
Reviewer subscribes to 'evidence_approved' -> publishes 'answer_complete'.
"""
from __future__ import annotations

import collections
import queue
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from agents.blackboard import Blackboard, BlackboardStatus
from parul_aggarwal.architectures.base import (
    Architecture,
    StubRetrievalAgent,
    StubVerificationAgent,
)


@dataclass
class Event:
    topic: str
    payload: Any
    timestamp: float


class InProcessEventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = collections.defaultdict(list)
        self._queue: queue.Queue = queue.Queue()

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> None:
        self._subscribers[topic].append(handler)

    def publish(self, topic: str, payload: Any) -> None:
        event = Event(topic=topic, payload=payload, timestamp=time.time())
        self._queue.put(event)

    def process_next(self, timeout: float = 1.0) -> Optional[Event]:
        try:
            event = self._queue.get(timeout=timeout)
            for handler in self._subscribers.get(event.topic, []):
                handler(event)
            self._queue.task_done()
            return event
        except queue.Empty:
            return None


class EventDrivenArchitecture(Architecture):
    name = "event_driven"

    def run(
        self,
        question: str,
        run_id: str,
        repo_slug: str = "seL4__seL4",
        retrieval_agents: Optional[Dict[str, Any]] = None,
        verification_agent: Optional[Any] = None
    ) -> Dict[str, Any]:
        start_time = time.time()
        coord_overhead_ms = 0.0
        agents_invoked: List[str] = []

        blackboard = Blackboard(question=question, repo_slug=repo_slug, run_id=run_id)
        bus = InProcessEventBus()

        agents = retrieval_agents or {
            "code_analysis": StubRetrievalAgent("code_analysis"),
            "history_rag": StubRetrievalAgent("history_rag"),
            "issue_pr": StubRetrievalAgent("issue_pr")
        }
        verifier = verification_agent or StubVerificationAgent()

        state = {
            "round": 1,
            "incomplete": False,
            "done": False,
            "reviewer_res": {},
            "v_res": {}
        }

        # Event Handlers
        def on_question(event: Event):
            c_start = time.time()
            plan = self.orchestrator.plan(question, run_id, round_num=state["round"], blackboard=blackboard)
            nonlocal coord_overhead_ms
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append(f"orchestrator_event_r{state['round']}")
            bus.publish("plan_ready", plan)

        def on_plan_ready(event: Event):
            plan = event.payload
            for subtask in plan.subtasks:
                agent = agents.get(subtask.assigned_agent)
                if agent:
                    agents_invoked.append(subtask.assigned_agent)
                    if hasattr(agent, "run"):
                        try:
                            frags = agent.run(subtask.sub_query, repo_slug=repo_slug)
                        except TypeError:
                            frags = agent.run(blackboard)
                        if isinstance(frags, list):
                            blackboard.post_evidence(subtask.assigned_agent, frags)
            bus.publish("evidence_posted", {"count": blackboard.evidence_count()})

        def on_evidence_posted(event: Event):
            blackboard.set_status(BlackboardStatus.VERIFYING)
            agents_invoked.append("verification")
            v_res = verifier.run(blackboard)
            state["v_res"] = v_res
            if v_res.get("sufficient", False):
                bus.publish("evidence_approved", v_res)
            else:
                if state["round"] < self.max_rounds:
                    state["round"] += 1
                    bus.publish("evidence_insufficient", v_res)
                else:
                    state["incomplete"] = True
                    bus.publish("evidence_approved", v_res)

        def on_evidence_insufficient(event: Event):
            blackboard.set_status(BlackboardStatus.REGATHERING)
            feedback = "; ".join(event.payload.get("gaps", []))
            c_start = time.time()
            plan = self.orchestrator.plan(
                question, run_id, round_num=state["round"], feedback=feedback, blackboard=blackboard
            )
            nonlocal coord_overhead_ms
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append(f"orchestrator_event_r{state['round']}")
            bus.publish("plan_ready", plan)

        def on_evidence_approved(event: Event):
            blackboard.set_status(BlackboardStatus.SYNTHESIZING)
            evidence_dicts = [
                {
                    "id": e.id,
                    "type": e.evidence_type,
                    "text": e.content,
                    "confidence": e.confidence,
                    "title": e.title,
                    "source_agent": e.source_agent
                }
                for e in blackboard.get_evidence()
            ]
            c_start = time.time()
            res = self.reviewer.review(
                {
                    "run_id": run_id,
                    "question": question,
                    "evidence": evidence_dicts,
                    "incomplete": state["incomplete"],
                    "gap_notes": state["v_res"].get("gaps", [])
                },
                blackboard=blackboard
            )
            nonlocal coord_overhead_ms
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append("reviewer")
            state["reviewer_res"] = res
            state["done"] = True
            bus.publish("answer_complete", res)

        # Subscriptions
        bus.subscribe("question_received", on_question)
        bus.subscribe("plan_ready", on_plan_ready)
        bus.subscribe("evidence_posted", on_evidence_posted)
        bus.subscribe("evidence_insufficient", on_evidence_insufficient)
        bus.subscribe("evidence_approved", on_evidence_approved)

        # Kickoff
        bus.publish("question_received", question)

        # Event processing loop with timeout guard
        loop_start = time.time()
        while not state["done"]:
            if (time.time() - loop_start) > self.timeout_seconds:
                state["incomplete"] = True
                break
            ev = bus.process_next(timeout=0.1)
            coord_overhead_ms += 0.5  # Event bus queue handling overhead

        return self._format_run_output(
            run_id=run_id,
            question=question,
            blackboard=blackboard,
            reviewer_res=state["reviewer_res"],
            start_time=start_time,
            coordination_time_ms=coord_overhead_ms,
            agents_invoked=agents_invoked,
            rounds=state["round"]
        )
