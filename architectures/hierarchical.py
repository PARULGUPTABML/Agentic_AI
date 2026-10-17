"""Variant 2: Hierarchical Coordination Architecture.

The Orchestrator acts as the central controller:
It delegates subtasks to retrieval agents in parallel, collects results,
submits to verification, decides re-planning rounds, and delegates final synthesis to Reviewer.
"""
from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Dict, List, Optional

from agents.blackboard import Blackboard, BlackboardStatus
from architectures.base import (
    Architecture,
    StubRetrievalAgent,
    StubVerificationAgent,
)


class HierarchicalArchitecture(Architecture):
    name = "hierarchical"

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

        agents = retrieval_agents or {
            "code_analysis": StubRetrievalAgent("code_analysis"),
            "history_rag": StubRetrievalAgent("history_rag"),
            "issue_pr": StubRetrievalAgent("issue_pr")
        }
        verifier = verification_agent or StubVerificationAgent()

        round_num = 1
        incomplete = False
        plan = None
        v_res = {}

        while round_num <= self.max_rounds:
            # 1. Orchestrator Controller creates/updates plan
            c_start = time.time()
            if plan is None:
                plan = self.orchestrator.plan(question, run_id, round_num=round_num, blackboard=blackboard)
            else:
                feedback = "; ".join(v_res.get("gaps", ["Need more evidence"]))
                plan = self.orchestrator.replan(plan, feedback=feedback, blackboard=blackboard)
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append(f"orchestrator_controller_r{round_num}")

            # 2. Parallel dispatch of planned subtasks
            tasks_to_run = []
            c_prep = time.time()
            for subtask in plan.subtasks:
                agent = agents.get(subtask.assigned_agent)
                if agent:
                    tasks_to_run.append((subtask.assigned_agent, agent, subtask.sub_query))
            coord_overhead_ms += (time.time() - c_prep) * 1000

            def _invoke_worker(agent_tuple):
                ag_name, ag_instance, q = agent_tuple
                if hasattr(ag_instance, "run"):
                    try:
                        return ag_name, ag_instance.run(q, repo_slug=repo_slug)
                    except TypeError:
                        return ag_name, ag_instance.run(blackboard)
                return ag_name, []

            # Execute workers in parallel (worker time is agent work, not coordination overhead)
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(_invoke_worker, tasks_to_run))

            c_gather = time.time()
            for ag_name, frags in results:
                agents_invoked.append(ag_name)
                if isinstance(frags, list):
                    blackboard.post_evidence(ag_name, frags)
            coord_overhead_ms += (time.time() - c_gather) * 1000

            # 3. Manager queries Verification
            c_v = time.time()
            blackboard.set_status(BlackboardStatus.VERIFYING)
            coord_overhead_ms += (time.time() - c_v) * 1000
            agents_invoked.append("verification")
            v_res = verifier.run(blackboard)

            if v_res.get("sufficient", False):
                break
            else:
                if round_num < self.max_rounds:
                    round_num += 1
                    c_r = time.time()
                    blackboard.set_status(BlackboardStatus.REGATHERING)
                    coord_overhead_ms += (time.time() - c_r) * 1000
                else:
                    incomplete = True
                    break

        # 4. Manager delegates final synthesis to Reviewer
        c_start = time.time()
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
        coord_overhead_ms += (time.time() - c_start) * 1000

        reviewer_res = self.reviewer.review(
            {
                "run_id": run_id,
                "question": question,
                "evidence": evidence_dicts,
                "incomplete": incomplete,
                "gap_notes": v_res.get("gaps", [])
            },
            blackboard=blackboard
        )
        agents_invoked.append("reviewer")

        return self._format_run_output(
            run_id=run_id,
            question=question,
            blackboard=blackboard,
            reviewer_res=reviewer_res,
            start_time=start_time,
            coordination_time_ms=coord_overhead_ms,
            agents_invoked=agents_invoked,
            rounds=round_num
        )
