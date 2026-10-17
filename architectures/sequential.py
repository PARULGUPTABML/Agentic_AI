"""Variant 1: Sequential Coordination Architecture.

Agents run one after another in a linear pipeline, strictly following the Orchestrator's plan:
Orchestrator -> Assigned Retrieval Agents in order -> Verification -> (re-plan if insufficient) -> Reviewer.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agents.blackboard import Blackboard, BlackboardStatus
from architectures.base import (
    Architecture,
    StubRetrievalAgent,
    StubVerificationAgent,
)


class SequentialArchitecture(Architecture):
    name = "sequential"

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
            # 1. Orchestrator
            c_start = time.time()
            if plan is None:
                plan = self.orchestrator.plan(question, run_id, round_num=round_num, blackboard=blackboard)
            else:
                feedback = "; ".join(v_res.get("gaps", ["Need more evidence"]))
                plan = self.orchestrator.replan(plan, feedback=feedback, blackboard=blackboard)
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append(f"orchestrator_r{round_num}")

            # 2. Sequential execution strictly adhering to the planned subtasks
            assigned_agent_names = [st.assigned_agent for st in plan.subtasks]
            for subtask in plan.subtasks:
                agent_name = subtask.assigned_agent
                agent = agents.get(agent_name)
                if agent:
                    agents_invoked.append(agent_name)
                    c_disp = time.time()
                    subquery = subtask.sub_query
                    coord_overhead_ms += (time.time() - c_disp) * 1000

                    if hasattr(agent, "run"):
                        try:
                            frags = agent.run(subquery, repo_slug=repo_slug)
                        except TypeError:
                            blackboard.question = subquery
                            frags = agent.run(blackboard)
                        if isinstance(frags, list):
                            c_post = time.time()
                            blackboard.post_evidence(agent_name, frags)
                            coord_overhead_ms += (time.time() - c_post) * 1000

            # 3. Verification Agent
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

        # 4. Reviewer Agent
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
