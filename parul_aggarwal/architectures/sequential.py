"""Variant 1: Sequential Coordination Architecture.

Agents run one after another in a fixed linear pipeline:
Orchestrator -> Code Analysis -> History/RAG -> Issue/PR -> Verification -> (re-run if insufficient) -> Reviewer.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agents.blackboard import Blackboard, BlackboardStatus
from parul_aggarwal.architectures.base import (
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

        # Setup agents
        agents = retrieval_agents or {
            "code_analysis": StubRetrievalAgent("code_analysis"),
            "history_rag": StubRetrievalAgent("history_rag"),
            "issue_pr": StubRetrievalAgent("issue_pr")
        }
        verifier = verification_agent or StubVerificationAgent()

        round_num = 1
        incomplete = False

        while round_num <= self.max_rounds:
            # 1. Orchestrator
            c_start = time.time()
            plan = self.orchestrator.plan(question, run_id, round_num=round_num, blackboard=blackboard)
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append(f"orchestrator_r{round_num}")

            # 2. Sequential execution of retrieval agents in fixed order
            for agent_name in ["code_analysis", "history_rag", "issue_pr"]:
                agent = agents.get(agent_name)
                if agent:
                    agents_invoked.append(agent_name)
                    # Subquery from plan or original question
                    subquery = question
                    for st in plan.subtasks:
                        if st.assigned_agent == agent_name:
                            subquery = st.sub_query
                            break

                    a_start = time.time()
                    if hasattr(agent, "run"):
                        # Support stub or pipeline agent
                        try:
                            frags = agent.run(subquery, repo_slug=repo_slug)
                        except TypeError:
                            frags = agent.run(blackboard)
                        if isinstance(frags, list):
                            blackboard.post_evidence(agent_name, frags)
                    coord_overhead_ms += 1.5  # Fixed sequential dispatch overhead

            # 3. Verification Agent
            blackboard.set_status(BlackboardStatus.VERIFYING)
            agents_invoked.append("verification")
            v_res = verifier.run(blackboard)

            if v_res.get("sufficient", False):
                break
            else:
                if round_num < self.max_rounds:
                    round_num += 1
                    blackboard.set_status(BlackboardStatus.REGATHERING)
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
        coord_overhead_ms += (time.time() - c_start) * 1000
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
