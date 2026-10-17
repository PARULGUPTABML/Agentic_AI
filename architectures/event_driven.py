"""Variant 3: Event-Driven Coordination Architecture.

Agents react asynchronously to events published to a shared event bus:
'question_received' -> Orchestrator publishes 'plan_ready'
Retrieval agents subscribe to 'plan_ready' -> dispatched in parallel -> publish 'evidence_posted'
Verification subscribes to 'evidence_posted' -> publishes 'evidence_approved' or 'evidence_insufficient'
Reviewer subscribes to 'evidence_approved' -> publishes 'answer_complete'.
"""
from __future__ import annotations

import collections
import concurrent.futures
import queue
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from agents.blackboard import Blackboard, BlackboardStatus
from architectures.base import (
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

    def publish(self, topic: str, payload: Any) -> float:
        """Publish event to queue and return enqueue duration in ms."""
        t0 = time.time()
        event = Event(topic=topic, payload=payload, timestamp=t0)
        self._queue.put(event)
        return (time.time() - t0) * 1000

    def process_next(self, timeout: float = 0.5) -> Tuple[Optional[Event], float]:
        """Process next event from queue.

        Measures only event dequeuing and dispatch lookup overhead in ms.
        Agent handler execution time is NOT counted towards dispatch overhead.
        """
        try:
            t0 = time.time()
            event = self._queue.get(timeout=timeout)
            handlers = list(self._subscribers.get(event.topic, []))
            dispatch_overhead_ms = (time.time() - t0) * 1000
            for handler in handlers:
                handler(event)
            self._queue.task_done()
            return event, dispatch_overhead_ms
        except queue.Empty:
            return None, 0.0


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
            "v_res": {},
            "current_plan": None
        }

        # Event Handlers
        def on_question(event: Event):
            nonlocal coord_overhead_ms
            c_start = time.time()
            plan = self.orchestrator.plan(question, run_id, round_num=state["round"], blackboard=blackboard)
            state["current_plan"] = plan
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append(f"orchestrator_event_r{state['round']}")
            coord_overhead_ms += bus.publish("plan_ready", plan)

        def on_plan_ready(event: Event):
            nonlocal coord_overhead_ms
            plan = event.payload
            tasks = []
            c_prep = time.time()
            for subtask in plan.subtasks:
                agent = agents.get(subtask.assigned_agent)
                if agent:
                    tasks.append((subtask.assigned_agent, agent, subtask.sub_query))
            coord_overhead_ms += (time.time() - c_prep) * 1000

            def _run_worker(t):
                ag_name, ag_inst, sub_q = t
                if hasattr(ag_inst, "run"):
                    try:
                        return ag_name, ag_inst.run(sub_q, repo_slug=repo_slug)
                    except TypeError:
                        blackboard.question = sub_q
                        return ag_name, ag_inst.run(blackboard)
                return ag_name, []

            # Execute retrieval workers concurrently without counting worker runtime as coordination
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
                f_map = {executor.submit(_run_worker, t): t[0] for t in tasks}
                for f in concurrent.futures.as_completed(f_map):
                    ag_name = f_map[f]
                    try:
                        _, frags = f.result()
                        if isinstance(frags, list) and frags:
                            c_post = time.time()
                            blackboard.post_evidence(ag_name, frags)
                            coord_overhead_ms += (time.time() - c_post) * 1000
                    except Exception:
                        pass
                    agents_invoked.append(ag_name)

            coord_overhead_ms += bus.publish("evidence_posted", state["current_plan"])

        def on_evidence_posted(event: Event):
            nonlocal coord_overhead_ms
            c_v = time.time()
            blackboard.set_status(BlackboardStatus.VERIFYING)
            coord_overhead_ms += (time.time() - c_v) * 1000

            agents_invoked.append("verification")
            v_res = verifier.run(blackboard)
            state["v_res"] = v_res

            if v_res.get("sufficient", False):
                coord_overhead_ms += bus.publish("evidence_approved", v_res)
            else:
                if state["round"] < self.max_rounds:
                    state["round"] += 1
                    c_regather = time.time()
                    blackboard.set_status(BlackboardStatus.REGATHERING)
                    coord_overhead_ms += (time.time() - c_regather) * 1000
                    coord_overhead_ms += bus.publish("evidence_insufficient", v_res)
                else:
                    state["incomplete"] = True
                    coord_overhead_ms += bus.publish("evidence_approved", v_res)

        def on_evidence_insufficient(event: Event):
            nonlocal coord_overhead_ms
            v_res = event.payload
            feedback = "; ".join(v_res.get("gaps", ["Need more evidence"]))
            c_replan = time.time()
            re_plan = self.orchestrator.replan(
                state["current_plan"],
                feedback=feedback,
                blackboard=blackboard
            )
            state["current_plan"] = re_plan
            coord_overhead_ms += (time.time() - c_replan) * 1000
            agents_invoked.append(f"orchestrator_event_r{state['round']}")
            coord_overhead_ms += bus.publish("plan_ready", re_plan)

        def on_evidence_approved(event: Event):
            nonlocal coord_overhead_ms
            c_rev = time.time()
            evidence_dicts = [
                {
                    "id": e.id,
                    "text": e.content,
                    "confidence": e.confidence,
                    "title": e.title,
                    "source_agent": e.source_agent
                }
                for e in blackboard.get_evidence()
            ]
            coord_overhead_ms += (time.time() - c_rev) * 1000

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
            agents_invoked.append("reviewer")
            state["reviewer_res"] = res
            state["done"] = True
            coord_overhead_ms += bus.publish("answer_complete", res)

        # Subscriptions
        bus.subscribe("question_received", on_question)
        bus.subscribe("plan_ready", on_plan_ready)
        bus.subscribe("evidence_posted", on_evidence_posted)
        bus.subscribe("evidence_insufficient", on_evidence_insufficient)
        bus.subscribe("evidence_approved", on_evidence_approved)

        # Kickoff
        coord_overhead_ms += bus.publish("question_received", question)

        # Event processing loop with timeout guard
        loop_start = time.time()
        while not state["done"]:
            if (time.time() - loop_start) > self.timeout_seconds:
                state["incomplete"] = True
                break
            ev, dispatch_overhead = bus.process_next(timeout=0.05)
            coord_overhead_ms += dispatch_overhead

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
